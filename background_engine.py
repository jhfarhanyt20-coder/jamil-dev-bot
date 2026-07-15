"""
background_engine.py
---------------------
Runs a persistent Quotex connection + scan loop in a daemon thread so the
Streamlit UI can refresh freely without reconnecting on every rerun.

Usage
-----
    from background_engine import engine

    engine.start(credentials)   # start scanning
    engine.stop()               # disconnect
    engine.status               # "disconnected" | "connecting" | "connected" | "error"
    engine.signals              # list of latest signal dicts (one per pair)
    engine.error                # last error string or None
"""

import asyncio
import logging
import os
import sys
import tempfile
import threading
import time
from typing import Optional

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pairs import all_pairs
from quotexapi.stable_api import Quotex
from signal_logic import calculate_indicators, get_signal_simple, calculate_htf_trend

logger = logging.getLogger(__name__)

USER_AGENT          = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
CANDLE_OFFSET       = 3600 * 3
POLL_INTERVAL       = 20          # seconds between full pair scans
KEEPALIVE_INTERVAL  = 30          # seconds between keepalive pings
MAX_KA_FAILURES     = 5           # give up after this many consecutive keepalive failures


def _runtime_dir() -> str:
    d = os.path.join(tempfile.gettempdir(), "quotex-streamlit-engine")
    os.makedirs(d, exist_ok=True)
    return d


# ─── Signal Engine (runs inside a background thread) ─────────────────────────

class _Engine:
    def __init__(self):
        self._lock      = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_evt  = threading.Event()

        self.status: str           = "disconnected"
        self.error:  Optional[str] = None
        self.signals: list         = []
        self.last_scan_time: Optional[float] = None
        self._credentials: dict    = {}

    # ── public API ────────────────────────────────────────────────────────────

    def start(self, credentials: dict) -> None:
        """Start (or restart) the engine with the given credentials."""
        self.stop()
        self._credentials = credentials
        self._stop_evt.clear()
        self._set_status("connecting")
        self._thread = threading.Thread(target=self._run, daemon=True, name="qx-engine")
        self._thread.start()

    def stop(self) -> None:
        """Signal the engine thread to stop and wait for it."""
        self._stop_evt.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._thread = None
        self._set_status("disconnected")

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── internal ──────────────────────────────────────────────────────────────

    def _set_status(self, status: str, error: str | None = None) -> None:
        with self._lock:
            self.status = status
            self.error  = error

    def _set_signals(self, sigs: list) -> None:
        with self._lock:
            self.signals        = sigs
            self.last_scan_time = time.time()

    def _run(self) -> None:
        """Entry point for the background thread — creates its own event loop."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._async_main())
        except Exception as exc:
            self._set_status("error", str(exc))
        finally:
            loop.close()

    async def _async_main(self) -> None:
        creds  = self._credentials
        email  = creds.get("email", "user@example.com")
        passwd = creds.get("password", "password")
        cookie = creds.get("cookies", "")
        token  = creds.get("token", "")

        if not cookie or not token:
            self._set_status("error", "Missing QX_COOKIES or QX_TOKEN.")
            return

        orig = os.getcwd()
        os.chdir(_runtime_dir())
        try:
            client = Quotex(email=email, password=passwd, lang="en", user_agent=USER_AGENT)
            client.set_session(user_agent=USER_AGENT, cookies=cookie, ssid=token)
            ok, reason = await client.connect()
            if not ok:
                self._set_status("error", str(reason) or "Connection failed")
                return

            self._set_status("connected")

            # Run scan + keepalive concurrently until stop event
            scan_task = asyncio.create_task(self._scan_loop(client))
            ka_task   = asyncio.create_task(self._keepalive_loop(client))

            done, pending = await asyncio.wait(
                [scan_task, ka_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
            for t in done:
                exc = t.exception()
                if exc:
                    self._set_status("error", str(exc))

        finally:
            os.chdir(orig)
            try:
                await client.close()
            except Exception:
                pass

    async def _scan_loop(self, client: Quotex) -> None:
        pairs = all_pairs()
        while not self._stop_evt.is_set():
            results = []
            for p in pairs:
                if self._stop_evt.is_set():
                    return
                sig = await self._fetch_signal(
                    client, p["symbol"], p["displayName"], p["market"]
                )
                results.append(sig)
            self._set_signals(results)
            # Sleep in small chunks so stop_evt is checked promptly
            for _ in range(POLL_INTERVAL * 4):
                if self._stop_evt.is_set():
                    return
                await asyncio.sleep(0.25)

    async def _keepalive_loop(self, client: Quotex) -> None:
        failures = 0
        while not self._stop_evt.is_set():
            for _ in range(KEEPALIVE_INTERVAL * 4):
                if self._stop_evt.is_set():
                    return
                await asyncio.sleep(0.25)
            try:
                ok = await Quotex.check_connect()
                if not ok:
                    await client.connect()
                failures = 0
            except Exception:
                failures += 1
                if failures >= MAX_KA_FAILURES:
                    raise RuntimeError("Keepalive failed too many times — disconnecting.")

    async def _fetch_signal(
        self, client: Quotex, symbol: str, display_name: str, market: str, period: int = 60
    ) -> dict:
        try:
            candles = await client.get_candles(
                symbol, end_from_time=time.time(), offset=CANDLE_OFFSET, period=period
            )
            if not candles or len(candles) < 30:
                return self._empty(symbol, display_name, market)

            df = pd.DataFrame(candles)

            htf_trend = "neutral"
            try:
                htf_raw = await client.get_candles(
                    symbol, end_from_time=time.time(), offset=CANDLE_OFFSET,
                    period=max(period * 5, 300),
                )
                if htf_raw:
                    htf_trend = calculate_htf_trend(pd.DataFrame(htf_raw))
            except Exception:
                pass

            df     = calculate_indicators(df)
            sig, conf, reasons = get_signal_simple(df, htf_trend=htf_trend)
            direction = {"CALL": "call", "PUT": "put"}.get(sig, "neutral")
            last_close = df["Close"].iloc[-1]
            price = None if last_close != last_close else round(float(last_close), 5)

            return {
                "symbol":      symbol,
                "displayName": display_name,
                "market":      market,
                "direction":   direction,
                "confidence":  conf,
                "price":       price,
                "reasons":     reasons[-6:],
                "timestamp":   time.time(),
            }
        except Exception as exc:
            s = self._empty(symbol, display_name, market)
            s["error"] = str(exc)
            return s

    @staticmethod
    def _empty(symbol, display_name, market) -> dict:
        return {
            "symbol":      symbol,
            "displayName": display_name,
            "market":      market,
            "direction":   "neutral",
            "confidence":  0,
            "price":       None,
            "reasons":     [],
            "timestamp":   time.time(),
        }


# ─── Singleton ────────────────────────────────────────────────────────────────
# Streamlit re-imports modules on every rerun, but module-level globals persist
# for the lifetime of the server process, so this one instance is shared across
# all reruns and all browser sessions on the same Streamlit server.

engine = _Engine()
