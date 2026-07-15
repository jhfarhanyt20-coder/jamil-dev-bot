"""
Quotex Signal Desk — Streamlit Edition
---------------------------------------
Local:   streamlit run app.py
Cloud:   push to GitHub → share.streamlit.io
         Secrets: QX_EMAIL, QX_PASSWORD, QX_COOKIES, QX_TOKEN
"""

import os
import sys
import time
from datetime import datetime

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from background_engine import engine
from pairs import all_pairs
from qx_client import generate_signal_once
from signal_logic import get_next_candle_window

# ─── Page config ─────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Quotex Signal Desk",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Session state ────────────────────────────────────────────────────────────

for _k, _v in {
    "credentials": {},
    "signal_history": [],
    "last_generated": None,
    "engine_started": False,
}.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ─── Auto-load from Streamlit Cloud secrets ───────────────────────────────────

if not st.session_state.credentials:
    try:
        c = {
            "email":    st.secrets.get("QX_EMAIL", ""),
            "password": st.secrets.get("QX_PASSWORD", ""),
            "cookies":  st.secrets.get("QX_COOKIES", ""),
            "token":    st.secrets.get("QX_TOKEN", ""),
        }
        if c["cookies"] and c["token"]:
            st.session_state.credentials = c
    except Exception:
        pass

# ─── Auto-start engine if credentials exist and engine not running ────────────

def _maybe_start():
    creds = st.session_state.credentials
    if creds.get("cookies") and creds.get("token") and not engine.is_running():
        engine.start(creds)
        st.session_state.engine_started = True

_maybe_start()

# ─── Helpers ──────────────────────────────────────────────────────────────────

PAIRS = all_pairs()

def has_creds():
    c = st.session_state.credentials
    return bool(c.get("cookies") and c.get("token"))

def fmt_time(dt: datetime) -> str:
    return dt.strftime("%H:%M:%S")

def parse_env(text: str) -> dict:
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        result[key.strip()] = val.strip().strip('"').strip("'")
    return result

def countdown_str(sec: float) -> str:
    s = max(0, int(sec))
    return f"{s // 60:02d}:{s % 60:02d}"

def dir_icon(d: str) -> str:
    return {"call": "🟢 ⬆ CALL", "put": "🔴 ⬇ PUT"}.get(d, "⚪ — NEUTRAL")

def elapsed_str(ts: float) -> str:
    s = int(time.time() - ts)
    if s < 60:   return f"{s}s ago"
    if s < 3600: return f"{s//60}m ago"
    return f"{s//3600}h ago"

# ─── Sidebar ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## ⚡ Signal Desk")
    st.divider()

    status = engine.status
    if status == "connected":
        st.success("● Connected")
        if engine.last_scan_time:
            st.caption(f"Last scan: {elapsed_str(engine.last_scan_time)}")
    elif status == "connecting":
        st.warning("◌ Connecting...")
    elif status == "error":
        st.error(f"✗ Error: {engine.error or 'unknown'}")
    else:
        st.error("○ Disconnected")

    st.divider()
    page = st.radio(
        "Navigation",
        ["Dashboard", "Generate Signal", "Signal History", "Connection"],
        label_visibility="collapsed",
    )
    st.divider()
    st.caption(f"Pairs: **{len(PAIRS)}** (7 Real + 18 OTC)")
    signals_in_session = len(st.session_state.signal_history)
    st.caption(f"Signals this session: **{signals_in_session}**")

    if status == "connected":
        if st.button("⏹ Disconnect", use_container_width=True):
            engine.stop()
            st.session_state.engine_started = False
            st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════

