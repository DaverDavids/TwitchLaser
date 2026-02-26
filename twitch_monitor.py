"""
Twitch Monitor - Watches for subscriptions and triggers engraving
"""
import threading
import time
import requests
from config import debug_print

class TwitchMonitor:
    def __init__(self, enqueue_callback):
        self.enqueue_callback = enqueue_callback
        self.running = False
        self.thread = None
        self.access_token = None
        self._reconnect_requested = False

        try:
            import secrets
            self.client_id     = secrets.TWITCH_CLIENT_ID
            self.client_secret = secrets.TWITCH_CLIENT_SECRET
            self.channel_name  = secrets.TWITCH_CHANNEL_NAME
        except Exception as e:
            debug_print(f"Error loading Twitch credentials: {e}")
            self.client_id = None

    # ── Auth ──────────────────────────────────────────────────
    def get_access_token(self):
        if not self.client_id:
            return False
        try:
            r = requests.post(
                "https://id.twitch.tv/oauth2/token",
                params={
                    'client_id':     self.client_id,
                    'client_secret': self.client_secret,
                    'grant_type':    'client_credentials',
                },
                timeout=10,
            )
            if r.status_code == 200:
                self.access_token = r.json()['access_token']
                debug_print("Got Twitch access token")
                return True
            debug_print(f"Failed to get token: {r.status_code}")
            return False
        except Exception as e:
            debug_print(f"Token request error: {e}")
            return False

    def get_user_id(self):
        if not self.access_token:
            if not self.get_access_token():
                return None
        try:
            r = requests.get(
                "https://api.twitch.tv/helix/users",
                headers={'Client-ID': self.client_id,
                         'Authorization': f'Bearer {self.access_token}'},
                params={'login': self.channel_name},
                timeout=10,
            )
            if r.status_code == 200:
                data = r.json().get('data', [])
                if data:
                    uid = data[0]['id']
                    debug_print(f"Channel ID: {uid}")
                    return uid
            debug_print(f"Failed to get user ID: {r.status_code}")
            return None
        except Exception as e:
            debug_print(f"User ID request error: {e}")
            return None

    def check_subscriptions(self, user_id, last_check_time):
        if not self.access_token:
            return []
        try:
            r = requests.get(
                "https://api.twitch.tv/helix/subscriptions",
                headers={'Client-ID': self.client_id,
                         'Authorization': f'Bearer {self.access_token}'},
                params={'broadcaster_id': user_id, 'first': 100},
                timeout=10,
            )
            if r.status_code == 401:
                debug_print("Token expired, refreshing...")
                if self.get_access_token():
                    return self.check_subscriptions(user_id, last_check_time)
                return []
            if r.status_code == 200:
                return [s.get('user_name', 'Unknown') for s in r.json().get('data', [])]
            debug_print(f"Subscription check failed: {r.status_code}")
            return []
        except Exception as e:
            debug_print(f"Subscription check error: {e}")
            return []

    # ── Monitor loop with auto-reconnect ─────────────────────
    def monitor_loop(self):
        debug_print("Twitch monitor started")
        user_id        = None
        last_check     = 0
        check_interval = 30
        seen_subs      = set()
        backoff        = 5   # seconds between reconnect attempts

        while self.running:
            # (Re)connect when user_id is missing or reconnect was requested
            if user_id is None or self._reconnect_requested:
                self._reconnect_requested = False
                debug_print("Twitch: (re)connecting...")
                self.access_token = None
                user_id = self.get_user_id()
                if not user_id:
                    debug_print(f"Twitch: failed to get user ID, retrying in {backoff}s")
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 120)  # exponential back-off, cap 2 min
                    continue
                backoff = 5  # reset on success
                debug_print("Twitch: connected")

            try:
                if time.time() - last_check >= check_interval:
                    subs = self.check_subscriptions(user_id, last_check)
                    for sub_name in subs:
                        if sub_name not in seen_subs:
                            debug_print(f"New subscriber: {sub_name}")
                            seen_subs.add(sub_name)
                            if self.enqueue_callback:
                                self.enqueue_callback(sub_name, 'subscription')
                    last_check = time.time()

                time.sleep(1)

            except requests.exceptions.ConnectionError:
                debug_print("Twitch: network error — will reconnect")
                user_id = None
                time.sleep(backoff)
            except Exception as e:
                debug_print(f"Twitch monitor error: {e}")
                time.sleep(5)

        debug_print("Twitch monitor stopped")

    # ── Public control ────────────────────────────────────────
    def start(self):
        if not self.client_id:
            debug_print("Twitch credentials not configured")
            return False
        if self.running and self.thread and self.thread.is_alive():
            debug_print("Monitor already running")
            return True
        self.running = True
        self.thread  = threading.Thread(
            target=self.monitor_loop, daemon=True, name='twitch-monitor')
        self.thread.start()
        debug_print("Twitch monitor thread started")
        return True

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        debug_print("Twitch monitor stopped")

    def reconnect(self):
        """Force a re-authentication and re-connect on the next loop tick."""
        debug_print("Twitch: manual reconnect requested")
        self._reconnect_requested = True
        if not self.is_running():
            return self.start()
        return True

    def is_running(self):
        return self.running and self.thread is not None and self.thread.is_alive()
