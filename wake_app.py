from playwright.sync_api import sync_playwright
import time

def main():
    with sync_playwright() as p:
        # হেডলেস ব্রাউজার লঞ্চ করা হচ্ছে
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        url = "https://quotextsignal.streamlit.app/" # <--- আপনার অ্যাপ লিংক এখানে দিন
        print("Navigating to Streamlit app...") # <--- এখান থেকে # সরিয়ে ফেলা হয়েছে
        page.goto(url)
        time.sleep(5) # পেজ লোড হওয়ার জন্য ৫ সেকেন্ড অপেক্ষা
        
        # যদি অ্যাপটি স্লিপ মোডে থাকে, তবে ওয়েক আপ বাটনটি খুঁজবে
        wake_button = page.locator("text=Yes, get this app back up!")
        
        if wake_button.count() > 0:
            print("App is sleeping! Clicking the wake-up button...")
            wake_button.click()
            time.sleep(15) # অ্যাপটি বিল্ড হতে কিছুটা সময় দেওয়া
            print("Wake up signal sent successfully.")
        else:
            print("App is already awake! Session refreshed successfully.")
            
        browser.close()

if __name__ == "__main__":
    main()
