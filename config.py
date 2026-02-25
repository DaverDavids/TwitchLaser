"""
Configuration management with persistent storage to JSON
"""
import json
import os
from pathlib import Path

# Enable/disable debug output
DEBUG = True

# Work area configuration (mm)
WORK_AREA_WIDTH = 200
WORK_AREA_HEIGHT = 298
WORK_MARGIN = 5  # Margin from edges (0 = use full area)

# Text engraving defaults
DEFAULT_TEXT_HEIGHT = 10  # mm
DEFAULT_LASER_POWER = 100  # percent (0-100)
DEFAULT_FEED_RATE = 1000  # mm/min

# Engraving work area (in machine coordinates, mm)
engraving_area = {
    # Full machine bed size
    'machine_width_mm': 200,
    'machine_height_mm': 298,

    # Active engraving region inside the bed
    # Example: 100x100 square starting at X=50, Y=100
    'active_width_mm': 100,
    'active_height_mm': 100,
    'offset_x_mm': 50,
    'offset_y_mm': 100,
}

def debug_print(*args, **kwargs):
    if DEBUG:
        print("[DEBUG]", *args, **kwargs)

class Config:
    def __init__(self, config_file='data/config.json'):
        self.config_file = config_file
        self.defaults = {
            'hostname': 'twitchlaser',
            'engraving_area': {
                'machine_width_mm':  200,
                'machine_height_mm': 298,
                'active_width_mm':   200,
                'active_height_mm':  298,
                'offset_x_mm':       0,
                'offset_y_mm':       0,
            },
            'laser_settings': {
                'power_percent':    50,
                'speed_mm_per_min': 1000,
                'passes':           1,
                'spindle_max':      1000,
            },
            'text_settings': {
                'initial_height_mm': 5.0,
                'min_height_mm':     2.0,
                'font':              'simplex',
                'spacing_mm':        2.0,
                'ttf_path':          '/usr/share/fonts/truetype/hack/Hack-Regular.ttf',
            },
            'twitch_enabled':     True,
            'camera_enabled':     True,
            'fluidnc_connection': 'network',
            'serial_port':        '/dev/ttyUSB0',
            'serial_baud':        115200,
        }

        self.config = self.load()

    def load(self):
        """Load config from JSON file or create with defaults"""
        Path(os.path.dirname(self.config_file)).mkdir(parents=True, exist_ok=True)

        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    saved = json.load(f)
                # Deep-merge: defaults fill in any missing keys from older saves
                merged = self._deep_merge(self.defaults.copy(), saved)
                debug_print(f"Loaded config from {self.config_file}")
                return merged
            except Exception as e:
                debug_print(f"Error loading config: {e}, using defaults")
                return self.defaults.copy()
        else:
            debug_print("No config file found, creating with defaults")
            self.save(self.defaults)
            return self.defaults.copy()

    def save(self, config=None):
        """Save config to JSON file"""
        if config is None:
            config = self.config

        try:
            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)
            debug_print(f"Saved config to {self.config_file}")
            return True
        except Exception as e:
            debug_print(f"Error saving config: {e}")
            return False

    def get(self, key, default=None):
        """Get config value with optional default"""
        keys = key.split('.')
        value = self.config
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return default
        return value if value is not None else default

    def set(self, key, value):
        """Set config value and save"""
        keys = key.split('.')
        config = self.config

        for k in keys[:-1]:
            if k not in config:
                config[k] = {}
            config = config[k]

        config[keys[-1]] = value
        self.save()
        debug_print(f"Set {key} = {value}")

    def _deep_merge(self, base, override):
        """Merge override into base recursively"""
        for k, v in override.items():
            if k in base and isinstance(base[k], dict) and isinstance(v, dict):
                base[k] = self._deep_merge(base[k], v)
            else:
                base[k] = v
        return base

    def update(self, updates):
        """Update multiple config values (deep merge to preserve nested keys)"""
        self.config = self._deep_merge(self.config, updates)
        self.save()


# Global config instance
config = Config()
