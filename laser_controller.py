"""
Laser Controller - Communicates with FluidNC via Telnet or Serial
"""
import socket
import serial
import time
import threading
import os
from datetime import datetime
from config import debug_print, config

class LaserController:
    def __init__(self):
        self.connected = False
        self.connection = None
        self.connection_type = config.get('fluidnc_connection', 'network')
        self.lock = threading.Lock()
        self._line_buf = ''
        self._monitor_thread = None
        self._monitor_running = False
        self._engraving = False  # flag to pause monitor during jobs
        self._abort_flag = False # flag to interrupt gcode stream immediately
        
        self.machine_state = "Unknown"
        self.mpos = {"x": 0.0, "y": 0.0, "z": 0.0}
        
        self.connect()
        self._start_monitor()

    # ── Connection ────────────────────────────────────────────
    def connect(self):
        """Connect to FluidNC via network or serial"""
        if self._engraving:
            debug_print("Cannot connect while actively engraving!")
            return False
            
        try:
            if self.connection_type == 'network':
                self._connect_network()
            else:
                self._connect_serial()

            if self.connected:
                time.sleep(1)
                self.send_command("$$")
                debug_print("FluidNC connected and initialized")
        except Exception as e:
            debug_print(f"Connection error: {e}")
            self.connected = False

    def _connect_network(self):
        try:
            import secrets
            host = secrets.FLUIDNC_HOST
            port = secrets.FLUIDNC_PORT
            self.connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # Short timeout on the socket itself so we can intercept connection drops easily
            self.connection.settimeout(0.5) 
            self.connection.connect((host, port))
            self.connected = True
            debug_print(f"Connected to FluidNC at {host}:{port}")
        except Exception as e:
            debug_print(f"Network connection failed: {e}")
            raise

    def _connect_serial(self):
        try:
            port = config.get('serial_port', '/dev/ttyUSB0')
            baud = config.get('serial_baud', 115200)
            self.connection = serial.Serial(port, baud, timeout=0.1)
            time.sleep(2)
            self.connected = True
            debug_print(f"Connected to FluidNC on {port}")
        except Exception as e:
            debug_print(f"Serial connection failed: {e}")
            raise

    def reconnect(self):
        """Attempt to reconnect"""
        if self._engraving:
            debug_print("Blocked reconnect attempt during active engrave.")
            return False
            
        debug_print("Attempting to reconnect to FluidNC...")
        self.disconnect()
        time.sleep(2)
        self.connect()
        return self.connected

    def disconnect(self):
        if self.connection:
            try:
                self.connection.close()
            except:
                pass
        self.connected = False
        self._line_buf = ''
        debug_print("Disconnected from FluidNC")

    # ── Status Parsing ────────────────────────────────────────
    def _parse_status(self, response):
        # Example: <Idle|MPos:10.000,20.000,0.000|FS:0,0>
        try:
            content = response.strip('<>\r\n ')
            parts = content.split('|')
            if not parts: return
            
            # Grbl state is the first item
            self.machine_state = parts[0]
            
            for p in parts[1:]:
                if p.startswith('MPos:') or p.startswith('WPos:'):
                    coords = p.split(':')[1].split(',')
                    if len(coords) >= 3:
                        self.mpos['x'] = float(coords[0])
                        self.mpos['y'] = float(coords[1])
                        self.mpos['z'] = float(coords[2])
        except Exception:
            pass

    # ── Background connection monitor ─────────────────────────
    def _start_monitor(self):
        """Start background thread that auto-reconnects on drop."""
        self._monitor_running = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name='laser-monitor')
        self._monitor_thread.start()

    def _monitor_loop(self):
        while self._monitor_running:
            time.sleep(0.5)
            
            if self.connected:
                got_lock = self.lock.acquire(blocking=False)
                if got_lock:
                    try:
                        # We have the lock, meaning no active commands or streams.
                        # Safely ask for status and parse the buffer.
                        if self.connection_type == 'network':
                            self.connection.sendall(b'?')
                        else:
                            self.connection.write(b'?')
                            
                        # Read specifically to catch the <...> block
                        resp = self._read_line(max_wait_seconds=0.2)
                        if resp and resp.startswith('<'):
                            self._parse_status(resp)
                    except Exception:
                        debug_print("FluidNC ping failed — reconnecting...")
                        self.connected = False
                        self._line_buf = ''
                    finally:
                        self.lock.release()
                else:
                    # System is actively streaming gcode! The lock is held by send_gcode.
                    # Send ? without lock (safe for GRBL single byte RT commands)
                    # The response will be caught and parsed by the send_gcode read loop.
                    try:
                        if self.connection_type == 'network':
                            self.connection.sendall(b'?')
                        else:
                            self.connection.write(b'?')
                    except Exception:
                        self.connected = False

            if not self.connected:
                try:
                    self.reconnect()
                    if self.connected:
                        debug_print("FluidNC reconnected successfully")
                except Exception as e:
                    debug_print(f"Reconnect attempt failed: {e}")
                    time.sleep(4.5) # Wait longer before retrying

    def stop_monitor(self):
        self._monitor_running = False

    # ── I/O helpers ───────────────────────────────────────────
    def _flush_input(self):
        self._line_buf = ''
        try:
            if self.connection_type == 'network':
                self.connection.settimeout(0.1)
                while True:
                    try:
                        data = self.connection.recv(1024)
                        if not data:
                            break
                    except socket.timeout:
                        break
                self.connection.settimeout(0.5)
            else:
                self.connection.reset_input_buffer()
        except Exception:
            pass

    def _read_line(self, max_wait_seconds=15.0):
        start = time.time()
        while time.time() - start < max_wait_seconds:
            if getattr(self, '_abort_flag', False):
                return "ALARM: aborted by software"
                
            # Check if we have a full line in buffer
            if '\n' in self._line_buf:
                line, self._line_buf = self._line_buf.split('\n', 1)
                line = line.strip()
                if line:
                    return line
                continue
                
            try:
                if self.connection_type == 'network':
                    try:
                        data = self.connection.recv(1024)
                        if data:
                            self._line_buf += data.decode('utf-8', errors='ignore')
                        else:
                            debug_print("_read_line: Connection remotely closed.")
                            return None
                    except socket.timeout:
                        # expected behavior if laser is just executing a move
                        continue
                else:
                    if self.connection.in_waiting > 0:
                        data = self.connection.read(self.connection.in_waiting)
                        self._line_buf += data.decode('utf-8', errors='ignore')
                    else:
                        time.sleep(0.01)
            except Exception as e:
                debug_print(f"_read_line unexpected error: {e}")
                return None
                
        return None

    # ── Commands ──────────────────────────────────────────────
    def send_command(self, command):
        with self.lock:
            if not self.connected:
                debug_print("Not connected, attempting reconnect...")
                if not self.reconnect():
                    return False, "Not connected to FluidNC"
            try:
                # Discard any old buffer
                self._flush_input()
                cmd = command.strip() + '\n'
                if self.connection_type == 'network':
                    self.connection.sendall(cmd.encode())
                else:
                    self.connection.write(cmd.encode())
                    
                # GRBL real-time commands (~, !, ?, \x18) don't return 'ok'
                if command in ('?', '~', '!', '\x18'):
                    return True, "Sent RT command"

                response = self._read_line(max_wait_seconds=2.0) or ''
                
                # If we catch a status update instead of the answer, parse it and fetch next
                while response.startswith('<'):
                    self._parse_status(response)
                    response = self._read_line(max_wait_seconds=2.0) or ''
                    
                debug_print(f"CMD: {command.strip()} -> {response[:80]}")
                return True, response
            except Exception as e:
                debug_print(f"send_command error: {e}")
                self.connected = False
                return False, str(e)

    def send_gcode(self, gcode_lines, progress_callback=None):
        """
        Send G-code one command at a time.
        FluidNC delays 'ok' when the planner is full, providing natural
        backpressure that keeps the planner populated without TCP flooding.
        """
        if isinstance(gcode_lines, str):
            gcode_lines = gcode_lines.split('\n')

        commands = [l.split(';')[0].strip() for l in gcode_lines if l.split(';')[0].strip()]
        total = len(commands)
        if total == 0:
            return True, "No commands"

        log_path = os.path.join(os.path.dirname(__file__), "gcode_stream.log")
        try:
            log_file = open(log_path, "a")
            log_file.write(f"\n\n--- NEW STREAM START: {datetime.now()} ---\n")
        except:
            log_file = None

        debug_print(f"Sending {total} G-code commands...")

        with self.lock:
            self._abort_flag = False
            if not self.connected:
                if not self.reconnect():
                    if log_file: log_file.write("ABORT: Not connected\n"); log_file.close()
                    return False, "Not connected to FluidNC"

            self._flush_input()
            self._engraving = True

            try:
                for i, cmd in enumerate(commands):
                    if getattr(self, '_abort_flag', False):
                        err_msg = "Job aborted by user/E-Stop"
                        if log_file: log_file.write(f"ABORT: {err_msg}\n"); log_file.close()
                        return False, err_msg
                        
                    try:
                        if self.connection_type == 'network':
                            self.connection.sendall((cmd + '\n').encode())
                        else:
                            self.connection.write((cmd + '\n').encode())
                            
                        if log_file:
                            log_file.write(f"[{i+1}/{total}] SENT: {cmd}\n")
                            log_file.flush()
                            
                    except Exception as e:
                        err_msg = f"Send error at line {i + 1}: {e}"
                        if log_file: log_file.write(f"ABORT: {err_msg}\n"); log_file.close()
                        return False, err_msg

                    # Wait for OK
                    while True:
                        # G1 lines and synchronous commands like M5 can take a very 
                        # long time to return 'ok' because FluidNC will not return ok 
                        # until the physical movement finishes if the planner buffer is full.
                        # We use a 1-hour timeout (3600s) specifically for the streaming loop.
                        response = self._read_line(max_wait_seconds=3600.0)
                        
                        if response is None:
                            err_msg = f"Timeout waiting for response at line {i + 1} ({cmd})"
                            debug_print(err_msg)
                            if log_file: log_file.write(f"ABORT: {err_msg}\n"); log_file.close()
                            return False, err_msg
                            
                        lc = response.lower()
                        
                        # Parse status if background thread sent a '?' query
                        if lc.startswith('<'):
                            self._parse_status(response)
                            continue
                        
                        if log_file:
                            log_file.write(f"  RECV: {response}\n")
                            log_file.flush()
                        
                        if lc == 'ok':
                            break
                            
                        if 'grbl' in lc or 'fluidnc' in lc:
                            err_msg = f"Controller reset detected at line {i + 1} ({cmd})"
                            debug_print(err_msg)
                            if log_file: log_file.write(f"RESET_DETECTED: {err_msg}\n")
                            return False, err_msg
                            
                        if lc.startswith('error') or lc.startswith('alarm'):
                            err_msg = f"FluidNC {response} at line {i + 1} ({cmd})"
                            debug_print(err_msg)
                            if log_file: log_file.write(f"ERROR_DETECTED: {err_msg}\n")
                            return False, err_msg
                            
                        # Skip non-response lines
                        if (lc.startswith('[echo:') or 
                            lc.startswith('[gc:') or 
                            lc.startswith('[msg:')):
                            continue

                    if getattr(self, '_abort_flag', False):
                        err_msg = "Job aborted by user/E-Stop"
                        if log_file: log_file.write(f"ABORT: {err_msg}\n"); log_file.close()
                        return False, err_msg

                    if progress_callback:
                        progress_callback(i + 1, total)

                debug_print(f"Successfully sent {total} commands")
                if log_file: 
                    log_file.write(f"--- STREAM COMPLETE ---\n")
                    log_file.close()
                return True, f"Completed {total} commands"
                
            finally:
                self._engraving = False
                if log_file and not log_file.closed:
                    log_file.close()

    # ── Convenience commands ──────────────────────────────────
    def home(self):
        return self.send_command("$H")

    def unlock(self):
        return self.send_command("$X")
        
    def resume(self):
        return self.send_command("~")

    def reset(self):
        return self.send_command("\x18")
        
    def clear_alarm(self):
        """Standard sequence to force clear a hard alarm and regain control"""
        self.send_command("\x18")  # Ctrl-X Soft Reset
        time.sleep(0.5)
        self.send_command("$X")    # Unlock
        time.sleep(0.1)
        return True, "Alarm Cleared"

    def stop(self):
        debug_print("EMERGENCY STOP")
        self._abort_flag = True
        try:
            if self.connection_type == 'network':
                self.connection.sendall(b'!')
                time.sleep(0.1)
                self.connection.sendall(b'\x18')
            else:
                self.connection.write(b'!')
                time.sleep(0.1)
                self.connection.write(b'\x18')
        except Exception as e:
            debug_print(f"Stop command failed to send: {e}")
        return True, "Stopped"
