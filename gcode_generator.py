"""
G-code Generator - Converts text to laser engraving G-code

Font engines:
  hershey_builtin  - built-in stroke font (always available)
  hershey_lib      - extended Hershey fonts via `hershey-fonts` package
  ttf              - any TTF/OTF font via `fontTools` package

Laser mode:
  Uses M4 (dynamic power) so G0 rapids auto-disable the laser and G1 cuts
  auto-enable it. No per-segment M3/M5 toggling needed.
"""

import math
from config import debug_print, config

# ── Built-in Hershey Simplex (uppercase + digits) ────────────
HERSHEY_SIMPLEX = {
    'A': [(0,0),(5,10),(10,0),None,(2.5,5),(7.5,5)],
    'B': [(0,0),(0,10),(7,10),(9,8),(9,6),(7,5),(0,5),None,(7,5),(9,3),(9,1),(7,0),(0,0)],
    'C': [(9,2),(7,0),(3,0),(1,2),(1,8),(3,10),(7,10),(9,8)],
    'D': [(0,0),(0,10),(6,10),(9,7),(9,3),(6,0),(0,0)],
    'E': [(9,0),(0,0),(0,10),(9,10),None,(0,5),(6,5)],
    'F': [(0,0),(0,10),(9,10),None,(0,5),(6,5)],
    'G': [(9,8),(7,10),(3,10),(1,8),(1,2),(3,0),(7,0),(9,2),(9,5),(5,5)],
    'H': [(0,0),(0,10),None,(9,0),(9,10),None,(0,5),(9,5)],
    'I': [(2,0),(7,0),None,(4.5,0),(4.5,10),None,(2,10),(7,10)],
    'J': [(0,2),(2,0),(7,0),(9,2),(9,10),(4,10)],
    'K': [(0,0),(0,10),None,(9,10),(0,5),None,(3,6),(9,0)],
    'L': [(0,10),(0,0),(9,0)],
    'M': [(0,0),(0,10),(5,5),(10,10),(10,0)],
    'N': [(0,0),(0,10),(9,0),(9,10)],
    'O': [(1,2),(1,8),(3,10),(7,10),(9,8),(9,2),(7,0),(3,0),(1,2)],
    'P': [(0,0),(0,10),(7,10),(9,8),(9,6),(7,5),(0,5)],
    'Q': [(1,2),(1,8),(3,10),(7,10),(9,8),(9,2),(7,0),(3,0),(1,2),None,(6,3),(10,-1)],
    'R': [(0,0),(0,10),(7,10),(9,8),(9,6),(7,5),(0,5),None,(5,5),(9,0)],
    'S': [(9,8),(7,10),(3,10),(1,8),(1,6),(3,5),(7,5),(9,4),(9,2),(7,0),(3,0),(1,2)],
    'T': [(0,10),(9,10),None,(4.5,10),(4.5,0)],
    'U': [(0,10),(0,2),(2,0),(7,0),(9,2),(9,10)],
    'V': [(0,10),(5,0),(10,10)],
    'W': [(0,10),(2,0),(5,5),(8,0),(10,10)],
    'X': [(0,0),(9,10),None,(0,10),(9,0)],
    'Y': [(0,10),(5,5),(10,10),None,(5,5),(5,0)],
    'Z': [(0,10),(9,10),(0,0),(9,0)],
    ' ': [],
    '0': [(1,2),(1,8),(3,10),(7,10),(9,8),(9,2),(7,0),(3,0),(1,2)],
    '1': [(3,8),(5,10),(5,0),None,(2,0),(8,0)],
    '2': [(1,8),(3,10),(7,10),(9,8),(9,6),(1,0),(9,0)],
    '3': [(1,8),(3,10),(7,10),(9,8),(9,2),(7,0),(3,0),(1,2),None,(3,5),(7,5)],
    '4': [(7,10),(1,4),(9,4),None,(7,10),(7,0)],
    '5': [(9,10),(1,10),(1,5),(7,5),(9,3),(9,2),(7,0),(3,0),(1,2)],
    '6': [(7,10),(3,10),(1,8),(1,2),(3,0),(7,0),(9,2),(9,4),(7,5),(3,5)],
    '7': [(1,10),(9,10),(4,0)],
    '8': [(3,5),(1,6),(1,8),(3,10),(7,10),(9,8),(9,6),(7,5),(3,5),(1,4),(1,2),(3,0),(7,0),(9,2),(9,4),(7,5)],
    '9': [(9,5),(7,5),(3,4),(1,2),(1,1),(3,0),(7,0),(9,2),(9,8),(7,10),(3,10),(1,8)],
    '-': [(1,5),(8,5)],
    '_': [(0,0),(9,0)],
    '.': [(4,0),(5,1)],
    '!': [(4.5,3),(4.5,10),None,(4.5,0),(5,1)],
    '?': [(1,8),(3,10),(7,10),(9,8),(9,6),(5,4),(5,2),None,(5,0),(5.5,1)],
}

