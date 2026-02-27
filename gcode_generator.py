"""
G-code Generator - Converts text to laser engraving G-code

Font engines:
  hershey_builtin  - built-in stroke font (always available)
  hershey_lib      - extended Hershey fonts via `hershey-fonts` package
  ttf              - any TTF/OTF font via `fontTools` package

Laser mode:
  Uses M4 (dynamic power) so G0 rapids auto-disable the laser and G1 cuts
  auto-enable it. No per-segment M3/M5 toggling needed.

Arc output (TTF engine):
  Bezier curve segments are converted to G2/G3 circular arcs so the
  motion planner executes each curve as a single continuous move.
  Straight segments and degenerate curves fall back to G1.

Z axis:
  If laser_settings.z_height_mm is non-zero, the generated G-code will
  move to that Z height before engraving begins and return to Z0 at the
  end.  Set to 0.0 (default) to disable Z motion entirely.
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


# ── Arc fitting helpers ───────────────────────────────────────

def _circumcenter(p0, p1, p2):
    ax, ay = p0
    bx, by = p1
    cx, cy = p2
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-10:
        return None
    ux = ((ax*ax + ay*ay) * (by - cy) +
          (bx*bx + by*by) * (cy - ay) +
          (cx*cx + cy*cy) * (ay - by)) / d
    uy = ((ax*ax + ay*ay) * (cx - bx) +
          (bx*bx + by*by) * (ax - cx) +
          (cx*cx + cy*cy) * (bx - ax)) / d
    return ux, uy


def _cross2d(ox, oy, ax, ay, bx, by):
    return (ax - ox) * (by - oy) - (ay - oy) * (bx - ox)


def _bezier_midpoint(p0, p1, p2, p3):
    t = 0.5
    mt = 1.0 - t
    x = mt**3*p0[0] + 3*mt**2*t*p1[0] + 3*mt*t**2*p2[0] + t**3*p3[0]
    y = mt**3*p0[1] + 3*mt**2*t*p1[1] + 3*mt*t**2*p2[1] + t**3*p3[1]
    return x, y


def _quad_midpoint(p0, cp, p3):
    t = 0.5
    mt = 1.0 - t
    x = mt**2*p0[0] + 2*mt*t*cp[0] + t**2*p3[0]
    y = mt**2*p0[1] + 2*mt*t*cp[1] + t**2*p3[1]
    return x, y


def _arc_cmd(start, end, center, ccw, feed):
    i = center[0] - start[0]
    j = center[1] - start[1]
    cmd = 'G3' if ccw else 'G2'
    return f'{cmd} X{end[0]:.4f} Y{end[1]:.4f} I{i:.4f} J{j:.4f} F{feed}'


def _bezier_to_arc_or_lines(p0, p1, p2, p3, scale, feed):
    MIN_RADIUS  = 0.05
    MAX_ARC_ERR = 0.08

    sp = (p0[0] * scale, p0[1] * scale)
    ep = (p3[0] * scale, p3[1] * scale)
    mid = _bezier_midpoint(
        (p0[0]*scale, p0[1]*scale),
        (p1[0]*scale, p1[1]*scale),
        (p2[0]*scale, p2[1]*scale),
        (p3[0]*scale, p3[1]*scale)
    )

    seg_len = math.hypot(ep[0]-sp[0], ep[1]-sp[1])
    if seg_len < 1e-6:
        return []

    center = _circumcenter(sp, mid, ep)
    if center is None:
        return [f'G1 X{ep[0]:.4f} Y{ep[1]:.4f} F{feed}']

    radius = math.hypot(sp[0]-center[0], sp[1]-center[1])
    if radius < MIN_RADIUS:
        return [f'G1 X{ep[0]:.4f} Y{ep[1]:.4f} F{feed}']

    arc_mid_r = math.hypot(mid[0]-center[0], mid[1]-center[1])
    err = abs(arc_mid_r - radius)
    if err > MAX_ARC_ERR:
        t = 0.5
        mt = 1.0 - t
        q0 = p0
        q1 = (mt*p0[0]+t*p1[0], mt*p0[1]+t*p1[1])
        q2 = (mt*q1[0]+t*(mt*p1[0]+t*p2[0]), mt*q1[1]+t*(mt*p1[1]+t*p2[1]))
        r2 = (mt*p2[0]+t*p3[0], mt*p2[1]+t*p3[1])
        r1 = (mt*(mt*p1[0]+t*p2[0])+t*r2[0], mt*(mt*p1[1]+t*p2[1])+t*r2[1])
        mid_pt = (mt*q2[0]+t*r1[0], mt*q2[1]+t*r1[1])
        r0 = mid_pt
        cmds = []
        cmds += _bezier_to_arc_or_lines(q0, q1, q2, r0, 1.0, feed)
        cmds += _bezier_to_arc_or_lines(r0, r1, r2, p3, 1.0, feed)
        return cmds

    cross = _cross2d(sp[0], sp[1], mid[0], mid[1], ep[0], ep[1])
    ccw = cross > 0
    return [_arc_cmd(sp, ep, center, ccw, feed)]


def _quad_to_arc_or_lines(p0, cp, p3, scale, feed):
    MIN_RADIUS  = 0.05
    MAX_ARC_ERR = 0.08

    sp  = (p0[0] * scale, p0[1] * scale)
    ep  = (p3[0] * scale, p3[1] * scale)
    cps = (cp[0] * scale, cp[1] * scale)
    mid = _quad_midpoint(sp, cps, ep)

    seg_len = math.hypot(ep[0]-sp[0], ep[1]-sp[1])
    if seg_len < 1e-6:
        return []

    center = _circumcenter(sp, mid, ep)
    if center is None:
        return [f'G1 X{ep[0]:.4f} Y{ep[1]:.4f} F{feed}']

    radius = math.hypot(sp[0]-center[0], sp[1]-center[1])
    if radius < MIN_RADIUS:
        return [f'G1 X{ep[0]:.4f} Y{ep[1]:.4f} F{feed}']

    arc_mid_r = math.hypot(mid[0]-center[0], mid[1]-center[1])
    err = abs(arc_mid_r - radius)
    if err > MAX_ARC_ERR:
        t = 0.5
        mt = 1.0 - t
        mid_pt = _quad_midpoint(sp, cps, ep)
        cp1 = ((sp[0]+cps[0])*0.5, (sp[1]+cps[1])*0.5)
        cp2 = ((cps[0]+ep[0])*0.5, (cps[1]+ep[1])*0.5)
        cmds = []
        cmds += _quad_to_arc_or_lines(
            (sp[0]/scale,  sp[1]/scale),
            (cp1[0]/scale, cp1[1]/scale),
            (mid_pt[0]/scale, mid_pt[1]/scale), scale, feed)
        cmds += _quad_to_arc_or_lines(
            (mid_pt[0]/scale, mid_pt[1]/scale),
            (cp2[0]/scale, cp2[1]/scale),
            (p3[0], p3[1]), scale, feed)
        return cmds

    cross = _cross2d(sp[0], sp[1], mid[0], mid[1], ep[0], ep[1])
    ccw = cross > 0
    return [_arc_cmd(sp, ep, center, ccw, feed)]


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
        Generate M4-mode G-code.
        Clears Work Coordinate Offsets via G10 L2 P1 to ensure WPos=MPos.
        Returns (gcode_string, actual_width_mm, actual_height_mm).
        """
        path, width, height = self._build_geometry(
            text, text_height_mm, origin=(x_start, y_start))
        s_on = self._s_value()

        # Read Z height from live config, supporting both height and depth naming for backwards compat
        z_height = float(config.get('laser_settings.z_height_mm', config.get('laser_settings.z_depth_mm', 0.0)))
        use_z    = abs(z_height) > 1e-4

        gc = [
            f'; Engrave: {text}',
            f'; Font={self.font_key}  Power={self.laser_power}%  S={s_on}/{self.spindle_max}  Feed={self.speed}',
            'G21',                  # mm mode
            'G10 L2 P1 X0 Y0 Z0',   # CRITICAL: Clear Work Coordinate Offset so WPos matches MPos
            'G54',                  # Ensure we are using the G54 workspace we just cleared
            'G90',                  # Absolute positioning
        ]

        if use_z:
            gc.append(f'; Z height: {z_height:.3f} mm')
            gc.append('G0 Z0')      # Ensure we start at the bottom (0)

        gc += [
            f'M4 S{s_on}',
            '',
        ]

        if use_z:
            gc.append(f'G0 Z{z_height:.4f}')   # Move up to the requested Z height
            gc.append('')

        for p in range(passes):
            gc.append(f'; Pass {p+1}/{passes}')
            for entry in path:
                gc.append(entry)
            gc.append('')

        gc.append('M5')   # laser off

        if use_z:
            gc.append('G0 Z0')      # Return to the bottom safely

        gc += ['G0 X0 Y0', 'M2']

        return '\n'.join(gc), width, height

    # ── Geometry pipeline ─────────────────────────────────────
    def _build_geometry(self, text, text_height_mm, origin=(0.0, 0.0)):
        if self.engine == 'ttf' and _FONTTOOLS_AVAILABLE and self.ttf_path:
            return self._build_ttf_geometry(text, text_height_mm, origin)
        return self._build_polyline_geometry(text, text_height_mm, origin)

    def _build_ttf_geometry(self, text, text_height_mm, origin):
        try:
            if self._ttfont is None:
                self._ttfont = _TTFont(self.ttf_path)
                self._glyph_set = self._ttfont.getGlyphSet()
                self._cmap = self._ttfont.getBestCmap() or {}
                self._units_per_em = self._ttfont['head'].unitsPerEm
        except Exception as e:
            debug_print(f'TTF load error: {e}, using builtin')
            return self._build_polyline_geometry(text, text_height_mm, origin)

        scale = text_height_mm / self._units_per_em
        ox, oy = origin

        path = []
        all_x = []
        all_y = []
        cursor_x_u = 0.0

        for ch in text:
            glyph_name = self._cmap.get(ord(ch))
            if not glyph_name:
                cursor_x_u += self._units_per_em * 0.6
                continue

            if glyph_name not in self._glyph_cache:
                pen = _RecordingPen()
                self._glyph_set[glyph_name].draw(pen)
                adv = self._glyph_set[glyph_name].width
                self._glyph_cache[glyph_name] = (pen.value, adv)

            cmds_raw, advance_u = self._glyph_cache[glyph_name]

            glyph_cmds, gx, gy = _pen_to_gcode(
                cmds_raw, scale, cursor_x_u, ox, oy, self.speed)
            path.extend(glyph_cmds)
            all_x.extend(gx)
            all_y.extend(gy)

            cursor_x_u += advance_u

        if not all_x:
            return [], 0.0, 0.0

        width  = max(all_x) - min(all_x)
        height = max(all_y) - min(all_y)
        return path, width, height

    def _build_polyline_geometry(self, text, text_height_mm, origin):
        polylines = self._glyphs_for_text(text)
        if not polylines:
            return [], 0.0, 0.0

        b = _bounds(polylines)
        if b is None:
            return [], 0.0, 0.0

        flipped = [[(x, -y) for (x, y) in poly] for poly in polylines]
        b2 = _bounds(flipped)
        min_x, min_y, max_x, max_y = b2
        units_height = (max_y - min_y) or 1.0

        scale = text_height_mm / units_height
        ox, oy = origin

        scaled = []
        for poly in flipped:
            pts = [(ox + (x - min_x) * scale,
                    oy + (y - min_y) * scale)
                   for (x, y) in poly]
            scaled.append(pts)

        if self.line_width_mm > 0:
            expanded = []
            for poly in scaled:
                for i in range(len(poly) - 1):
                    x1, y1 = poly[i]
                    x2, y2 = poly[i + 1]
                    for seg in self._bold_passes(x1, y1, x2, y2):
                        expanded.append([seg[:2], seg[2:]])
            scaled = expanded

        path = []
        for poly in scaled:
            if not poly:
                continue
            x0, y0 = poly[0]
            path.append(f'G0 X{x0:.4f} Y{y0:.4f}')
            for (x, y) in poly[1:]:
                path.append(f'G1 X{x:.4f} Y{y:.4f} F{self.speed}')

        b3 = _bounds(scaled)
        if b3 is None:
            return path, 0.0, text_height_mm
        bx0, by0, bx1, by1 = b3
        return path, (bx1 - bx0), (by1 - by0)

    def _bold_passes(self, x1, y1, x2, y2):
        step = 0.15
        n = max(1, round(self.line_width_mm / step))
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length < 1e-6:
            yield (x1, y1, x2, y2)
            return
        px, py = -dy / length, dx / length
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
            return _builtin_glyphs(text)
        return _builtin_glyphs(text)

    def _hershey_lib_glyphs(self, text):
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


