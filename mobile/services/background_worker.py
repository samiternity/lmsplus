import time
import schedule
import threading
from datetime import datetime
from services.scraper_cms import MobileCMSScraper

class BackgroundWorker:
    def __init__(self, credentials_func, notification_callback):
        self.credentials_func = credentials_func # Function to get current (user, pass)
        self.notification_callback = notification_callback # Function to show notification
        self.scraper = MobileCMSScraper()
        self.running = False

    def start(self):
        if self.running: return
        self.running = True
        
        # Schedule check every 4 hours
        schedule.every(4).hours.do(self.check_updates)
        # Also run immediately once (delayed by 10s to let app load)
        threading.Timer(10, self.check_updates).start()
        
        # Start loop in thread
        threading.Thread(target=self._loop, daemon=True).start()
        print("Background Worker Started")

    def _loop(self):
        while self.running:
            schedule.run_pending()
            time.sleep(60)

    def check_updates(self):
        print(f"[{datetime.now()}] Background Worker: Checking Updates...")
        creds = self.credentials_func()
        if not creds: return

        username, password = creds
        if self.scraper.login(username, password):
            data = self.scraper.scrape_summary()
            if data:
                # Logic: Compare with saved hash (simplified here)
                # For now, just notify "Check complete"
                self.notification_callback(f"Background Check: Data Updated at {data['last_updated']}")
        else:
            print("Background Worker: Login Failed")