# ── Font profiles: label, line_width_mm, engine ──────────────
FONT_PROFILES = {
    'simplex':        ('Simplex – thin',          0.0,  'hershey_builtin'),
    'medium':         ('Simplex – medium',         0.45, 'hershey_builtin'),
    'bold':           ('Simplex – bold',           0.9,  'hershey_builtin'),
    'heavy':          ('Simplex – heavy',          1.5,  'hershey_builtin'),
    'hershey_roman':  ('Hershey Roman (serif)',    0.0,  'hershey_lib'),
    'hershey_script': ('Hershey Script (cursive)', 0.0,  'hershey_lib'),
    'hershey_gothic': ('Hershey Gothic',           0.0,  'hershey_lib'),
    'ttf':            ('TrueType font',            0.0,  'ttf'),
    'ttf_bold':       ('TrueType font – bold',     0.6,  'ttf'),
}

# ── Optional library imports ──────────────────────────────────
try:
    from HersheyFonts import HersheyFonts as _HersheyFonts
    _HERSHEY_AVAILABLE = True
    debug_print('hershey-fonts library available')
except ImportError:
    _HERSHEY_AVAILABLE = False
    debug_print('hershey-fonts not installed; hershey_lib fonts will fall back to builtin')

try:
    from fontTools.ttLib import TTFont as _TTFont
    from fontTools.pens.recordingPen import RecordingPen as _RecordingPen
    _FONTTOOLS_AVAILABLE = True
    debug_print('fontTools library available')
except ImportError:
    _FONTTOOLS_AVAILABLE = False
    debug_print('fontTools not installed; TTF fonts will fall back to builtin')