# ── TTF pen-recording → G-code with G2/G3 arcs ───────────────

def _pen_to_gcode(commands, scale, cursor_x_u, ox, oy, feed):
    def tx(x):  return ox + (cursor_x_u + x) * scale
    def ty(y):  return oy + (-y) * scale

    gcode  = []
    all_x  = []
    all_y  = []
    cur    = None

    def move_to(mx, my):
        nonlocal cur
        gcode.append(f'G0 X{mx:.4f} Y{my:.4f}')
        all_x.append(mx);  all_y.append(my)
        cur = (mx, my)

    def line_to(mx, my):
        nonlocal cur
        gcode.append(f'G1 X{mx:.4f} Y{my:.4f} F{feed}')
        all_x.append(mx);  all_y.append(my)
        cur = (mx, my)

    for op, pts in commands:
        if op == 'moveTo':
            x, y = pts[0]
            move_to(tx(x), ty(y))

        elif op == 'lineTo':
            x, y = pts[0]
            line_to(tx(x), ty(y))

        elif op == 'curveTo':
            if cur is None:
                continue
            mp0 = cur
            mp1 = (tx(pts[0][0]), ty(pts[0][1]))
            mp2 = (tx(pts[1][0]), ty(pts[1][1]))
            mp3 = (tx(pts[2][0]), ty(pts[2][1]))

            arc_cmds = _cubic_to_arc_or_lines_machine(mp0, mp1, mp2, mp3, feed)
            for ac in arc_cmds:
                gcode.append(ac)
            all_x.append(mp3[0]);  all_y.append(mp3[1])
            cur = mp3

        elif op == 'qCurveTo':
            if cur is None:
                continue
            all_mpts = [(tx(p[0]), ty(p[1])) for p in pts]
            endpoint  = all_mpts[-1]
            ctrl_mpts = all_mpts[:-1]

            segments = []
            prev = cur
            for i, cp in enumerate(ctrl_mpts):
                if i == len(ctrl_mpts) - 1:
                    on = endpoint
                else:
                    on = ((cp[0] + ctrl_mpts[i+1][0]) / 2,
                          (cp[1] + ctrl_mpts[i+1][1]) / 2)
                segments.append((prev, cp, on))
                prev = on

            for (sp, qcp, ep) in segments:
                arc_cmds = _quad_to_arc_or_lines_machine(sp, qcp, ep, feed)
                for ac in arc_cmds:
                    gcode.append(ac)
                all_x.append(ep[0]);  all_y.append(ep[1])
                cur = ep

        elif op in ('closePath', 'endPath'):
            pass

    return gcode, all_x, all_y


