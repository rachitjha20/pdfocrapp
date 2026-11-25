"""
Optional one-day demo helper.
Run this on your own computer while the Render demo is needed.
It sends a lightweight GET request to Streamlit's built-in health endpoint
at a random interval and stops at midnight India time on 25 Nov 2026.
"""
from datetime import datetime, time
from zoneinfo import ZoneInfo
import random
import time as sleep_time
import urllib.request

IST = ZoneInfo("Asia/Kolkata")
DEMO_DATE = "2026-11-25"

def main():
    url = input("Render health URL (https://YOUR-SERVICE.onrender.com/_stcore/health): ").strip()
    if not url.startswith("https://") or "/_stcore/health" not in url:
        raise SystemExit("Please enter the full HTTPS Streamlit health endpoint.")
    print("One-day demo pinger started. Keep this terminal open.")
    while True:
        now = datetime.now(IST)
        if now.strftime("%Y-%m-%d") != DEMO_DATE:
            print("Demo date has ended. Pinger stopped.")
            break
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "FolioDesk-demo-health-check/1.0"})
            with urllib.request.urlopen(req, timeout=20) as response:
                print(f"{now:%H:%M:%S} IST  HTTP {response.status}")
        except Exception as exc:
            print(f"{now:%H:%M:%S} IST  health check failed: {exc}")
        # Randomised 8–12 minute interval; never runs past the demo date.
        sleep_time.sleep(random.randint(8 * 60, 12 * 60))

if __name__ == "__main__":
    main()