# ── Main class ────────────────────────────────────────────────
class GCodeGenerator:
    def __init__(self,
                 laser_power=50,
                 speed_mm_per_min=1000,
                 spindle_max=1000,
                 font_key='simplex',
                 ttf_path=None):
        self.laser_power = laser_power
        self.speed = speed_mm_per_min
        self.spindle_max = spindle_max
        self.font_key = font_key
        self.ttf_path = ttf_path

        profile = FONT_PROFILES.get(font_key, FONT_PROFILES['simplex'])
        self.line_width_mm = profile[1]
        self.engine = profile[2]

        # lazy-init caches
        self._ttfont = None
        self._glyph_set = None
        self._cmap = None
        self._units_per_em = 1000
        self._glyph_cache = {}

    def _s_value(self):
        return int((self.laser_power / 100.0) * self.spindle_max)

    # ── Public API ────────────────────────────────────────────
    def estimate_dimensions(self, text, text_height_mm):
        """Return (width_mm, height_mm) for placement/collision checks."""
        _, w, h = self._build_geometry(text, text_height_mm)
        return w, h

    def text_to_gcode(self, text, x_start, y_start, text_height_mm, passes=1):
        """
        Generate M4-mode G-code at machine coordinates.
        Returns (gcode_string, actual_width_mm, actual_height_mm).
        """
        path, width, height = self._build_geometry(
            text, text_height_mm, origin=(x_start, y_start))
        s_on = self._s_value()

        gc = [
            f'; Engrave: {text}',
            f'; Font={self.font_key}  Power={self.laser_power}%  S={s_on}/{self.spindle_max}  Feed={self.speed}',
            'G21', 'G90',
            f'M4 S{s_on}',   # dynamic laser mode: G0→off, G1→on
            '',
        ]
        for p in range(passes):
            gc.append(f'; Pass {p+1}/{passes}')
            for (cmd, x, y) in path:
                if cmd == 'G0':
                    gc.append(f'G0 X{x:.3f} Y{y:.3f}')
                else:
                    gc.append(f'G1 X{x:.3f} Y{y:.3f} F{self.speed}')
            gc.append('')
        gc += ['M5', 'G0 X0 Y0', 'M2']
        return '\n'.join(gc), width, height

    # ── Geometry pipeline ─────────────────────────────────────
    def _build_geometry(self, text, text_height_mm, origin=(0.0, 0.0)):
        """
        1. Get raw polylines from engine
        2. Flip Y (font: Y+ up → machine: Y+ down → text appears upright)
        3. Normalize height, scale to text_height_mm, apply origin offset
        4. Expand strokes for bold (perpendicular offsets)
        5. Return (path_list, width_mm, height_mm)
        """
        polylines = self._glyphs_for_text(text)
        if not polylines:
            return [], 0.0, 0.0

        # ── Step 1: bounds in raw font space ─────────────────
        b = _bounds(polylines)
        if b is None:
            return [], 0.0, 0.0
        min_x, min_y, max_x, max_y = b
        units_height = (max_y - min_y) or 1.0

        # ── Step 2: flip Y so text is right-side-up ──────────
        # Machines with Y+ downward engrave text upside-down if we don't flip.
        # We negate Y and then re-normalize so the text sits above y_start.
        flipped = [[(x, -y) for (x, y) in poly] for poly in polylines]
        b2 = _bounds(flipped)
        min_x, min_y, max_x, max_y = b2
        units_height = (max_y - min_y) or 1.0
        units_width  = (max_x - min_x) or 1.0

        # ── Step 3: scale + translate to machine space ────────
        scale = text_height_mm / units_height
        ox, oy = origin

        scaled = []
        for poly in flipped:
            pts = [(ox + (x - min_x) * scale,
                    oy + (y - min_y) * scale)
                   for (x, y) in poly]
            scaled.append(pts)

        # ── Step 4: bold expansion ────────────────────────────
        if self.line_width_mm > 0:
            expanded = []
            for poly in scaled:
                for i in range(len(poly) - 1):
                    x1, y1 = poly[i]
                    x2, y2 = poly[i + 1]
                    for seg in self._bold_passes(x1, y1, x2, y2):
                        expanded.append([seg[:2], seg[2:]])
            scaled = expanded

        # ── Step 5: convert to (cmd, x, y) ───────────────────
        path = []
        for poly in scaled:
            if not poly:
                continue
            x0, y0 = poly[0]
            path.append(('G0', x0, y0))       # rapid to stroke start
            for (x, y) in poly[1:]:
                path.append(('G1', x, y))      # cut along stroke

        b3 = _bounds(scaled)
        if b3 is None:
            return path, 0.0, text_height_mm
        bx0, by0, bx1, by1 = b3
        return path, (bx1 - bx0), (by1 - by0)

    def _bold_passes(self, x1, y1, x2, y2):
        """Yield (ox1,oy1,ox2,oy2) perpendicular offsets for bold strokes."""
        step = 0.15  # mm per pass (≈ laser spot)
        n = max(1, round(self.line_width_mm / step))
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length < 1e-6:
            yield (x1, y1, x2, y2)
            return
        px, py = -dy / length, dx / length   # perpendicular unit vector
        for k in range(n):
            off = (k - (n - 1) / 2.0) * step
            yield (x1 + px*off, y1 + py*off,
                   x2 + px*off, y2 + py*off)

    # ── Font engines ──────────────────────────────────────────
    def _glyphs_for_text(self, text):
        if self.engine == 'hershey_builtin':
            return _builtin_glyphs(text)
        elif self.engine == 'hershey_lib':
            return self._hershey_lib_glyphs(text)
        elif self.engine == 'ttf':
            return self._ttf_glyphs(text)
        return _builtin_glyphs(text)

    def _hershey_lib_glyphs(self, text):
        """
        Render text using the HersheyFonts package (class-based API).
        lines_for_text() yields ((x1,y1),(x2,y2)) segment pairs.
        """
        if not _HERSHEY_AVAILABLE:
            return _builtin_glyphs(text)

        font_name_map = {
            'hershey_roman':  'rowmand',
            'hershey_script': 'scriptc',
            'hershey_gothic': 'gothgbt',
        }
        hf_name = font_name_map.get(self.font_key, 'rowmand')

        try:
            hf = _HersheyFonts()
            hf.load_default_font(hf_name)
            # Chain consecutive segments into continuous polylines so the
            # laser doesn't pulse off/on between every segment in M4 mode.
            EPS = 1e-6
            polylines = []
            current = None
            for (x1, y1), (x2, y2) in hf.lines_for_text(text):
                if current is None:
                    current = [(x1, y1), (x2, y2)]
                else:
                    lx, ly = current[-1]
                    if abs(lx - x1) < EPS and abs(ly - y1) < EPS:
                        current.append((x2, y2))
                    else:
                        polylines.append(current)
                        current = [(x1, y1), (x2, y2)]
            if current:
                polylines.append(current)
            return polylines if polylines else _builtin_glyphs(text)

        except Exception as e:
            debug_print(f'hershey_lib error: {e}, falling back to builtin')
            return _builtin_glyphs(text)

    def _ttf_glyphs(self, text):
        """
        Render text using a TTF/OTF font via fontTools.
        Bezier curves (cubic & quadratic) are flattened to line segments.
        """
        if not _FONTTOOLS_AVAILABLE:
            debug_print('fontTools not available, using builtin')
            return _builtin_glyphs(text)
        if not self.ttf_path:
            debug_print('No ttf_path configured, using builtin')
            return _builtin_glyphs(text)

        try:
            if self._ttfont is None:
                self._ttfont = _TTFont(self.ttf_path)
                self._glyph_set = self._ttfont.getGlyphSet()
                self._cmap = self._ttfont.getBestCmap() or {}
                self._units_per_em = self._ttfont['head'].unitsPerEm

            scale = 10.0 / self._units_per_em   # normalise to ~10 font-units tall

            polylines = []
            cursor_x = 0.0

            for ch in text:
                glyph_name = self._cmap.get(ord(ch))
                if not glyph_name:
                    cursor_x += 5.0
                    continue

                if glyph_name not in self._glyph_cache:
                    pen = _RecordingPen()
                    self._glyph_set[glyph_name].draw(pen)
                    glyph_polys = _ttf_recording_to_polylines(pen.value, scale)
                    advance = self._glyph_set[glyph_name].width * scale
                    self._glyph_cache[glyph_name] = (glyph_polys, advance)

                glyph_polys, advance = self._glyph_cache[glyph_name]
                for stroke in glyph_polys:
                    if len(stroke) >= 2:
                        polylines.append([(cursor_x + x, y) for (x, y) in stroke])

                cursor_x += advance

            return polylines if polylines else _builtin_glyphs(text)

        except Exception as e:
            debug_print(f'TTF render error: {e}, using builtin')
            return _builtin_glyphs(text)