if page == "Dashboard":
    st.title("Market Overview")

    if not has_creds():
        st.info("👈 Go to **Connection** page, enter your credentials — the engine starts automatically.")
        st.stop()

    if status == "connecting":
        st.warning("Connecting to Quotex... please wait.")
        time.sleep(2)
        st.rerun()

    if status == "error":
        st.error(f"Engine error: {engine.error}")
        if st.button("Retry connection"):
            engine.start(st.session_state.credentials)
            st.rerun()
        st.stop()

    signals = engine.signals

    # ── Stats ─────────────────────────────────────────────────────────────────
    calls    = [s for s in signals if s["direction"] == "call"]
    puts     = [s for s in signals if s["direction"] == "put"]
    actives  = [s for s in signals if s["direction"] != "neutral"]
    avg_conf = round(sum(s["confidence"] for s in actives) / len(actives), 1) if actives else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Active Pairs",   len(PAIRS))
    c2.metric("CALL signals",   len(calls))
    c3.metric("PUT signals",    len(puts))
    c4.metric("Avg Confidence", f"{avg_conf}%")

    if engine.last_scan_time:
        st.caption(f"Last full scan: {elapsed_str(engine.last_scan_time)}  •  Next scan in ~{max(0, 20 - int(time.time() - engine.last_scan_time))}s")

    st.divider()

    # ── Signal grid ───────────────────────────────────────────────────────────
    st.subheader("Live Signals Grid")

    if not signals:
        with st.spinner("Engine running — first scan in progress..."):
            time.sleep(3)
        st.rerun()
    else:
        # Store new non-neutral signals into history
        for s in signals:
            if s["direction"] != "neutral":
                already = any(
                    h.get("symbol") == s["symbol"] and
                    abs(h.get("raw_ts", 0) - s["timestamp"]) < 25
                    for h in st.session_state.signal_history
                )
                if not already:
                    entry_dt, exit_dt, _ = get_next_candle_window(60)
                    st.session_state.signal_history.append({
                        **s,
                        "raw_ts":     s["timestamp"],
                        "entry_time": fmt_time(entry_dt),
                        "exit_time":  fmt_time(exit_dt),
                        "scan_time":  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    })

        ordered = (
            [s for s in signals if s["direction"] == "call"] +
            [s for s in signals if s["direction"] == "put"] +
            [s for s in signals if s["direction"] == "neutral"]
        )

        cols_per_row = 4
        for row_start in range(0, len(ordered), cols_per_row):
            row  = ordered[row_start : row_start + cols_per_row]
            cols = st.columns(cols_per_row)
            for col, sig in zip(cols, row):
                with col:
                    d     = sig["direction"]
                    conf  = sig["confidence"]
                    price = f"{sig['price']:.5f}" if sig.get("price") else "—"
                    mkt   = sig["market"].upper()
                    st.markdown(
                        f"**{sig['displayName']}** `{mkt}`  \n"
                        f"{dir_icon(d)}  \n"
                        f"Confidence: `{conf:.1f}%`  \n"
                        f"Price: `{price}`"
                    )
                    st.progress(min(int(conf), 100))
                    st.divider()

    # ── Auto-refresh every 20s ────────────────────────────────────────────────
    time.sleep(20)
    st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# GENERATE SIGNAL
# ══════════════════════════════════════════════════════════════════════════════

