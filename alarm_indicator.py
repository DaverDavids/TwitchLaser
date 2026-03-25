"""
Alarm Indicator + Recovery Button

LED (GPIO 17, physical pin 11)
  Blinks to signal non-idle machine states and service health, driven
  directly by the Pi so it works even when FluidNC rejects gcode.

  Priority (highest first):
    Alarm state          -> fast blink 150 ms on / 150 ms off
    Hold / Door / other  -> slow blink 600 ms on / 600 ms off
    Twitch disconnected  -> double-pulse every 2 s
                           (100 ms on, 100 ms off, 100 ms on, 1.7 s off)
    Camera down          -> long-short pulse every 3 s
                           (500 ms on, 200 ms off, 100 ms on, 2.2 s off)
    Idle / engraving     -> off

Recovery Button (GPIO 27, physical pin 13)
  Press to attempt state-aware recovery back to Idle:

  Alarm       -> $X (unlock alarm) then $H (home)
  Hold        -> ~ (resume / cycle-start)
  Door        -> ~ (resume; close door first)
  Unknown /
  disconnected-> reconnect attempt
  Idle /
  engraving   -> ignored (no-op)

Config overrides (data/config.json):
  alarm_led_gpio_pin       (default 17)
  recovery_button_gpio_pin (default 27)
"""

import threading
import time
from config import debug_print, config

_IDLE_STATES  = {'idle'}
_ALARM_STATES = {'alarm'}


class AlarmIndicator:
    """
    Manages the alarm LED blink loop and the recovery button.
    Both are optional: if gpiozero is unavailable the class starts
    silently without crashing the service.

    Call set_twitch_status(bool) and set_camera_status(bool) from
    the web server status poller to drive the service-health blink modes.
    """

    def __init__(self, laser_controller, led_pin=None, button_pin=None):
        self._laser = laser_controller

        self._led_pin    = led_pin    if led_pin    is not None else int(config.get('alarm_led_gpio_pin',       17))
        self._button_pin = button_pin if button_pin is not None else int(config.get('recovery_button_gpio_pin', 27))

        self._led    = None
        self._button = None
        self._running = False
        self._thread  = None

        # Service-health flags (updated externally by web server)
        self._twitch_connected = True   # optimistic until first poll
        self._camera_ok        = True

        # Prevent overlapping recovery attempts
        self._recovering = False

        self._init_gpio()

    # ── GPIO init ────────────────────────────────────────────
    def _init_gpio(self):
        try:
            from gpiozero import LED, Button

            self._led = LED(self._led_pin)
            debug_print(f"AlarmIndicator: LED on GPIO {self._led_pin}")

            # pull_up=True: wire button between GPIO pin and GND
            # hold_time gives us a clean 50 ms debounce before when_pressed fires
            self._button = Button(self._button_pin, pull_up=True, hold_time=0.05, bounce_time=0.05)
            self._button.when_pressed = self._on_button_press
            debug_print(f"AlarmIndicator: recovery button on GPIO {self._button_pin}")

        except ImportError:
            debug_print("AlarmIndicator: gpiozero not installed — LED and button disabled")
        except Exception as e:
            debug_print(f"AlarmIndicator: GPIO init failed ({e}) — LED and button disabled")

    # ── Public interface ─────────────────────────────────────
    def start(self):
        if self._led is None:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._led_loop, daemon=True, name='alarm-indicator')
        self._thread.start()
        debug_print("AlarmIndicator: started")

    def stop(self):
        self._running = False
        if self._led:
            try:
                self._led.off()
                self._led.close()
            except Exception:
                pass
        if self._button:
            try:
                self._button.close()
            except Exception:
                pass
        debug_print("AlarmIndicator: stopped")

    def set_twitch_status(self, connected: bool):
        """Call from the status poller with whether Twitch IRC is connected."""
        self._twitch_connected = bool(connected)

    def set_camera_status(self, ok: bool):
        """Call from the status poller with whether ustreamer is reachable."""
        self._camera_ok = bool(ok)

    # ── LED blink loop ───────────────────────────────────────
    def _sleep(self, seconds):
        """Interruptible sleep — checks _running every 50 ms."""
        end = time.monotonic() + seconds
        while self._running and time.monotonic() < end:
            time.sleep(min(0.05, end - time.monotonic()))

    def _led_loop(self):
        while self._running:
            state     = self._laser.machine_state.lower()
            engraving = self._laser._engraving

            # ── Priority 1: Laser alarm ───────────────────────
            if state in _ALARM_STATES:
                # Fast blink — attention needed
                self._led.on();  self._sleep(0.15)
                self._led.off(); self._sleep(0.15)

            # ── Priority 2: Other non-idle machine state ──────
            elif state not in _IDLE_STATES and not engraving:
                # Hold, Door, Unknown, disconnected — slow blink
                self._led.on();  self._sleep(0.6)
                self._led.off(); self._sleep(0.6)

            # ── Priority 3: Twitch not connected ──────────────
            elif not self._twitch_connected:
                # Double-pulse: pip-pip … pause
                self._led.on();  self._sleep(0.10)
                self._led.off(); self._sleep(0.10)
                self._led.on();  self._sleep(0.10)
                self._led.off(); self._sleep(1.70)

            # ── Priority 4: Camera / ustreamer not reachable ──
            elif not self._camera_ok:
                # Long-short pulse: dash-dot … pause
                self._led.on();  self._sleep(0.50)
                self._led.off(); self._sleep(0.20)
                self._led.on();  self._sleep(0.10)
                self._led.off(); self._sleep(2.20)

            # ── Priority 5: All good (idle or engraving) ──────
            else:
                self._led.off()
                self._sleep(0.25)

    # ── Recovery button ──────────────────────────────────────
    def _on_button_press(self):
        """Called by gpiozero in its own thread when the button is pressed."""
        if self._laser._engraving:
            debug_print("Recovery button: ignored — engraving in progress")
            return
        if self._recovering:
            debug_print("Recovery button: ignored — recovery already in progress")
            return
        # Spin off so the gpiozero event thread is never blocked
        threading.Thread(target=self._do_recovery, daemon=True, name='recovery').start()

    def _do_recovery(self):
        self._recovering = True
        try:
            state = self._laser.machine_state.lower()
            debug_print(f"Recovery button pressed — machine state: '{state}'")

            if state in _IDLE_STATES:
                debug_print("Recovery: machine already Idle, nothing to do")
                return

            if state in _ALARM_STATES:
                debug_print("Recovery: Alarm — sending $X (unlock) then $H (home)")
                self._laser.clear_stop()
                ok, resp = self._laser.send_command('$X')
                debug_print(f"Recovery $X -> {resp}")
                time.sleep(0.3)
                ok, resp = self._laser.send_command('$H')
                debug_print(f"Recovery $H -> {resp}")

            elif state == 'hold':
                debug_print("Recovery: Hold — sending ~ (resume)")
                self._laser.send_command('~')

            elif state == 'door':
                debug_print("Recovery: Door open — sending ~ (resume, close door first)")
                self._laser.send_command('~')

            elif not self._laser.connected:
                debug_print("Recovery: Not connected — attempting reconnect")
                self._laser.reconnect()

            else:
                debug_print(f"Recovery: Unknown state '{state}' — sending reset then home")
                self._laser.clear_stop()
                self._laser.reset()
                time.sleep(1.0)
                self._laser.send_command('$X')
                time.sleep(0.3)
                self._laser.send_command('$H')

        except Exception as e:
            debug_print(f"Recovery: exception during recovery: {e}")
        finally:
            self._recovering = False