# ── Module-level helpers ──────────────────────────────────────

def _builtin_glyphs(text):
    """Convert text using HERSHEY_SIMPLEX into a list of polylines."""
    polylines = []
    cursor_x = 0.0
    advance = 10.0

    for ch in text:
        strokes = HERSHEY_SIMPLEX.get(ch.upper(), [])
        current = []
        for pt in strokes:
            if pt is None:
                if len(current) >= 2:
                    polylines.append(current)
                current = []
            else:
                current.append((cursor_x + pt[0], pt[1]))
        if len(current) >= 2:
            polylines.append(current)
        cursor_x += advance

    return polylines


def _ttf_recording_to_polylines(commands, scale):
    """
    Convert fontTools RecordingPen commands to polylines.
    All coordinates are multiplied by `scale` (normalises to ~10 units tall).
    Supports moveTo, lineTo, curveTo (cubic Bezier), qCurveTo (quadratic), closePath, endPath.
    """
    polylines = []
    current = None
    cur_pos = (0.0, 0.0)

    def add_pt(x, y):
        nonlocal cur_pos, current
        p = (x * scale, y * scale)
        if current is None:
            current = [cur_pos]
        current.append(p)
        cur_pos = p

    def flush():
        nonlocal current
        if current and len(current) >= 2:
            polylines.append(current)
        current = None

    for op, pts in commands:
        if op == 'moveTo':
            flush()
            x, y = pts[0]
            cur_pos = (x * scale, y * scale)

        elif op == 'lineTo':
            x, y = pts[0]
            add_pt(x, y)

        elif op == 'curveTo':
            # Cubic Bezier: control points pts[0], pts[1], endpoint pts[2]
            p0 = cur_pos
            p1 = (pts[0][0] * scale, pts[0][1] * scale)
            p2 = (pts[1][0] * scale, pts[1][1] * scale)
            p3 = (pts[2][0] * scale, pts[2][1] * scale)
            steps = 8
            for i in range(1, steps + 1):
                t = i / steps
                mt = 1.0 - t
                x = mt**3*p0[0] + 3*mt**2*t*p1[0] + 3*mt*t**2*p2[0] + t**3*p3[0]
                y = mt**3*p0[1] + 3*mt**2*t*p1[1] + 3*mt*t**2*p2[1] + t**3*p3[1]
                if current is None:
                    current = [cur_pos]
                pt = (x, y)
                current.append(pt)
                cur_pos = pt

        elif op == 'qCurveTo':
            # Quadratic (TrueType): may have implicit on-curve points between control pts
            p0 = cur_pos
            all_pts = [(p[0] * scale, p[1] * scale) for p in pts]
            endpoint = all_pts[-1]
            ctrl_pts = all_pts[:-1]

            segments = []
            if len(ctrl_pts) == 1:
                segments = [(p0, ctrl_pts[0], endpoint)]
            else:
                prev = p0
                for i, cp in enumerate(ctrl_pts):
                    if i == len(ctrl_pts) - 1:
                        on = endpoint
                    else:
                        on = ((cp[0] + ctrl_pts[i+1][0]) / 2,
                              (cp[1] + ctrl_pts[i+1][1]) / 2)
                    segments.append((prev, cp, on))
                    prev = on

            for (sp0, qcp, sp3) in segments:
                cp1 = (sp0[0] + 2/3*(qcp[0]-sp0[0]), sp0[1] + 2/3*(qcp[1]-sp0[1]))
                cp2 = (sp3[0] + 2/3*(qcp[0]-sp3[0]), sp3[1] + 2/3*(qcp[1]-sp3[1]))
                steps = 8
                for i in range(1, steps + 1):
                    t = i / steps
                    mt = 1.0 - t
                    x = mt**3*sp0[0] + 3*mt**2*t*cp1[0] + 3*mt*t**2*cp2[0] + t**3*sp3[0]
                    y = mt**3*sp0[1] + 3*mt**2*t*cp1[1] + 3*mt*t**2*cp2[1] + t**3*sp3[1]
                    if current is None:
                        current = [cur_pos]
                    pt = (x, y)
                    current.append(pt)
                    cur_pos = pt

        elif op in ('closePath', 'endPath'):
            flush()

    flush()
    return polylines


def _bounds(polylines):
    """Return (min_x, min_y, max_x, max_y) across all polylines, or None."""
    xs, ys = [], []
    for poly in polylines:
        for (x, y) in poly:
            xs.append(x); ys.append(y)
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)
