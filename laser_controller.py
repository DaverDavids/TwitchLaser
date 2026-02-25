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
        self.connect()

    def connect(self):
        """Connect to FluidNC via network or serial"""
        try:
            if self.connection_type == 'network':
                self._connect_network()
            else:
                self._connect_serial()

            if self.connected:
                # Wait for FluidNC to initialize
                time.sleep(1)
                self.send_command("$$")  # Request settings
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
            time.sleep(2)  # Wait for reset
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
                if self.connection_type == 'network':
                    self.connection.close()
                else:
                    self.connection.close()
            except:
                pass
        self.connected = False
        debug_print("Disconnected from FluidNC")

    def send_command(self, command):
        """Send a single command to FluidNC"""
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

                # Read response
                time.sleep(0.1)
                response = self._read_response()

                debug_print(f"CMD: {command.strip()} -> {response[:50]}")
                return True, response

            except Exception as e:
                debug_print(f"Send command error: {e}")
                self.connected = False
                return False, str(e)

    def _read_response(self, timeout=2):
        """Read response from FluidNC"""
        response = ""
        start_time = time.time()

        try:
            while (time.time() - start_time) < timeout:
                if self.connection_type == 'network':
                    self.connection.settimeout(0.1)
                    try:
                        data = self.connection.recv(1024)
                        if data:
                            response += data.decode('utf-8', errors='ignore')
                    except socket.timeout:
                        if response:
                            break
                else:
                    if self.connection.in_waiting:
                        data = self.connection.read(self.connection.in_waiting)
                        response += data.decode('utf-8', errors='ignore')
                    else:
                        time.sleep(0.1)
                        if response:
                            break
        except Exception as e:
            debug_print(f"Read response error: {e}")

        return response

    def send_gcode(self, gcode_lines, progress_callback=None):
        """
        Send multiple G-code lines to FluidNC

        Args:
            gcode_lines: List of G-code commands or single string
            progress_callback: Function to call with progress (line_num, total_lines)

        Returns:
            (success, message)
        """
        if isinstance(gcode_lines, str):
            gcode_lines = gcode_lines.split('\n')

        # Filter out comments and empty lines
        commands = [line.split(';')[0].strip() 
                   for line in gcode_lines 
                   if line.strip() and not line.strip().startswith(';')]

        total = len(commands)
        debug_print(f"Sending {total} G-code commands...")

        for i, cmd in enumerate(commands):
            success, response = self.send_command(cmd)

            if not success:
                return False, f"Failed at line {i+1}: {response}"

            # Check for error responses
            if 'error' in response.lower():
                return False, f"Error at line {i+1}: {response}"

            if progress_callback:
                progress_callback(i + 1, total)

            # Small delay between commands
            time.sleep(0.01)

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
        if self.connection_type == 'network':
            self.connection.sendall(b'!\n')  # Feed hold
            time.sleep(0.1)
            self.connection.sendall(b'\x18\n')  # Reset
        else:
            self.connection.write(b'!\n')
            time.sleep(0.1)
            self.connection.write(b'\x18\n')
        return True, "Stopped"
