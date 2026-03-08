"""
Twitch Monitor - Watches for subscriptions via Twitch IRC and triggers engraving
"""
import threading
import time
import socket
from config import config, debug_print

class TwitchMonitor:
    def __init__(self, enqueue_callback):
        self.enqueue_callback = enqueue_callback
        self.running = False
        self.thread = None
        self._reconnect_requested = False
        self.sock = None

    def _parse_tags(self, tags_str):
        tags = {}
        for part in tags_str.split(';'):
            if '=' in part:
                k, v = part.split('=', 1)
                tags[k] = v
        return tags

    def monitor_loop(self):
        debug_print("Twitch monitor thread started")
        
        while self.running:
            tw_cfg = config.get('twitch', {})
            channel = tw_cfg.get('channel', '').strip().lower()
            username = tw_cfg.get('username', '').strip().lower()
            oauth = tw_cfg.get('oauth_token', '').strip()
            
            if not channel:
                debug_print("Twitch IRC: No channel configured. Waiting 10s...")
                time.sleep(10)
                continue

            # Fallback to anonymous if credentials aren't fully provided
            if not username or not oauth:
                username = f"justinfan{int(time.time())}"
                oauth = "SCHMOOPIIE"
            elif not oauth.startswith('oauth:'):
                oauth = f"oauth:{oauth}"
                
            if channel.startswith('#'):
                channel = channel[1:]

            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(300) # 5 minutes timeout for PINGs
            
            try:
                debug_print(f"Twitch IRC: Connecting to #{channel} as {username}...")
                self.sock.connect(('irc.chat.twitch.tv', 6667))
                
                self.sock.send(f"PASS {oauth}\r\n".encode('utf-8'))
                self.sock.send(f"NICK {username}\r\n".encode('utf-8'))
                self.sock.send(f"CAP REQ :twitch.tv/tags twitch.tv/commands\r\n".encode('utf-8'))
                self.sock.send(f"JOIN #{channel}\r\n".encode('utf-8'))
                
                debug_print("Twitch IRC: Connected and joined!")
                self._reconnect_requested = False
                
                buffer = ""
                while self.running and not self._reconnect_requested:
                    try:
                        data = self.sock.recv(4096)
                        if not data:
                            break # Disconnected
                        buffer += data.decode('utf-8', errors='replace')
                    except socket.timeout:
                        debug_print("Twitch IRC: Socket timeout (no PING received). Reconnecting.")
                        break
                    
                    lines = buffer.split('\r\n')
                    buffer = lines.pop() # Keep incomplete line for next loop
                    
                    for line in lines:
                        if line.startswith('PING'):
                            self.sock.send(f"PONG {line.split()[1]}\r\n".encode('utf-8'))
                            continue
                            
                        # Parse USERNOTICE (Subscription events)
                        if 'USERNOTICE' in line:
                            # Format: @tags... :tmi.twitch.tv USERNOTICE #channel :Message
                            if line.startswith('@'):
                                parts = line.split(' ', 2)
                                tags_str = parts[0][1:] # Remove leading @
                                tags = self._parse_tags(tags_str)
                                
                                msg_id = tags.get('msg-id')
                                
                                # Standard sub or resub
                                if msg_id in ('sub', 'resub'):
                                    user = tags.get('display-name', 'Unknown')
                                    debug_print(f"Twitch IRC: Sub/Resub detected -> {user}")
                                    if self.enqueue_callback:
                                        self.enqueue_callback(user, 'Subscription')
                                        
                                # Single gift sub recipient (fires individually for every gift in a bomb)
                                elif msg_id == 'subgift':
                                    recipient = tags.get('msg-param-recipient-display-name')
                                    if recipient:
                                        debug_print(f"Twitch IRC: Gift sub received by -> {recipient}")
                                        if self.enqueue_callback:
                                            self.enqueue_callback(recipient, 'Gifted Sub')
                                            
                                # Bulk gift summary event (Ignore this! The 'subgift' handles individual recipients)
                                elif msg_id == 'submysterygift':
                                    gifter = tags.get('display-name', 'Unknown')
                                    count = tags.get('msg-param-mass-gift-count', '0')
                                    debug_print(f"Twitch IRC: {gifter} is gifting {count} subs (Ignoring summary event)")

            except Exception as e:
                debug_print(f"Twitch IRC error: {e}")
                
            if self.sock:
                try:
                    self.sock.close()
                except: pass
                self.sock = None
                
            if self.running:
                debug_print("Twitch IRC: Reconnecting in 5 seconds...")
                time.sleep(5)
                
        debug_print("Twitch monitor stopped")

    def start(self):
        if not config.get('twitch', {}).get('enabled', True):
            debug_print("Twitch monitor is disabled in config")
            return False
            
        if self.running and self.thread and self.thread.is_alive():
            debug_print("Twitch monitor already running")
            return True
            
        self.running = True
        self.thread = threading.Thread(target=self.monitor_loop, daemon=True, name='twitch-monitor')
        self.thread.start()
        return True

    def stop(self):
        self.running = False
        self._reconnect_requested = True # Break loop
        if self.sock:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
                self.sock.close()
            except: pass
        if self.thread:
            self.thread.join(timeout=5)
        debug_print("Twitch monitor stopped")

    def reconnect(self):
        debug_print("Twitch: manual reconnect requested")
        if not self.is_running():
            return self.start()
        else:
            self._reconnect_requested = True
            if self.sock:
                try:
                    self.sock.shutdown(socket.SHUT_RDWR)
                    self.sock.close()
                except: pass
            return True

    def is_running(self):
        return self.running and self.thread is not None and self.thread.is_alive()