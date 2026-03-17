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
        self._line_buf_lock = threading.Lock()
        self._monitor_thread = None
        self._monitor_running = False
        self._engraving = False
        self._abort_flag = False

        self.machine_state = "Unknown"
        self.mpos = {"x": 0.0, "y": 0.0, "z": 0.0}

        # Start monitor thread immediately — it handles initial connect
        # and all future reconnects, so __init__ returns without blocking.
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
        with self._line_buf_lock:
            self._line_buf = ''
        debug_print("Disconnected from FluidNC")

    # ── Status Parsing ────────────────────────────────────────
    def _parse_status(self, response):
        try:
            content = response.strip('<>\r\n ')
            parts = content.split('|')
            if not parts: return
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
        self._monitor_running = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name='laser-monitor')
        self._monitor_thread.start()

    def _monitor_loop(self):
        if not self.connected:
            debug_print("LaserController: initial connect attempt in background...")
            try:
                self.connect()
            except Exception as e:
                debug_print(f"LaserController: initial connect failed: {e}")

        while self._monitor_running:
            time.sleep(0.5)

            if self.connected:
                # Always send ? as a real-time command — never needs the lock
                try:
                    self._send_rt(b'?')
                except Exception:
                    self.connected = False
                    continue

                # Only try to read the status response if we're not mid-gcode
                if not self._engraving:
                    got_lock = self.lock.acquire(blocking=False)
                    if got_lock:
                        try:
                            resp = self._read_line(max_wait_seconds=0.2)
                            if resp and resp.startswith('<'):
                                self._parse_status(resp)
                        except Exception:
                            debug_print("FluidNC ping read failed — reconnecting...")
                            self.connected = False
                            with self._line_buf_lock:
                                self._line_buf = ''
                        finally:
                            self.lock.release()

            if not self.connected:
                try:
                    self.reconnect()
                    if self.connected:
                        debug_print("FluidNC reconnected successfully")
                except Exception as e:
                    debug_print(f"Reconnect attempt failed: {e}")
                    time.sleep(4.5)

    def stop_monitor(self):
        self._monitor_running = False

    # ── I/O helpers ───────────────────────────────────────────
    def _send_rt(self, byte_cmd):
        """Send a real-time single-byte command directly — never acquires lock."""
        if self.connection_type == 'network':
            self.connection.sendall(byte_cmd)
        else:
            self.connection.write(byte_cmd)

    def _flush_input(self):
        with self._line_buf_lock:
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
            if self._abort_flag:
                return "ALARM: aborted by software"

            with self._line_buf_lock:
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
                            with self._line_buf_lock:
                                self._line_buf += data.decode('utf-8', errors='ignore')
                        else:
                            debug_print("_read_line: Connection remotely closed.")
                            return None
                    except socket.timeout:
                        continue
                else:
                    if self.connection.in_waiting > 0:
                        data = self.connection.read(self.connection.in_waiting)
                        with self._line_buf_lock:
                            self._line_buf += data.decode('utf-8', errors='ignore')
                    else:
                        time.sleep(0.01)
            except Exception as e:
                debug_print(f"_read_line unexpected error: {e}")
                return None

        return None

    # ── Commands ──────────────────────────────────────────────
    def send_command(self, command):
        """Send a single command and return (success, response).
        Real-time commands (!, \x18, ~, ?) bypass the lock entirely.
        """
        RT_COMMANDS = {'!', '\x18', '~', '?'}
        if command.strip() in RT_COMMANDS:
            # RT commands are single-byte, no response expected, never block
            try:
                self._send_rt(command.strip().encode())
                return True, "Sent RT command"
            except Exception as e:
                return False, str(e)

        with self.lock:
            if not self.connected:
                debug_print("Not connected, attempting reconnect...")
                if not self.reconnect():
                    return False, "Not connected to FluidNC"
            try:
                self._flush_input()
                cmd = command.strip() + '\n'
                if self.connection_type == 'network':
                    self.connection.sendall(cmd.encode())
                else:
                    self.connection.write(cmd.encode())

                response = self._read_line(max_wait_seconds=2.0) or ''
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
        Send G-code one command at a time, releasing the lock between lines.

        The lock is held only for the duration of (send + wait for ok) of each
        individual line, then released before moving to the next one.  This
        means stop(), send_command(), and manual LED commands can all acquire
        the lock in the gaps between lines and get through immediately.
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

        # One-time setup — check connection and flush before the loop
        with self.lock:
            self._abort_flag = False
            if not self.connected:
                if not self.reconnect():
                    if log_file:
                        log_file.write("ABORT: Not connected\n")
                        log_file.close()
                    return False, "Not connected to FluidNC"
            self._flush_input()

        self._engraving = True
        try:
            for i, cmd in enumerate(commands):
                if self._abort_flag:
                    err_msg = "Job aborted by user/E-Stop"
                    if log_file: log_file.write(f"ABORT: {err_msg}\n")
                    return False, err_msg

                # Acquire lock for just this one line: send + wait for ok
                with self.lock:
                    if self._abort_flag:
                        err_msg = "Job aborted by user/E-Stop"
                        if log_file: log_file.write(f"ABORT: {err_msg}\n")
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
                        if log_file: log_file.write(f"ABORT: {err_msg}\n")
                        return False, err_msg

                    # Wait for ok/error response for this line
                    while True:
                        if self._abort_flag:
                            err_msg = "Job aborted by user/E-Stop"
                            if log_file: log_file.write(f"ABORT: {err_msg}\n")
                            return False, err_msg

                        response = self._read_line(max_wait_seconds=3600.0)

                        if response is None:
                            err_msg = f"Timeout waiting for response at line {i + 1} ({cmd})"
                            debug_print(err_msg)
                            if log_file: log_file.write(f"ABORT: {err_msg}\n")
                            return False, err_msg

                        lc = response.lower()

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

                        if (lc.startswith('[echo:') or
                                lc.startswith('[gc:') or
                                lc.startswith('[msg:')):
                            continue
                # Lock released here — other threads can send commands now

                if progress_callback:
                    progress_callback(i + 1, total)

            debug_print(f"Successfully sent {total} commands")
            if log_file:
                log_file.write("--- STREAM COMPLETE ---\n")
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
        self.send_command("\x18")
        time.sleep(0.5)
        self.send_command("$X")
        time.sleep(0.1)
        return True, "Alarm Cleared"

    def stop(self):
        """Emergency stop — sets abort flag and sends ! + \x18 directly,
        bypassing the lock so it always gets through during an engrave."""
        debug_print("EMERGENCY STOP")
        self._abort_flag = True
        try:
            self._send_rt(b'!')
            time.sleep(0.05)
            self._send_rt(b'\x18')
        except Exception as e:
            debug_print(f"Stop RT send failed: {e}")
        return True, "Stopped"

    def clear_stop(self):
        """Reset the abort flag so commands can flow again after a stop.
        Call this before starting a new job or sending recovery commands
        like $X / $H after an emergency stop."""
        self._abort_flag = False
        debug_print("Abort flag cleared")
