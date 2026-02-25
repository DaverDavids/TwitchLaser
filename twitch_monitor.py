"""
Twitch Monitor - Watches for subscriptions and triggers engraving
"""
import threading
import time
import requests
from config import debug_print

class TwitchMonitor:
    def __init__(self, enqueue_callback):
        """
        Args:
            enqueue_callback: Function to call when new subscriber detected
        """
        self.enqueue_callback = enqueue_callback
        self.running = False
        self.thread = None
        self.access_token = None

        # Load credentials
        try:
            import secrets
            self.client_id = secrets.TWITCH_CLIENT_ID
            self.client_secret = secrets.TWITCH_CLIENT_SECRET
            self.channel_name = secrets.TWITCH_CHANNEL_NAME
        except Exception as e:
            debug_print(f"Error loading Twitch credentials: {e}")
            self.client_id = None

    def get_access_token(self):
        """Get OAuth access token from Twitch"""
        if not self.client_id:
            return False

        try:
            url = "https://id.twitch.tv/oauth2/token"
            params = {
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'grant_type': 'client_credentials'
            }

            response = requests.post(url, params=params)
            if response.status_code == 200:
                self.access_token = response.json()['access_token']
                debug_print("Got Twitch access token")
                return True
            else:
                debug_print(f"Failed to get token: {response.status_code}")
                return False
        except Exception as e:
            debug_print(f"Token request error: {e}")
            return False

    def get_user_id(self):
        """Get channel user ID from channel name"""
        if not self.access_token:
            if not self.get_access_token():
                return None

        try:
            url = "https://api.twitch.tv/helix/users"
            headers = {
                'Client-ID': self.client_id,
                'Authorization': f'Bearer {self.access_token}'
            }
            params = {'login': self.channel_name}

            response = requests.get(url, headers=headers, params=params)
            if response.status_code == 200:
                data = response.json()
                if data['data']:
                    user_id = data['data'][0]['id']
                    debug_print(f"Channel ID: {user_id}")
                    return user_id

            debug_print(f"Failed to get user ID: {response.status_code}")
            return None
        except Exception as e:
            debug_print(f"User ID request error: {e}")
            return None

    def check_subscriptions(self, user_id, last_check_time):
        """Check for new subscriptions since last check"""
        if not self.access_token:
            return []

        try:
            url = f"https://api.twitch.tv/helix/subscriptions"
            headers = {
                'Client-ID': self.client_id,
                'Authorization': f'Bearer {self.access_token}'
            }
            params = {
                'broadcaster_id': user_id,
                'first': 100  # Max 100 at a time
            }

            response = requests.get(url, headers=headers, params=params)

            if response.status_code == 401:
                # Token expired, refresh
                debug_print("Token expired, refreshing...")
                if self.get_access_token():
                    return self.check_subscriptions(user_id, last_check_time)
                return []

            if response.status_code == 200:
                data = response.json()
                new_subs = []

                # Note: This is simplified - you'd need EventSub for real-time
                # This polls for new subscribers
                for sub in data.get('data', []):
                    # In a real implementation, track which subs we've seen
                    user_name = sub.get('user_name', 'Unknown')
                    new_subs.append(user_name)

                return new_subs
            else:
                debug_print(f"Subscription check failed: {response.status_code}")
                return []

        except Exception as e:
            debug_print(f"Subscription check error: {e}")
            return []

    def monitor_loop(self):
        """Main monitoring loop"""
        debug_print("Twitch monitor started")

        user_id = self.get_user_id()
        if not user_id:
            debug_print("Failed to get user ID, monitoring disabled")
            return

        last_check = time.time()
        check_interval = 30  # Check every 30 seconds
        seen_subs = set()

        while self.running:
            try:
                current_time = time.time()

                if current_time - last_check >= check_interval:
                    subs = self.check_subscriptions(user_id, last_check)

                    for sub_name in subs:
                        if sub_name not in seen_subs:
                            debug_print(f"New subscriber: {sub_name}")
                            seen_subs.add(sub_name)

                            # Trigger engraving
                            if self.enqueue_callback:
                                self.enqueue_callback(sub_name, 'subscription')

                    last_check = current_time

                time.sleep(1)

            except Exception as e:
                debug_print(f"Monitor loop error: {e}")
                time.sleep(5)

        debug_print("Twitch monitor stopped")

    def start(self):
        """Start monitoring in background thread"""
        if not self.client_id:
            debug_print("Twitch credentials not configured")
            return False

        if self.running:
            debug_print("Monitor already running")
            return True

        self.running = True
        self.thread = threading.Thread(target=self.monitor_loop, daemon=True)
        self.thread.start()
        debug_print("Twitch monitor thread started")
        return True

    def stop(self):
        """Stop monitoring"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        debug_print("Twitch monitor stopped")

    def is_running(self):
        """Check if monitor is running"""
        return self.running and self.thread and self.thread.is_alive()
