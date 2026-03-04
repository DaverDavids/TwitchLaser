"""
Layout Manager - Tracks placed names and finds empty spaces on the engraving board.
"""

import json
import math
import os
import random
import shutil
from datetime import datetime
from pathlib import Path

from config import debug_print


class LayoutManager:
    def __init__(self,
                 data_file='data/placements.json',
                 width_mm=None,
                 height_mm=None,
                 machine_width_mm=None,
                 machine_height_mm=None,
                 offset_x_mm=0.0,
                 offset_y_mm=0.0):
        from config import config as _cfg

        self.data_file       = data_file
        self.width_mm        = width_mm        if width_mm        is not None else _cfg.get('engraving_area.active_width_mm',   200)
        self.height_mm       = height_mm       if height_mm       is not None else _cfg.get('engraving_area.active_height_mm',  298)
        self.machine_width_mm  = machine_width_mm  if machine_width_mm  is not None else _cfg.get('engraving_area.machine_width_mm',  200)
        self.machine_height_mm = machine_height_mm if machine_height_mm is not None else _cfg.get('engraving_area.machine_height_mm', 298)

        self.offset_x_mm     = offset_x_mm
        self.offset_y_mm     = offset_y_mm

        # Margins and padding (mm), configurable from config/web UI
        self.edge_margin_mm  = _cfg.get('engraving_area.edge_margin_mm', 1.5)
        self.name_padding_mm = _cfg.get('engraving_area.name_padding_mm', 1.5)

        self.placements = []
        self.load()

    # ── Persistence ───────────────────────────────────────────
    def load(self):
        Path(os.path.dirname(self.data_file)).mkdir(parents=True, exist_ok=True)
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, 'r') as f:
                    data = json.load(f)
                self.placements = data.get('placements', [])
                debug_print(f'Loaded {len(self.placements)} placements')
            except Exception as e:
                debug_print(f'Error loading placements: {e}')
                self.placements = []
        else:
            self.placements = []

    def save(self):
        try:
            with open(self.data_file, 'w') as f:
                json.dump({
                    'placements':        self.placements,
                    'width_mm':          self.width_mm,
                    'height_mm':         self.height_mm,
                    'machine_width_mm':  self.machine_width_mm,
                    'machine_height_mm': self.machine_height_mm,
                    'offset_x_mm':       self.offset_x_mm,
                    'offset_y_mm':       self.offset_y_mm,
                }, f, indent=2)
            debug_print(f'Saved {len(self.placements)} placements')
            return True
        except Exception as e:
            debug_print(f'Error saving placements: {e}')
            return False

    def archive_and_reset(self):
        """
        Copy current placements.json to a timestamped backup, then clear.
        Returns the backup path or None if there was nothing to back up.
        """
        if not os.path.exists(self.data_file):
            self.placements = []
            return None

        ts     = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup = self.data_file.replace('.json', f'_archive_{ts}.json')
        try:
            shutil.copy2(self.data_file, backup)
            debug_print(f'Archived placements → {backup}')
        except Exception as e:
            debug_print(f'Archive failed: {e}')
            backup = None

        self.placements = []
        self.save()
        return backup

    # ── Placement management ──────────────────────────────────
    def add_placement(self, name, x, y, width, height, text_height):
        self.placements.append({
            'name':          name,
            'x':             round(x, 3),
            'y':             round(y, 3),
            'width':         round(width, 3),
            'height':        round(height, 3),
            'text_height_mm': round(text_height, 3),
            'timestamp':     datetime.now().isoformat(),
        })
        self.save()
        debug_print(f'Added placement: {name} at ({x:.1f}, {y:.1f})')

    def clear_all(self):
        self.placements = []
        self.save()
        debug_print('Cleared all placements')

    # ── Space finder ──────────────────────────────────────────

    def find_empty_space(self, required_width, required_height, text_height):
        """
        Find a free rectangle for a name of the given bounding-box size.

        Algorithm:
          1. If name is wider than the active area, shrink until it fits.
          2. Collect all valid (non-overlapping) grid positions, then pick one
             with weighted-random selection.  The weight for each candidate is
             its distance to the nearest existing-placement centre, so positions
             in the emptiest part of the board get the highest probability while
             the result still varies each call.
             When the board is empty a plain random.choice() is used instead.
          3. If no valid position exists at the current size, shrink 20 % and
             recurse.
          4. Return (x_local, y_local, final_text_height) or None.
        """
        grid_size  = 2.0   # mm
        min_height = 2.0   # mm — absolute floor

        # Step 1: force-fit width
        while required_width > self.width_mm and text_height > min_height:
            new_h  = max(text_height * 0.8, min_height)
            scale  = new_h / text_height
            required_width  *= scale
            required_height *= scale
            text_height = new_h
            debug_print(f'Name too wide, shrinking to {text_height:.1f} mm')

        if required_width > self.width_mm:
            debug_print('Cannot fit name even at minimum height.')
            return None

        # Step 2: collect valid positions, then weighted-random pick
        # We ensure the entire bounding box plus edge margin stays within active area.
        max_x = self.width_mm  - required_width  - 2.0 * self.edge_margin_mm
        max_y = self.height_mm - required_height - 2.0 * self.edge_margin_mm

        if max_x >= 0 and max_y >= 0:
            x_start = int(self.edge_margin_mm)
            y_start = int(self.edge_margin_mm)

            xs = list(range(x_start, int(x_start + max_x) + 1, max(1, int(grid_size))))
            ys = list(range(y_start, int(y_start + max_y) + 1, max(1, int(grid_size))))

            valid = [
                (x, y)
                for x in xs for y in ys
                if self._is_space_empty(x, y, required_width, required_height)
            ]

            if valid:
                if not self.placements:
                    # Board is empty — pure random
                    x, y = random.choice(valid)
                else:
                    # Randomly sample to prevent event loop blocking when calculating math.hypot
                    if len(valid) > 100:
                        candidates = random.sample(valid, 100)
                    else:
                        candidates = valid

                    # Weight each candidate by its distance to the nearest
                    # existing placement centre.  Farther away = emptier area
                    # = higher probability of being chosen.
                    half_w = required_width  / 2.0
                    half_h = required_height / 2.0
                    pl_centres = [
                        (pl['x'] + pl['width']  / 2.0,
                         pl['y'] + pl['height'] / 2.0)
                        for pl in self.placements
                    ]

                    def _weight(x, y):
                        cx, cy = x + half_w, y + half_h
                        return max(
                            min(math.hypot(cx - px, cy - py)
                                for px, py in pl_centres),
                            0.1   # prevent zero-weight edge case
                        )

                    weights = [_weight(x, y) for x, y in candidates]
                    (x, y) = random.choices(candidates, weights=weights, k=1)[0]

                return (float(x), float(y), text_height)

        # Step 3: shrink and recurse
        if text_height > min_height:
            new_h  = max(text_height * 0.8, min_height)
            scale  = new_h / text_height
            debug_print(f'No space at {text_height:.1f} mm, trying {new_h:.1f} mm')
            return self.find_empty_space(
                required_width  * scale,
                required_height * scale,
                new_h,
            )

        return None

    def _is_space_empty(self, x, y, width, height):
        """True if the rectangle (x,y,width,height) plus padding does not overlap any placement."""
        p = self.name_padding_mm
        for pl in self.placements:
            if not (
                x + width  + p <= pl['x']                  or
                x           - p >= pl['x'] + pl['width']   or
                y + height + p <= pl['y']                  or
                y           - p >= pl['y'] + pl['height']
            ):
                return False
        return True

    # ── Statistics ────────────────────────────────────────────
    def get_statistics(self):
        if not self.placements:
            return {'total': 0, 'coverage_percent': 0.0, 'avg_text_height': 0.0}

        total_area     = sum(p['width'] * p['height'] for p in self.placements)
        available_area = self.width_mm * self.height_mm
        avg_height     = sum(p['text_height_mm'] for p in self.placements) / len(self.placements)

        return {
            'total':            len(self.placements),
            'coverage_percent': round((total_area / available_area) * 100, 1),
            'avg_text_height':  round(avg_height, 2),
        }
