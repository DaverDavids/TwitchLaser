"""
Alarm Indicator + Recovery Button

LED (GPIO 17, physical pin 11)
  Blinks to signal non-idle machine states, driven directly by the Pi
  so it works even when FluidNC rejects gcode (e.g. during Alarm).

  Idle / engraving active  -> off
  Alarm                    -> fast blink 150 ms on / 150 ms off
  Hold / Door / other      -> slow blink 600 ms on / 600 ms off
  Unknown / disconnected   -> slow blink 600 ms on / 600 ms off

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
    """

    def __init__(self, laser_controller, led_pin=None, button_pin=None):
        self._laser = laser_controller

        self._led_pin    = led_pin    if led_pin    is not None else int(config.get('alarm_led_gpio_pin',       17))
        self._button_pin = button_pin if button_pin is not None else int(config.get('recovery_button_gpio_pin', 27))

        self._led    = None
        self._button = None
        self._running = False
        self._thread  = None

        # Prevent overlapping recovery attempts
        self._recovering = False

        self._init_gpio()

    # ── GPIO init ────────────────────────────────────────────
    def _init_gpio(self):
        try:
            from gpiozero import LED, Button

            self._led = LED(self._led_pin)
            debug_print(f"AlarmIndicator: LED on GPIO {self._led_pin}")

            # pull_up=True: wire button between GPIO pin and GND (no external resistor needed)
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

    # ── LED blink loop ───────────────────────────────────────
    def _led_loop(self):
        while self._running:
            state     = self._laser.machine_state.lower()
            engraving = self._laser._engraving

            if engraving or state in _IDLE_STATES:
                self._led.off()
                time.sleep(0.25)

            elif state in _ALARM_STATES:
                # Fast blink — attention needed
                self._led.on()
                time.sleep(0.15)
                self._led.off()
                time.sleep(0.15)

            else:
                # Hold, Door, Unknown, disconnected — slow blink
                self._led.on()
                time.sleep(0.6)
                self._led.off()
                time.sleep(0.6)

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
                # Clear the software abort flag so commands reach the machine
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
                # Any other non-idle state: try a soft reset then home
                debug_print(f"Recovery: Unknown state '{state}' — sending reset then home")
                self._laser.clear_stop()
                self._laser.reset()          # \x18 soft reset
                time.sleep(1.0)              # let FluidNC reinitialise
                self._laser.send_command('$X')
                time.sleep(0.3)
                self._laser.send_command('$H')

        except Exception as e:
            debug_print(f"Recovery: exception during recovery: {e}")
        finally:
            self._recovering = False
