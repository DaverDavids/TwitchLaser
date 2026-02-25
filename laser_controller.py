"""
Laser Controller - Communicates with FluidNC via Telnet or Serial
"""
import socket
import serial
import time
import threading
from config import debug_print, config

class LaserController:
    def __init__(self):
        self.connected = False
        self.connection = None
        self.connection_type = config.get('fluidnc_connection', 'network')
        self.lock = threading.Lock()
        self._line_buf = ''   # persistent read buffer for _read_line
        self.connect()

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
        """Connect via Telnet (network)"""
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
        """Connect via Serial"""
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
        debug_print("Attempting to reconnect...")
        self.disconnect()
        time.sleep(2)
        self.connect()
        return self.connected

    def disconnect(self):
        """Disconnect from FluidNC"""
        if self.connection:
            try:
                self.connection.close()
            except:
                pass
        self.connected = False
        self._line_buf = ''
        debug_print("Disconnected from FluidNC")

    def _flush_input(self):
        """Discard any data already in the receive buffer."""
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
        """
        Read one complete line from FluidNC.
        Returns the stripped string, or None on timeout.
        Uses self._line_buf to handle partial reads across calls.
        """
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

    def send_command(self, command):
        """Send a single command and wait for its response."""
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
        Send G-code to FluidNC one command at a time, relying on FluidNC's
        natural flow control for smooth motion.

        How it works:
          FluidNC delays 'ok' when the motion planner buffer is full — it
          only responds once it has accepted the command into the planner.
          So sending one-at-a-time with no sleep naturally keeps the planner
          populated without ever flooding the async_tcp task on the ESP32.

          Timeline example (15-slot planner, 3000 mm/min, ~2mm segments):
            - Commands 1-15 get instant 'ok' as the planner fills up.
            - Command 16 blocks until move 1 finishes (~40ms), then 'ok' comes.
            - From here on the planner stays full: smooth constant-speed motion.

          This is simpler and more stable than any explicit pipeline scheme
          because FluidNC itself provides the backpressure.
        """
        if isinstance(gcode_lines, str):
            gcode_lines = gcode_lines.split('\n')

        commands = []
        for line in gcode_lines:
            stripped = line.split(';')[0].strip()
            if stripped:
                commands.append(stripped)

        total = len(commands)
        if total == 0:
            return True, "No commands"

        debug_print(f"Sending {total} G-code commands (natural flow control)...")

        with self.lock:
            if not self.connected:
                if not self.reconnect():
                    return False, "Not connected to FluidNC"

            self._flush_input()

            for i, cmd in enumerate(commands):
                # Send the command
                try:
                    if self.connection_type == 'network':
                        self.connection.sendall((cmd + '\n').encode())
                    else:
                        self.connection.write((cmd + '\n').encode())
                except Exception as e:
                    return False, f"Send error at line {i + 1}: {e}"

                # Wait for 'ok'. FluidNC delays this when the planner is
                # full, so the planner always stays populated. Skip echo
                # lines and status reports while waiting.
                while True:
                    response = self._read_line(timeout=15.0)
                    if response is None:
                        return False, f"Timeout waiting for response at line {i + 1}"
                    lc = response.lower()
                    if lc == 'ok':
                        break
                    elif lc.startswith('error'):
                        debug_print(f"FluidNC error at line {i + 1}: {response}")
                        break
                    # echo / status report / MSG - keep waiting for 'ok'

                if progress_callback:
                    progress_callback(i + 1, total)

        debug_print(f"Successfully sent {total} commands")
        return True, f"Completed {total} commands"

    def home(self):
        """Home all axes"""
        debug_print("Homing...")
        return self.send_command("$H")

    def unlock(self):
        """Unlock after alarm"""
        debug_print("Unlocking...")
        return self.send_command("$X")

    def get_status(self):
        """Get current status"""
        success, response = self.send_command("?")
        if success:
            return response
        return "Not connected"

    def reset(self):
        """Soft reset"""
        debug_print("Resetting...")
        return self.send_command("\x18")  # Ctrl-X

    def stop(self):
        """Emergency stop"""
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