def _cubic_to_arc_or_lines_machine(p0, p1, p2, p3, feed):
    MIN_RADIUS  = 0.05
    MAX_ARC_ERR = 0.08

    seg_len = math.hypot(p3[0]-p0[0], p3[1]-p0[1])
    if seg_len < 1e-6:
        return []

    mid = _bezier_midpoint(p0, p1, p2, p3)
    center = _circumcenter(p0, mid, p3)
    if center is None:
        return [f'G1 X{p3[0]:.4f} Y{p3[1]:.4f} F{feed}']

    radius = math.hypot(p0[0]-center[0], p0[1]-center[1])
    if radius < MIN_RADIUS:
        return [f'G1 X{p3[0]:.4f} Y{p3[1]:.4f} F{feed}']

    arc_mid_r = math.hypot(mid[0]-center[0], mid[1]-center[1])
    if abs(arc_mid_r - radius) > MAX_ARC_ERR:
        q1 = ((p0[0]+p1[0])*0.5, (p0[1]+p1[1])*0.5)
        r1 = ((p1[0]+p2[0])*0.5, (p1[1]+p2[1])*0.5)
        r2 = ((p2[0]+p3[0])*0.5, (p2[1]+p3[1])*0.5)
        q2 = ((q1[0]+r1[0])*0.5, (q1[1]+r1[1])*0.5)
        r0 = ((r1[0]+r2[0])*0.5, (r1[1]+r2[1])*0.5)
        mid_pt = ((q2[0]+r0[0])*0.5, (q2[1]+r0[1])*0.5)
        return (_cubic_to_arc_or_lines_machine(p0, q1, q2, mid_pt, feed) +
                _cubic_to_arc_or_lines_machine(mid_pt, r0, r2, p3, feed))

    cross = _cross2d(p0[0], p0[1], mid[0], mid[1], p3[0], p3[1])
    ccw = cross > 0
    return [_arc_cmd(p0, p3, center, ccw, feed)]