elif page == "Generate Signal":
    st.title("Generate On-Demand Signal")
    st.caption("Pick a pair → engine fetches fresh candles → shows CALL/PUT with entry & exit time.")

    if not has_creds():
        st.warning("Go to **Connection** page first.")
        st.stop()

    left, right = st.columns([1, 1], gap="large")

    with left:
        st.subheader("Parameters")

        pair_labels = [f"{p['displayName']}  ({p['market'].upper()})" for p in PAIRS]
        pair_idx    = st.selectbox("Pair", range(len(pair_labels)), format_func=lambda i: pair_labels[i])
        sel         = PAIRS[pair_idx]

        dur_map   = {"M1 — 1 Minute": 60, "M5 — 5 Minutes": 300, "M15 — 15 Minutes": 900}
        dur_label = st.selectbox("Duration", list(dur_map.keys()))
        dur_sec   = dur_map[dur_label]

        clicked = st.button("⚡ Generate", type="primary", use_container_width=True)

        if clicked:
            with st.spinner(f"Fetching candles for {sel['displayName']}..."):
                sig, err = generate_signal_once(
                    st.session_state.credentials,
                    sel["symbol"], sel["displayName"], sel["market"],
                    dur_sec,
                )
            if err:
                st.error(f"Error: {err}")
            else:
                entry_dt, exit_dt, secs = get_next_candle_window(dur_sec)
                st.session_state.last_generated = {
                    "signal":       sig,
                    "entry_dt":     entry_dt,
                    "exit_dt":      exit_dt,
                    "generated_at": time.time(),
                    "dur_sec":      dur_sec,
                }
                if sig["direction"] != "neutral":
                    st.session_state.signal_history.append({
                        **sig,
                        "raw_ts":     sig["timestamp"],
                        "entry_time": fmt_time(entry_dt),
                        "exit_time":  fmt_time(exit_dt),
                        "scan_time":  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    })
                st.rerun()

    with right:
        gen = st.session_state.last_generated
        if not gen:
            st.info("Select a pair and click **Generate**.")
        else:
            sig      = gen["signal"]
            d        = sig["direction"]
            conf     = sig["confidence"]
            entry_dt = gen["entry_dt"]
            exit_dt  = gen["exit_dt"]
            now      = time.time()
            entry_ts = entry_dt.timestamp()
            exit_ts  = exit_dt.timestamp()
            price    = sig.get("price")

            # Direction
            if d == "call":
                st.success(f"## ⬆ CALL — {conf:.1f}%")
            elif d == "put":
                st.error(f"## ⬇ PUT — {conf:.1f}%")
            else:
                st.warning(f"## — NEUTRAL — {conf:.1f}%")

            if price:
                st.markdown(f"**{sig['displayName']}** &nbsp;|&nbsp; Price: `{price:.5f}`")
            else:
                st.markdown(f"**{sig['displayName']}**")

            st.divider()

            # Entry / Exit
            t1, t2 = st.columns(2)
            t1.metric("Entry Time", fmt_time(entry_dt), help="Enter at this candle open")
            t2.metric("Exit Time",  fmt_time(exit_dt),  help="Close trade at this time")

            st.divider()

            # Countdown — live update loop
            if now < entry_ts:
                remaining = entry_ts - now
                label = "⏳ Entry opens in"
            elif now < exit_ts:
                remaining = exit_ts - now
                label = "🟢 Trade active — closes in" if d == "call" else "🔴 Trade active — closes in"
            else:
                remaining = 0
                label = "✅ Candle closed"

            st.markdown(f"**{label}**")
            cd_ph   = st.empty()
            prog_ph = st.empty()

            if remaining > 0:
                total = (entry_ts - gen["generated_at"]) if now < entry_ts else (exit_ts - entry_ts)
                for _ in range(8):
                    n2 = time.time()
                    if n2 < entry_ts:
                        rem2 = max(0.0, entry_ts - n2)
                        prog = 1 - rem2 / max(entry_ts - gen["generated_at"], 1)
                    elif n2 < exit_ts:
                        rem2 = max(0.0, exit_ts - n2)
                        prog = 1 - rem2 / max(exit_ts - entry_ts, 1)
                    else:
                        rem2 = 0.0
                        prog = 1.0
                    cd_ph.markdown(f"### `{countdown_str(rem2)}`")
                    prog_ph.progress(min(max(prog, 0.0), 1.0))
                    if rem2 <= 0:
                        break
                    time.sleep(1)
                st.rerun()
            else:
                cd_ph.markdown("### `DONE`")
                prog_ph.progress(1.0)

            # Reasons
            reasons = sig.get("reasons", [])
            if reasons:
                st.divider()
                st.caption("**Indicator reasons:**")
                for r in reasons:
                    st.caption(f"• {r}")


# ══════════════════════════════════════════════════════════════════════════════
# SIGNAL HISTORY
# ══════════════════════════════════════════════════════════════════════════════

elif page == "Signal History":
    st.title("Signal History")
    st.caption("All CALL/PUT signals from this session (newest first).")

    history = list(reversed(st.session_state.signal_history))

    if not history:
        st.info("No signals yet — let the engine scan a few cycles.")
        st.stop()

    fc1, fc2, _ = st.columns([2, 2, 4])
    dir_f = fc1.selectbox("Direction", ["All", "CALL", "PUT"])
    mkt_f = fc2.selectbox("Market",    ["All", "REAL", "OTC"])

    if dir_f != "All":
        history = [h for h in history if h["direction"].upper() == dir_f]
    if mkt_f != "All":
        history = [h for h in history if h["market"].upper() == mkt_f]

    st.caption(f"Showing **{len(history)}** signals")
    st.divider()

    for sig in history:
        d   = sig["direction"]
        c1, c2, c3, c4, c5 = st.columns([2, 2, 2, 3, 3])
        c1.markdown(f"**{sig['displayName']}** `{sig['market'].upper()}`")
        c2.markdown(dir_icon(d))
        c3.markdown(f"`{sig['confidence']:.1f}%`")
        c4.markdown(f"Entry `{sig.get('entry_time','—')}` → Exit `{sig.get('exit_time','—')}`")
        c5.markdown(f"<small>{sig.get('scan_time','—')}</small>", unsafe_allow_html=True)
        st.divider()


