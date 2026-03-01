"""
GCode Generator - Converts text + layout rectangles into standard G-Code
Supports standard TTF vector fonts via freetype-py.
"""

import os
import math
from freetype import Face, FT_CURVE_TAG_ON, FT_CURVE_TAG_CONIC, FT_CURVE_TAG_CUBIC

from config import config, debug_print

def _scan_for_fonts(fonts_dir='fonts'):
    """Scans the given directory for TTF files and builds a dictionary profile."""
    # We define the system paths where common fonts actually exist on Ubuntu/Debian.
    # The dictionary maps internal key -> (Display Name, line_width, engine, disk_path)
    profiles = {
        # DejaVu is installed on almost all Linux distros by default
        'simplex': ('Simplex (Standard Sans)', 0.4, 'ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
        'times':   ('Times (Serif)', 0.5, 'ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf'),
        'arial':   ('Arial (Sans-serif)', 0.5, 'ttf', '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf'),
        'cursive': ('Cursive (Ubuntu Italic)', 0.3, 'ttf', '/usr/share/fonts/truetype/ubuntu/Ubuntu-Italic.ttf'),
        'impact':  ('Impact (Ubuntu Bold)', 0.6, 'ttf', '/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf'),
    }
    
    # Also scan the local 'fonts/' folder for any custom user uploads
    if os.path.exists(fonts_dir):
        for filename in os.listdir(fonts_dir):
            if filename.lower().endswith('.ttf'):
                key = filename[:-4].lower()
                path = os.path.join(fonts_dir, filename)
                
                # If they upload a custom font matching a default name (like 'arial.ttf'),
                # override the system path with their local custom file.
                if key in profiles:
                    old = profiles[key]
                    profiles[key] = (old[0], old[1], old[2], path)
                else:
                    label = filename[:-4].replace('_', ' ').title()
                    profiles[key] = (label, 0.5, 'ttf', path)
                    
    return profiles

FONT_PROFILES = _scan_for_fonts()

# ── Arc fitting helpers ───────────────────────────────────────
def _circumcenter(p0, p1, p2):
    """
    Return the circumcenter (cx, cy) of the triangle formed by three points,
    or None if the points are collinear (no unique circle exists).
    """
    ax, ay = p0
    bx, by = p1
    cx, cy = p2
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-10:
        return None   # collinear
    ux = ((ax*ax + ay*ay) * (by - cy) +
          (bx*bx + by*by) * (cy - ay) +
          (cx*cx + cy*cy) * (ay - by)) / d
    uy = ((ax*ax + ay*ay) * (cx - bx) +
          (bx*bx + by*by) * (ax - cx) +
          (cx*cx + cy*cy) * (bx - ax)) / d
    return ux, uy

def _cross2d(ox, oy, ax, ay, bx, by):
    """2-D cross product of vectors (o→a) and (o→b)."""
    return (ax - ox) * (by - oy) - (ay - oy) * (bx - ox)

def _bezier_midpoint(p0, p1, p2, p3):
    """Midpoint of a cubic Bezier at t=0.5."""
    t = 0.5
    mt = 1.0 - t
    x = mt**3*p0[0] + 3*mt**2*t*p1[0] + 3*mt*t**2*p2[0] + t**3*p3[0]
    y = mt**3*p0[1] + 3*mt**2*t*p1[1] + 3*mt*t**2*p2[1] + t**3*p3[1]
    return x, y

def _quad_midpoint(p0, cp, p3):
    """Midpoint of a quadratic Bezier at t=0.5."""
    t = 0.5
    mt = 1.0 - t
    x = mt**2*p0[0] + 2*mt*t*cp[0] + t**2*p3[0]
    y = mt**2*p0[1] + 2*mt*t*cp[1] + t**2*p3[1]
    return x, y

def _arc_cmd(start, end, center, ccw, feed):
    """
    Build a G2/G3 arc command string.
    """
    i = center[0] - start[0]
    j = center[1] - start[1]
    cmd = 'G3' if ccw else 'G2'
    return f'{cmd} X{end[0]:.3f} Y{end[1]:.3f} I{i:.3f} J{j:.3f} F{feed}' 

def _quad_to_arc_or_lines_machine(p0, cp, p3, feed):
    """
    Fit a G2/G3 arc to a quadratic Bezier in machine coordinates.
    """
    MIN_RADIUS  = 0.05
    MAX_ARC_ERR = 0.08

    seg_len = math.hypot(p3[0]-p0[0], p3[1]-p0[1])
    if seg_len < 1e-6:
        return []

    mid = _quad_midpoint(p0, cp, p3)
    center = _circumcenter(p0, mid, p3)
    if center is None:
        return [f'G1 X{p3[0]:.3f} Y{p3[1]:.3f} F{feed}']

    radius = math.hypot(p0[0]-center[0], p0[1]-center[1])
    if radius < MIN_RADIUS:
        return [f'G1 X{p3[0]:.3f} Y{p3[1]:.3f} F{feed}']

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

def _cubic_to_arc_or_lines_machine(p0, p1, p2, p3, feed):
    """
    Fit a G2/G3 arc to a cubic Bezier given in machine coordinates.
    """
    MIN_RADIUS  = 0.05
    MAX_ARC_ERR = 0.08

    seg_len = math.hypot(p3[0]-p0[0], p3[1]-p0[1])
    if seg_len < 1e-6:
        return []

    mid = _bezier_midpoint(p0, p1, p2, p3)
    center = _circumcenter(p0, mid, p3)
    if center is None:
        return [f'G1 X{p3[0]:.3f} Y{p3[1]:.3f} F{feed}']

    radius = math.hypot(p0[0]-center[0], p0[1]-center[1])
    if radius < MIN_RADIUS:
        return [f'G1 X{p3[0]:.3f} Y{p3[1]:.3f} F{feed}']

    arc_mid_r = math.hypot(mid[0]-center[0], mid[1]-center[1])
    if abs(arc_mid_r - radius) > MAX_ARC_ERR:
        # Split with De Casteljau at t=0.5
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


class GCodeGenerator:
    def __init__(self):
        self._face = None
        self._glyph_cache = {}
        self._current_font_path = None
        self.offset_x = 0.0
        self.offset_y = 0.0
        self._load_settings()

    def _load_settings(self):
        """Loads settings from config and updates font face if needed"""
        s = config.get('laser_settings', {})
        self.laser_power = s.get('power_percent', 40.0)
        self.speed       = s.get('speed_mm_per_min', 800)
        self.spindle_max = s.get('spindle_max', 1000)
        self.focal_height = s.get('z_height_mm', s.get('z_depth_mm', 0.0))

        t = config.get('text_settings', {})
        self.font_key = t.get('font', 'arial')

        global FONT_PROFILES
        FONT_PROFILES = _scan_for_fonts()

        if self.font_key not in FONT_PROFILES:
            debug_print(f"Font '{self.font_key}' not found in profiles, falling back to 'arial'")
            self.font_key = 'arial'
            
        profile = FONT_PROFILES.get(self.font_key, ('Arial', 0.5, 'ttf', '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf'))
        self.line_width_mm = profile[1]
        self.engine        = profile[2]
        
        if len(profile) > 3:
            new_ttf_path = profile[3]
        else:
            new_ttf_path = t.get('ttf_path', f'/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf')

        # If font changed, clear cache and trigger reload
        if self._current_font_path != new_ttf_path:
            debug_print(f"Font path change detected! Purging cache. Old: {self._current_font_path}, New: {new_ttf_path}")
            self.ttf_path = new_ttf_path
            self._current_font_path = new_ttf_path
            self._face = None
            self._glyph_cache = {}

    def _init_font(self):
        """Lazy load TTF font Face"""
        if not self._face:
            debug_print(f"Attempting to initialize font at: {self.ttf_path}")
            if os.path.exists(self.ttf_path):
                try:
                    self._face = Face(self.ttf_path)
                    debug_print(f"Successfully loaded font: {self.ttf_path}")
                except Exception as e:
                    debug_print(f"Freetype error loading {self.ttf_path}: {e}")
            else:
                debug_print(f"TTF font NOT FOUND on disk at {self.ttf_path}.")
                alt_paths = [
                    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
                    '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
                    '/usr/share/fonts/truetype/freefont/FreeSans.ttf'
                ]
                for p in alt_paths:
                    if os.path.exists(p):
                        try:
                            self._face = Face(p)
                            self.ttf_path = p
                            debug_print(f"Successfully loaded fallback font: {p}")
                            break
                        except Exception as e:
                            debug_print(f"Freetype error loading fallback {p}: {e}")

    def _get_ttf_commands(self, text, height):
        """
        Extracts native Bezier commands from freetype-py outlines.
        Returns unscaled, un-offset commands.
        """
        self._init_font()
        if not self._face:
            debug_print("ERROR: No valid TrueType font available to render text. Font Face is None.")
            return [], 1.0, 0, 0

        self._face.set_char_size(48 * 64)
        
        commands = []
        cursor_x = 0.0
        max_y = -999999
        min_y = 999999
        valid_chars_found = False

        for char in text:
            if char not in self._glyph_cache:
                self._face.load_char(char)
                slot = self._face.glyph
                outline = slot.outline
                
                char_commands = []
                start = 0
                for end in outline.contours:
                    points = outline.points[start:end+1]
                    tags = outline.tags[start:end+1]
                    
                    if not points:
                        start = end + 1
                        continue
                        
                    first_on = 0
                    for i in range(len(tags)):
                        if tags[i] & 1:
                            first_on = i
                            break
                    else:
                        start = end + 1
                        continue
                        
                    points = points[first_on:] + points[:first_on]
                    tags = tags[first_on:] + tags[:first_on]
                    
                    char_commands.append(('moveTo', points[0]))
                    
                    i = 1
                    while i < len(points):
                        tag = tags[i]
                        pt = points[i]
                        is_on = (tag & 1)
                        is_cubic = (tag & 2)
                        
                        if is_on:
                            char_commands.append(('lineTo', pt))
                            i += 1
                        elif not is_cubic:
                            cp = pt
                            i += 1
                            if i < len(points):
                                next_tag = tags[i]
                                next_pt = points[i]
                                if (next_tag & 1):
                                    char_commands.append(('qCurveTo', cp, next_pt))
                                    i += 1
                                else:
                                    mid_pt = ((cp[0] + next_pt[0]) / 2.0, (cp[1] + next_pt[1]) / 2.0)
                                    char_commands.append(('qCurveTo', cp, mid_pt))
                            else:
                                char_commands.append(('qCurveTo', cp, points[0]))
                        else:
                            cp1 = pt
                            if i + 2 < len(points):
                                cp2 = points[i+1]
                                end_pt = points[i+2]
                                char_commands.append(('curveTo', cp1, cp2, end_pt))
                                i += 3
                            else:
                                cp2 = points[i+1] if i + 1 < len(points) else points[0]
                                char_commands.append(('curveTo', cp1, cp2, points[0]))
                                break
                    
                    char_commands.append(('lineTo', points[0]))
                    start = end + 1

                advance = slot.advance.x
                self._glyph_cache[char] = (char_commands, advance)

            char_commands, advance = self._glyph_cache[char]
            
            if char_commands:
                valid_chars_found = True
            
            for cmd in char_commands:
                op = cmd[0]
                if op in ('moveTo', 'lineTo'):
                    pt = cmd[1]
                    shifted = (pt[0] + cursor_x, pt[1])
                    if shifted[1] > max_y: max_y = shifted[1]
                    if shifted[1] < min_y: min_y = shifted[1]
                    commands.append((op, shifted))
                elif op == 'qCurveTo':
                    cp = (cmd[1][0] + cursor_x, cmd[1][1])
                    ep = (cmd[2][0] + cursor_x, cmd[2][1])
                    if cp[1] > max_y: max_y = cp[1]
                    if cp[1] < min_y: min_y = cp[1]
                    if ep[1] > max_y: max_y = ep[1]
                    if ep[1] < min_y: min_y = ep[1]
                    commands.append((op, cp, ep))
                elif op == 'curveTo':
                    cp1 = (cmd[1][0] + cursor_x, cmd[1][1])
                    cp2 = (cmd[2][0] + cursor_x, cmd[2][1])
                    ep  = (cmd[3][0] + cursor_x, cmd[3][1])
                    if ep[1] > max_y: max_y = ep[1]
                    if ep[1] < min_y: min_y = ep[1]
                    commands.append((op, cp1, cp2, ep))
                    
            cursor_x += advance

        if not valid_chars_found:
            debug_print(f"WARNING: The font '{self.font_key}' generated no visible contours for the text '{text}'.")
            return [], 1.0, 0, 0

        raw_height = max_y - min_y
        if raw_height < 1e-5:
            return [], 1.0, 0, 0
            
        scale = height / raw_height
        return commands, scale, min_y, cursor_x * scale

    def _get_bold_offsets(self, repeats, offset_mm, pattern):
        """Calculate X/Y translation vectors for classic bolding/repeating passes"""
        offsets = [(0.0, 0.0)]
        if repeats <= 1:
            return offsets
            
        if pattern == 'circle':
            for i in range(1, repeats):
                angle = (i - 1) * (2 * math.pi / (repeats - 1))
                offsets.append((math.cos(angle) * offset_mm, math.sin(angle) * offset_mm))
        else:
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

    def _get_concentric_offsets(self, repeats, offset_mm):
        """Calculate morphological inset/outset magnitudes"""
        if repeats <= 1:
            return [0.0]
        offsets = [0.0]
        for i in range(1, repeats):
            # i=1: +1*offset, i=2: -1*offset, i=3: +2*offset, i=4: -2*offset
            sign = 1 if i % 2 == 1 else -1
            step = (i + 1) // 2
            offsets.append(sign * step * offset_mm)
        return offsets

    def _compute_normals(self, commands):
        """
        Calculates the vertex normal for every point in a command stream 
        to allow concentric morphological offsetting.
        """
        pt_refs = []
        pts = []
        for c_idx, cmd in enumerate(commands):
            op = cmd[0]
            if op in ('moveTo', 'lineTo'):
                pts.append(cmd[1])
                pt_refs.append((c_idx, 1))
            elif op == 'qCurveTo':
                pts.append(cmd[1])
                pt_refs.append((c_idx, 1))
                pts.append(cmd[2])
                pt_refs.append((c_idx, 2))
            elif op == 'curveTo':
                pts.append(cmd[1])
                pt_refs.append((c_idx, 1))
                pts.append(cmd[2])
                pt_refs.append((c_idx, 2))
                pts.append(cmd[3])
                pt_refs.append((c_idx, 3))
                
        contours = []
        curr = []
        for i, (c_idx, _) in enumerate(pt_refs):
            if commands[c_idx][0] == 'moveTo':
                if curr: contours.append(curr)
                curr = [i]
            else:
                curr.append(i)
        if curr: contours.append(curr)
        
        normals = [(0.0, 0.0)] * len(pts)
        for contour_indices in contours:
            N = len(contour_indices)
            if N < 2: continue
            
            first_p = pts[contour_indices[0]]
            last_p = pts[contour_indices[-1]]
            is_closed = math.hypot(first_p[0]-last_p[0], first_p[1]-last_p[1]) < 1e-5
            
            for i in range(N):
                curr_i = contour_indices[i]
                p_curr = pts[curr_i]
                
                p_prev = None
                for step in range(1, N):
                    prev_i = contour_indices[(i - step) % N] if is_closed else contour_indices[max(0, i - step)]
                    if math.hypot(pts[prev_i][0] - p_curr[0], pts[prev_i][1] - p_curr[1]) > 1e-5:
                        p_prev = pts[prev_i]
                        break
                if not p_prev: p_prev = p_curr
                
                p_next = None
                for step in range(1, N):
                    next_i = contour_indices[(i + step) % N] if is_closed else contour_indices[min(N-1, i + step)]
                    if math.hypot(pts[next_i][0] - p_curr[0], pts[next_i][1] - p_curr[1]) > 1e-5:
                        p_next = pts[next_i]
                        break
                if not p_next: p_next = p_curr
                
                d1x = p_curr[0] - p_prev[0]
                d1y = p_curr[1] - p_prev[1]
                L1 = math.hypot(d1x, d1y)
                n1x = d1x/L1 if L1 > 0 else 0.0
                n1y = d1y/L1 if L1 > 0 else 0.0
                
                d2x = p_next[0] - p_curr[0]
                d2y = p_next[1] - p_curr[1]
                L2 = math.hypot(d2x, d2y)
                n2x = d2x/L2 if L2 > 0 else 0.0
                n2y = d2y/L2 if L2 > 0 else 0.0
                
                tx = n1x + n2x
                ty = n1y + n2y
                Lt = math.hypot(tx, ty)
                if Lt > 1e-5:
                    tx /= Lt
                    ty /= Lt
                else:
                    tx, ty = -n1y, n1x
                    
                nx, ny = -ty, tx
                
                dot = n1x*n2x + n1y*n2y
                denom = math.sqrt(max(0.001, (1.0 + dot) / 2.0))
                miter = 1.0 / denom
                miter = min(miter, 2.0)
                
                normals[curr_i] = (nx * miter, ny * miter)
                
        normal_cmds = []
        curr_pt_idx = 0
        for cmd in commands:
            op = cmd[0]
            if op in ('moveTo', 'lineTo'):
                normal_cmds.append((op, normals[curr_pt_idx]))
                curr_pt_idx += 1
            elif op == 'qCurveTo':
                normal_cmds.append((op, normals[curr_pt_idx], normals[curr_pt_idx+1]))
                curr_pt_idx += 2
            elif op == 'curveTo':
                normal_cmds.append((op, normals[curr_pt_idx], normals[curr_pt_idx+1], normals[curr_pt_idx+2]))
                curr_pt_idx += 3
                
        return normal_cmds

    def generate(self, text, box_x, box_y, box_w, box_h, orientation='horizontal'):
        """
        Generates standard FluidNC/GRBL compatible G-code for the text inside the bounding box.
        """
        self._load_settings()
        
        s = config.get('laser_settings', {})
        t = config.get('text_settings', {})
        
        passes         = int(s.get('passes', 1))
        bold_repeats   = int(t.get('bold_repeats', 1))
        bold_offset_mm = float(t.get('bold_offset_mm', 0.15))
        bold_pattern   = t.get('bold_pattern', 'cross')
        mirror_y       = t.get('mirror_y', False)
        
        s_val = int((self.laser_power / 100.0) * self.spindle_max)

        # 1. Extract vector geometry
        raw_commands, scale, min_y_raw, raw_w_scaled = self._get_ttf_commands(text, box_h)

        if not raw_commands:
            return "; Error: No paths generated"

        # 2. Scale and Justify into the target Bounding Box
        final_scale = 1.0
        if raw_w_scaled > box_w:
            final_scale = box_w / raw_w_scaled
            
        active_scale = scale * final_scale

        final_w = raw_w_scaled * final_scale
        final_h = box_h * final_scale
        
        offset_x = box_x + (box_w - final_w) / 2.0
        offset_y = box_y + (box_h - final_h) / 2.0

        # Apply global job offsets (if any)
        offset_x += self.offset_x
        offset_y += self.offset_y

        if bold_pattern == 'concentric':
            offset_amounts = self._get_concentric_offsets(bold_repeats, bold_offset_mm)
            offsets = [(0.0, 0.0)] * bold_repeats
            normal_cmds = self._compute_normals(raw_commands)
        else:
            offset_amounts = [0.0] * bold_repeats
            offsets = self._get_bold_offsets(bold_repeats, bold_offset_mm, bold_pattern)
            normal_cmds = None

        def _tx(pt, norm_vec, amt, bx, by):
            mx = pt[0] * active_scale
            my = (pt[1] - min_y_raw) * active_scale
            
            if mirror_y:
                my = (box_h / final_scale - (pt[1] - min_y_raw)) * active_scale
                
            # Apply concentric morphological offset
            nx = norm_vec[0] * amt
            ny = norm_vec[1] * amt
            
            if mirror_y:
                ny = -ny
                
            return (mx + offset_x + bx + nx, my + offset_y + by + ny)

        gcode = [
            f"; TwitchLaser Engrave: '{text}'",
            "; Engine: " + self.engine,
            "; Bounding Box: X{:.1f} Y{:.1f} W{:.1f} H{:.1f}".format(box_x, box_y, box_w, box_h),
            f"; Passes: {passes} | Bold Repeats: {bold_repeats} ({bold_pattern})",
            "G21 ; Millimeters",
            "G90 ; Absolute positioning",
            "M5  ; Ensure laser is off",
            f"G0 Z{self.focal_height:.4f} ; Move to physical focus height before XY movement",
        ]

        # 4. G-Code generation loop
        for p in range(passes):
            for b_idx in range(bold_repeats):
                bx, by = offsets[b_idx]
                amt = offset_amounts[b_idx]
                
                if passes > 1 or bold_repeats > 1:
                    if bold_pattern == 'concentric':
                        gcode.append(f"; --- Pass {p+1}/{passes} | Concentric Offset {b_idx+1}/{bold_repeats} (Shift: {amt:+.3f}mm) ---")
                    else:
                        gcode.append(f"; --- Pass {p+1}/{passes} | Bold Offset {b_idx+1}/{bold_repeats} (dX:{bx:.3f} dY:{by:.3f}) ---")
                
                current_pos = None
                
                for c_idx, cmd in enumerate(raw_commands):
                    op = cmd[0]
                    n_cmd = normal_cmds[c_idx] if normal_cmds else None
                    
                    def _get_n(idx):
                        return n_cmd[idx] if n_cmd else (0.0, 0.0)
                    
                    if op == 'moveTo':
                        mpt = _tx(cmd[1], _get_n(1), amt, bx, by)
                        gcode.append(f"G0 X{mpt[0]:.3f} Y{mpt[1]:.3f}")
                        gcode.append(f"M4 S{s_val}") # Dynamic laser mode activates
                        current_pos = mpt
                        
                    elif op == 'lineTo':
                        mpt = _tx(cmd[1], _get_n(1), amt, bx, by)
                        gcode.append(f"G1 X{mpt[0]:.3f} Y{mpt[1]:.3f} F{self.speed}")
                        current_pos = mpt
                        
                    elif op == 'qCurveTo':
                        mcp = _tx(cmd[1], _get_n(1), amt, bx, by)
                        mep = _tx(cmd[2], _get_n(2), amt, bx, by)
                        if current_pos:
                            arc_lines = _quad_to_arc_or_lines_machine(current_pos, mcp, mep, self.speed)
                            gcode.extend(arc_lines)
                        current_pos = mep
                        
                    elif op == 'curveTo':
                        mcp1 = _tx(cmd[1], _get_n(1), amt, bx, by)
                        mcp2 = _tx(cmd[2], _get_n(2), amt, bx, by)
                        mep  = _tx(cmd[3], _get_n(3), amt, bx, by)
                        if current_pos:
                            arc_lines = _cubic_to_arc_or_lines_machine(current_pos, mcp1, mcp2, mep, self.speed)
                            gcode.extend(arc_lines)
                        current_pos = mep

                # Turn off laser at end of bold/pass
                gcode.append("M5")

        gcode.extend([
            "; Job Complete",
            "G90",         
            "G0 Z0",       
            "$H",          
            "$MD",         
        ])

        return "\n".join(gcode)
