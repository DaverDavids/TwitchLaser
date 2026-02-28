"""
GCode Generator - Converts text + layout rectangles into standard G-Code
Supports standard TTF vector fonts via freetype-py.
"""

import os
import math
from freetype import Face

from config import config, debug_print


def _scan_for_fonts(fonts_dir='fonts'):
    """Scans the given directory for TTF files and builds a dictionary profile."""
    profiles = {
        'simplex': ('Simplex (Single line)', 0.4, 'ttf', 'fonts/simplex.ttf'),
        'times':   ('Times (Standard)', 0.5, 'ttf', 'fonts/times.ttf'),
        'arial':   ('Arial (Sans-serif)', 0.5, 'ttf', 'fonts/arial.ttf'),
        'cursive': ('Cursive (Elegant)', 0.3, 'ttf', 'fonts/cursive.ttf'),
        'impact':  ('Impact (Bold)', 0.6, 'ttf', 'fonts/impact.ttf'),
    }
    
    if os.path.exists(fonts_dir):
        for filename in os.listdir(fonts_dir):
            if filename.lower().endswith('.ttf'):
                key = filename[:-4].lower()
                # Don't overwrite our default labeled ones if they exist
                if key not in profiles:
                    # e.g., 'comic_sans.ttf' -> key: 'comic_sans', label: 'Comic Sans'
                    label = filename[:-4].replace('_', ' ').title()
                    path = os.path.join(fonts_dir, filename)
                    profiles[key] = (label, 0.5, 'ttf', path)
                    
    return profiles

FONT_PROFILES = _scan_for_fonts()