# ══════════════════════════════════════════════════════════════════════════════
# CONNECTION
# ══════════════════════════════════════════════════════════════════════════════

elif page == "Connection":
    st.title("Broker Connection")
    st.caption("Enter credentials once — the engine starts automatically and runs continuously.")

    tab1, tab2 = st.tabs(["Paste .env file", "Manual entry"])

    with tab1:
        st.markdown(
            "Paste your `.env` file below. "
            "Must contain `QX_COOKIES` and `QX_TOKEN`."
        )
        env_text = st.text_area(
            ".env contents",
            height=200,
            placeholder=(
                "QX_EMAIL=your@email.com\n"
                "QX_PASSWORD=yourpassword\n"
                "QX_COOKIES=your_cookie_string_here\n"
                "QX_TOKEN=your_token_string_here"
            ),
        )
        if st.button("Load & Connect", type="primary", use_container_width=True):
            parsed = parse_env(env_text)
            creds  = {
                "email":    parsed.get("QX_EMAIL", ""),
                "password": parsed.get("QX_PASSWORD", ""),
                "cookies":  parsed.get("QX_COOKIES", ""),
                "token":    parsed.get("QX_TOKEN", ""),
            }
            if not creds["cookies"] or not creds["token"]:
                st.error("QX_COOKIES and QX_TOKEN are required.")
            else:
                st.session_state.credentials = creds
                engine.start(creds)
                st.session_state.engine_started = True
                st.success("Engine started! Go to **Dashboard** — signals will appear within 20 seconds.")

    with tab2:
        with st.form("manual_creds"):
            email    = st.text_input("QX_EMAIL",    value=st.session_state.credentials.get("email", ""))
            password = st.text_input("QX_PASSWORD", value=st.session_state.credentials.get("password", ""), type="password")
            cookies  = st.text_area("QX_COOKIES",  value=st.session_state.credentials.get("cookies", ""), height=80)
            token    = st.text_input("QX_TOKEN",   value=st.session_state.credentials.get("token", ""),  type="password")
            if st.form_submit_button("Save & Connect", use_container_width=True):
                if not cookies or not token:
                    st.error("QX_COOKIES and QX_TOKEN are required.")
                else:
                    creds = {"email": email, "password": password, "cookies": cookies, "token": token}
                    st.session_state.credentials = creds
                    engine.start(creds)
                    st.session_state.engine_started = True
                    st.success("Engine started! Go to **Dashboard**.")

    # ── Current status ────────────────────────────────────────────────────────
    if has_creds():
        st.divider()
        creds = st.session_state.credentials
        col_a, col_b = st.columns([3, 1])
        with col_a:
            st.subheader("Current credentials")
            st.markdown(f"- **Email:** `{creds.get('email') or '(not set)'}`")
            st.markdown(f"- **Cookies:** `{creds['cookies'][:50]}...`")
            st.markdown(f"- **Token:** `{'●' * 16}`")
            st.markdown(f"- **Engine:** `{engine.status}`")
        with col_b:
            st.write("")
            st.write("")
            if st.button("Clear & Disconnect", type="secondary"):
                engine.stop()
                st.session_state.credentials   = {}
                st.session_state.engine_started = False
                st.rerun()

    # ── Cloud deployment guide ────────────────────────────────────────────────
    with st.expander("☁ Streamlit Cloud deployment guide"):
        st.markdown("""
### Deploy to Streamlit Cloud (free, 24/7)

1. Push the `streamlit_app/` folder to a **GitHub repo**
2. Go to [share.streamlit.io](https://share.streamlit.io) → **New app**
3. Repo → branch → main file: **`app.py`**
4. **Advanced settings → Secrets**, paste:

```toml
QX_EMAIL    = "your@email.com"
QX_PASSWORD = "yourpassword"
QX_COOKIES  = "your_full_cookie_string"
QX_TOKEN    = "your_token_string"
```

5. Click **Deploy** — live in ~60 seconds

> **To keep it alive 24/7:** Streamlit Cloud sleeps after ~15 min inactivity.
> Add your app URL to [UptimeRobot](https://uptimerobot.com) (free) to ping it
> every 5 minutes — it will never sleep.
""")