def _quad_to_arc_or_lines_machine(p0, cp, p3, feed):
    MIN_RADIUS  = 0.05
    MAX_ARC_ERR = 0.08

    seg_len = math.hypot(p3[0]-p0[0], p3[1]-p0[1])
    if seg_len < 1e-6:
        return []

    mid = _quad_midpoint(p0, cp, p3)
    center = _circumcenter(p0, mid, p3)
    if center is None:
        return [f'G1 X{p3[0]:.4f} Y{p3[1]:.4f} F{feed}']

    radius = math.hypot(p0[0]-center[0], p0[1]-center[1])
    if radius < MIN_RADIUS:
        return [f'G1 X{p3[0]:.4f} Y{p3[1]:.4f} F{feed}']

    arc_mid_r = math.hypot(mid[0]-center[0], mid[1]-center[1])
    if abs(arc_mid_r - radius) > MAX_ARC_ERR:
        cp1 = ((p0[0]+cp[0])*0.5, (p0[1]+cp[1])*0.5)
        cp2 = ((cp[0]+p3[0])*0.5, (cp[1]+p3[1])*0.5)
        mid_pt = ((cp1[0]+cp2[0])*0.5, (cp1[1]+cp2[1])*0.5)
        return (_quad_to_arc_or_lines_machine(p0, cp1, mid_pt, feed) +
                _quad_to_arc_or_lines_machine(mid_pt, cp2, p3, feed))

    cross = _cross2d(p0[0], p0[1], mid[0], mid[1], p3[0], p3[1])
    ccw = cross > 0
    return [_arc_cmd(p0, p3, center, ccw, feed)]


# ── Module-level helpers ──────────────────────────────────────

def _builtin_glyphs(text):
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


def _bounds(polylines):
    xs, ys = [], []
    for poly in polylines:
        for (x, y) in poly:
            xs.append(x); ys.append(y)
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)