class GCodeGenerator:
    def __init__(self):
        # Laser settings
        s = config.get('laser_settings', {})
        self.laser_power = s.get('power_percent', 40.0)
        self.speed       = s.get('speed_mm_per_min', 800)
        self.spindle_max = s.get('spindle_max', 1000)
        
        # Check both modern and legacy keys from the web UI
        self.focal_height = s.get('z_height_mm', s.get('z_depth_mm', 0.0))

        # Text settings
        t = config.get('text_settings', {})
        self.font_key = t.get('font', 'arial')

        # Re-scan in case user added fonts while running (though a restart is safer)
        global FONT_PROFILES
        FONT_PROFILES = _scan_for_fonts()

        # Fallback to arial if the key isn't found
        if self.font_key not in FONT_PROFILES:
            self.font_key = 'arial'
            
        profile = FONT_PROFILES[self.font_key]
        self.line_width_mm = profile[1]
        self.engine        = profile[2]
        
        # If the config specified an explicit path, use it, otherwise use the scanned path
        self.ttf_path      = t.get('ttf_path', profile[3] if len(profile) > 3 else f'fonts/{self.font_key}.ttf')

        self._face = None
        self._glyph_cache = {}

        # Coordinate offsets (for multi-pass or centering)
        self.offset_x = 0.0
        self.offset_y = 0.0

    def _init_font(self):
        """Lazy load TTF font Face"""
        if not self._face:
            if os.path.exists(self.ttf_path):
                self._face = Face(self.ttf_path)
            else:
                debug_print(f"TTF font not found at {self.ttf_path}.")
                # Attempt to load a default system font if arial isn't present
                alt_paths = [
                    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
                    '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
                    '/usr/share/fonts/truetype/freefont/FreeSans.ttf'
                ]
                for p in alt_paths:
                    if os.path.exists(p):
                        self._face = Face(p)
                        self.ttf_path = p
                        debug_print(f"Loaded fallback font: {p}")
                        break

    def _get_ttf_paths(self, text, height):
        """
        Generate vector outlines from a TrueType Font using freetype-py.
        Returns a list of polygons (lists of (x,y) tuples).
        """
        self._init_font()
        if not self._face:
            debug_print("ERROR: No valid TrueType font available to render text.")
            return []

        # Calculate a reasonable point size to sample
        # We'll generate it large, then scale it down to exact `height` mm.
        self._face.set_char_size(48 * 64)

        paths = []
        cursor_x = 0.0

        scale_factor = 1.0
        max_y = -999999
        min_y = 999999

        raw_glyphs = []

        # 1. Extract raw unscaled glyph points
        for char in text:
            if char not in self._glyph_cache:
                self._face.load_char(char)
                slot = self._face.glyph
                outline = slot.outline
                
                char_paths = []
                start = 0
                for end in outline.contours:
                    contour = []
                    for i in range(start, end + 1):
                        x = outline.points[i][0]
                        y = outline.points[i][1]
                        contour.append((x, y))
                    # Close the contour
                    if contour:
                        contour.append(contour[0])
                    char_paths.append(contour)
                    start = end + 1

                advance = slot.advance.x
                self._glyph_cache[char] = (char_paths, advance)
            
            char_paths, advance = self._glyph_cache[char]
            raw_glyphs.append((char_paths, cursor_x))
            cursor_x += advance

            # Find vertical bounds of these raw paths to determine scaling
            for contour in char_paths:
                for px, py in contour:
                    if py > max_y: max_y = py
                    if py < min_y: min_y = py

        # 2. Scale and shift to match the requested physical box height
        raw_height = max_y - min_y
        if raw_height < 1e-5:
            return []
            
        scale = height / raw_height

        for char_paths, cx in raw_glyphs:
            for contour in char_paths:
                scaled_contour = []
                for px, py in contour:
                    # Scale to mm
                    sx = (px + cx) * scale
                    # Align bottom to Y=0
                    sy = (py - min_y) * scale
                    scaled_contour.append((sx, sy))
                paths.append(scaled_contour)

        return paths

    def _get_text_bounds(self, paths):
        if not paths:
            return 0, 0, 0, 0
        min_x = min_y = 999999
        max_x = max_y = -999999
        for path in paths:
            for x, y in path:
                if x < min_x: min_x = x
                if x > max_x: max_x = x
                if y < min_y: min_y = y
                if y > max_y: max_y = y
        return min_x, min_y, max_x, max_y

    def _get_bold_offsets(self, repeats, offset_mm, pattern):
        """Calculate offset vectors for bolding/repeating passes"""
        offsets = [(0.0, 0.0)]
        if repeats <= 1:
            return offsets
            
        if pattern == 'circle':
            # Distribute points around a circle
            for i in range(1, repeats):
                angle = (i - 1) * (2 * math.pi / (repeats - 1))
                offsets.append((math.cos(angle) * offset_mm, math.sin(angle) * offset_mm))
        else:
            # Cross/grid pattern
            cross_sequence = [
                (1, 0), (0, 1), (-1, 0), (0, -1),
                (1, 1), (-1, 1), (-1, -1), (1, -1)
            ]
            for i in range(1, repeats):
                idx = (i - 1) % len(cross_sequence)
                mult = 1 + ((i - 1) // len(cross_sequence))
                dx, dy = cross_sequence[idx]
                offsets.append((dx * offset_mm * mult, dy * offset_mm * mult))
                
        return offsets

    def generate(self, text, box_x, box_y, box_w, box_h, orientation='horizontal'):
        """
        Generates standard FluidNC/GRBL compatible G-code for the text inside the bounding box.
        """
        # Re-fetch settings right before generation in case they changed via web UI
        s = config.get('laser_settings', {})
        t = config.get('text_settings', {})
        
        self.laser_power  = s.get('power_percent', 40.0)
        self.speed        = s.get('speed_mm_per_min', 800)
        self.spindle_max  = s.get('spindle_max', 1000)
        self.focal_height = s.get('z_height_mm', s.get('z_depth_mm', 0.0))
        
        passes         = int(s.get('passes', 1))
        bold_repeats   = int(t.get('bold_repeats', 1))
        bold_offset_mm = float(t.get('bold_offset_mm', 0.15))
        bold_pattern   = t.get('bold_pattern', 'cross')
        
        # Convert power % to spindle S-value
        s_val = int((self.laser_power / 100.0) * self.spindle_max)

        # 1. Generate Raw Paths
        raw_paths = self._get_ttf_paths(text, box_h)

        if not raw_paths:
            return "; Error: No paths generated"

        # 2. Scale and Justify into the target Bounding Box
        min_x, min_y, max_x, max_y = self._get_text_bounds(raw_paths)
        raw_w = max_x - min_x
        raw_h = max_y - min_y

        scale = 1.0
        if raw_w > box_w:
            scale = box_w / raw_w  # Shrink to fit width

        # Center within the box
        final_w = raw_w * scale
        final_h = raw_h * scale
        
        offset_x = box_x + (box_w - final_w) / 2.0
        offset_y = box_y + (box_h - final_h) / 2.0

        # Apply global job offsets (if any)
        offset_x += self.offset_x
        offset_y += self.offset_y

        # Calculate bold offsets
        offsets = self._get_bold_offsets(bold_repeats, bold_offset_mm, bold_pattern)

        # 3. Build G-code Header
        gcode = [
            f"; TwitchLaser Engrave: '{text}'",
            "; Engine: " + self.engine,
            "; Bounding Box: X{:.1f} Y{:.1f} W{:.1f} H{:.1f}".format(box_x, box_y, box_w, box_h),
            f"; Passes: {passes} | Bold Repeats: {bold_repeats}",
            "G21 ; Millimeters",
            "G90 ; Absolute positioning",
            "M5  ; Ensure laser is off",
            f"G0 Z{self.focal_height:.4f} ; Move to physical focus height before XY movement",
        ]

        # 4. Path traversal (with repeats and passes)
        for p in range(passes):
            for b_idx, (bx, by) in enumerate(offsets):
                if passes > 1 or bold_repeats > 1:
                    gcode.append(f"; --- Pass {p+1}/{passes} | Bold Offset {b_idx+1}/{bold_repeats} (dX:{bx:.3f} dY:{by:.3f}) ---")
                    
                for path in raw_paths:
                    if not path:
                        continue
                        
                    # Move to start of path (Laser OFF)
                    start_x = (path[0][0] * scale) + offset_x + bx
                    start_y = (path[0][1] * scale) + offset_y + by
                    
                    # FluidNC Dynamic Laser Mode: M4
                    gcode.append(f"G0 X{start_x:.3f} Y{start_y:.3f}")
                    gcode.append(f"M4 S{s_val}")

                    # Trace path (Laser ON)
                    for i in range(1, len(path)):
                        px = (path[i][0] * scale) + offset_x + bx
                        py = (path[i][1] * scale) + offset_y + by
                        gcode.append(f"G1 X{px:.3f} Y{py:.3f} F{self.speed}")

                    # Laser OFF at end of path
                    gcode.append("M5")

        # 5. Footer
        gcode.extend([
            "; Job Complete",
            "G90",         # Absolute pos
            "G0 Z0",       # Move Z out of the way safely first
            "$H",          # Hardware Home (return to X0 Y0 safely via firmware)
            "$MD",         # FluidNC specific: Disable Motors to stop whining
        ])

        return "\n".join(gcode)
