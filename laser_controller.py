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
        self.connect()
        self._start_monitor()

    # ── Connection ────────────────────────────────────────────
    def connect(self):
        """Connect to FluidNC via network or serial"""
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
            self.connection.settimeout(5)
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
            self.connection = serial.Serial(port, baud, timeout=1)
            time.sleep(2)
            self.connected = True
            debug_print(f"Connected to FluidNC on {port}")
        except Exception as e:
            debug_print(f"Serial connection failed: {e}")
            raise

    def reconnect(self):
        """Attempt to reconnect"""
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

    # ── Background connection monitor ─────────────────────────
    def _start_monitor(self):
        """Start background thread that auto-reconnects on drop."""
        self._monitor_running = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name='laser-monitor')
        self._monitor_thread.start()

    def _monitor_loop(self):
        while self._monitor_running:
            time.sleep(5)
            # Don't ping or read during active engraving
            if self._engraving:
                continue
            
            if self.connected:
                # Lightweight ping — send '?' and expect '<'
                try:
                    with self.lock:
                        if self.connection_type == 'network':
                            self.connection.sendall(b'?\n')
                        else:
                            self.connection.write(b'?\n')
                        self.connection.settimeout(2)
                        data = self.connection.recv(64)
                        if not data:
                            raise ConnectionError("empty response")
                except Exception:
                    debug_print("FluidNC ping failed — reconnecting...")
                    self.connected = False
                    self._line_buf = ''

            if not self.connected:
                try:
                    self.reconnect()
                    if self.connected:
                        debug_print("FluidNC reconnected successfully")
                except Exception as e:
                    debug_print(f"Reconnect attempt failed: {e}")

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
            else:
                self.connection.reset_input_buffer()
        except Exception:
            pass

    def _read_line(self, timeout=10.0):
        start = time.time()
        partial = self._line_buf
        while time.time() - start < timeout:
            if '\n' in partial:
                line, partial = partial.split('\n', 1)
                self._line_buf = partial
                stripped = line.strip()
                if stripped:
                    return stripped
                continue
            try:
                if self.connection_type == 'network':
                    self.connection.settimeout(0.05)
                    try:
                        data = self.connection.recv(256)
                        if data:
                            partial += data.decode('utf-8', errors='ignore')
                    except socket.timeout:
                        pass
                else:
                    if self.connection.in_waiting > 0:
                        data = self.connection.read(self.connection.in_waiting)
                        partial += data.decode('utf-8', errors='ignore')
                    else:
                        time.sleep(0.005)
            except Exception as e:
                debug_print(f"_read_line error: {e}")
                self._line_buf = partial
                return None
        self._line_buf = partial
        return None

    # ── Commands ──────────────────────────────────────────────
    def send_command(self, command):
        with self.lock:
            if not self.connected:
                debug_print("Not connected, attempting reconnect...")
                if not self.reconnect():
                    return False, "Not connected to FluidNC"
            try:
                cmd = command.strip() + '\n'
                if self.connection_type == 'network':
                    self.connection.sendall(cmd.encode())
                else:
                    self.connection.write(cmd.encode())
                response = self._read_line(timeout=2.0) or ''
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
            if not self.connected:
                if not self.reconnect():
                    if log_file: log_file.write("ABORT: Not connected\n"); log_file.close()
                    return False, "Not connected to FluidNC"

            self._flush_input()
            self._engraving = True

            try:
                for i, cmd in enumerate(commands):
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

                    # We increase timeout to 60s because M400 or long slow moves 
                    # can cause GRBL to delay the 'ok' until the queue has space or empties.
                    while True:
                        response = self._read_line(timeout=60.0)
                        if response is None:
                            err_msg = f"Timeout waiting for response at line {i + 1} ({cmd})"
                            debug_print(err_msg)
                            if log_file: log_file.write(f"ABORT: {err_msg}\n"); log_file.close()
                            return False, err_msg
                            
                        lc = response.lower()
                        
                        if log_file and not lc.startswith('<'): # omit constant position pings from log
                            log_file.write(f"  RECV: {response}\n")
                            log_file.flush()
                        
                        # Skip non-response lines
                        if (lc.startswith('[echo:') or 
                            lc.startswith('<') or 
                            lc.startswith('[gc:') or 
                            lc.startswith('[msg:')):
                            continue
                            
                        if lc == 'ok':
                            break
                        elif lc.startswith('error') or lc.startswith('alarm'):
                            err_msg = f"FluidNC {response} at line {i + 1} ({cmd})"
                            debug_print(err_msg)
                            if log_file: log_file.write(f"ERROR_DETECTED: {err_msg}\n")
                            # We break to continue sending the rest, but you can see errors in the log now
                            break

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

    def get_status(self):
        success, response = self.send_command("?")
        return response if success else "Not connected"

    def reset(self):
        return self.send_command("\x18")

    def stop(self):
        debug_print("EMERGENCY STOP")
        try:
            if self.connection_type == 'network':
                self.connection.sendall(b'!\n')
                time.sleep(0.1)
                self.connection.sendall(b'\x18\n')
            else:
                self.connection.write(b'!\n')
                time.sleep(0.1)
                self.connection.write(b'\x18\n')
        except Exception:
            pass
        return True, "Stopped"
