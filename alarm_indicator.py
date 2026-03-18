"""
Alarm Indicator - Blinks a GPIO LED to signal non-idle machine states.

The LED is driven directly by the Pi via gpiozero, completely independent
of FluidNC.  This means it works even when FluidNC rejects gcode commands
(e.g. during an Alarm state).

Blink patterns:
  Idle / engraving active  -> LED off (job runner owns the engraving LED)
  Alarm                    -> fast blink 150 ms on / 150 ms off
  Hold / Door / homing     -> slow blink 600 ms on / 600 ms off
  Unknown / disconnected   -> slow blink 600 ms on / 600 ms off

Default GPIO pin: 17 (physical pin 11).  Override via config key
'alarm_led_gpio_pin'.
"""

import threading
import time
from config import debug_print, config

# States where we leave the LED completely alone
_IDLE_STATES   = {'idle'}
# States that warrant a fast blink
_ALARM_STATES  = {'alarm'}
# States that warrant a slow blink (everything else that isn't idle/engraving)
_HOLD_STATES   = {'hold', 'door', 'home', 'jog', 'check'}


class AlarmIndicator:
    """
    Runs a background thread that watches laser_controller.machine_state
    and drives a GPIO LED accordingly.
    """

    def __init__(self, laser_controller, pin=None):
        self._laser = laser_controller
        self._pin   = pin if pin is not None else int(config.get('alarm_led_gpio_pin', 17))
        self._led   = None
        self._running = False
        self._thread  = None

        self._init_gpio()

    def _init_gpio(self):
        try:
            from gpiozero import LED
            self._led = LED(self._pin)
            debug_print(f"AlarmIndicator: GPIO {self._pin} ready")
        except ImportError:
            debug_print("AlarmIndicator: gpiozero not installed — alarm LED disabled")
        except Exception as e:
            debug_print(f"AlarmIndicator: GPIO init failed ({e}) — alarm LED disabled")

    def start(self):
        if self._led is None:
            return  # gpiozero unavailable, silently skip
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name='alarm-indicator')
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
        debug_print("AlarmIndicator: stopped")

    def _loop(self):
        while self._running:
            state     = self._laser.machine_state.lower()
            engraving = self._laser._engraving

            if engraving or state in _IDLE_STATES:
                # Leave LED off; job runner controls the engraving LED via M67
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
