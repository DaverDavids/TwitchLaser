"""
Web Server - Flask-based web interface for TwitchLaser
"""

import os
import subprocess
import threading

from flask import Flask, render_template, request, jsonify, Response

from config import config, debug_print
from gcode_generator import FONT_PROFILES

app = Flask(__name__)

laser = layout = gcode_gen = twitch = camera = engraving_queue = obs_ctrl = None

# NEW: Thread-safe engraving progress tracking
_engrave_lock = threading.Lock()
_engrave_progress = {'active': False, 'current': 0, 'total': 0, 'text': ''}
_engrave_stop_flag = False


def init_web_server(laser_ctrl, layout_mgr, gcode_generator,
                    twitch_mon, camera_stream, queue, obs_controller=None):
    global laser, layout, gcode_gen, twitch, camera, engraving_queue, obs_ctrl
    laser           = laser_ctrl
    layout          = layout_mgr
    gcode_gen       = gcode_generator
    twitch          = twitch_mon
    camera          = camera_stream
    engraving_queue = queue
    obs_ctrl        = obs_controller


# ── Pages ─────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')


# ── Status ─────────────────────────────────────────────────
@app.route('/api/status')
def get_status():
    stats = layout.get_statistics() if layout else {}
    return jsonify({
        'laser_connected': laser.connected if laser else False,
        'twitch_running':  twitch.is_running() if twitch else False,
        'camera_running':  camera.is_running() if camera else False,
        'obs_connected':   obs_ctrl.is_connected() if obs_ctrl else False,
        'queue_size':      len(engraving_queue) if engraving_queue is not None else 0,
        'placements':      stats.get('total', 0),
        'coverage':        round(stats.get('coverage_percent', 0), 1),
        'config':          config.config,
    })


# ── Config ─────────────────────────────────────────────────
@app.route('/api/config', methods=['GET', 'POST'])
def handle_config():
    if request.method == 'POST':
        updates = request.json or {}
        config.update(updates)

        if gcode_gen and 'laser_settings' in updates:
            s = updates['laser_settings']
            if 'power_percent'    in s: gcode_gen.laser_power = s['power_percent']
            if 'speed_mm_per_min' in s: gcode_gen.speed       = s['speed_mm_per_min']
            if 'spindle_max'      in s: gcode_gen.spindle_max = s['spindle_max']

        if gcode_gen and 'text_settings' in updates:
            ts       = updates['text_settings']
            font_key = ts.get('font', gcode_gen.font_key)
            profile  = FONT_PROFILES.get(font_key, FONT_PROFILES['simplex'])
            gcode_gen.font_key      = font_key
            gcode_gen.line_width_mm = profile[1]
            gcode_gen.engine        = profile[2]
            gcode_gen._glyph_cache  = {}
            gcode_gen.ttf_path = config.get('text_settings.ttf_path', gcode_gen.ttf_path)
            gcode_gen._ttfont  = None

        # If OBS settings changed, reconnect
        if obs_ctrl and 'obs' in updates:
            obs_ctrl.reconnect()

        return jsonify({'success': True})

    return jsonify(config.config)


# ── Font list ───────────────────────────────────────────────
@app.route('/api/fonts')
def get_fonts():
    return jsonify([
        {'key': k, 'label': v[0], 'line_width_mm': v[1], 'engine': v[2]}
        for k, v in FONT_PROFILES.items()
    ])


# ── Work Area ───────────────────────────────────────────────
@app.route('/api/work_area', methods=['GET', 'POST'])
def work_area():
    if request.method == 'POST':
        data     = request.json or {}
        required = ['machine_width_mm', 'machine_height_mm',
                    'active_width_mm',  'active_height_mm',
                    'offset_x_mm',      'offset_y_mm']
        for f in required:
            if f not in data:
                return jsonify({'success': False, 'message': f'Missing: {f}'}), 400
        config.set('engraving_area', data)
        if layout:
            layout.machine_width_mm  = data['machine_width_mm']
            layout.machine_height_mm = data['machine_height_mm']
            layout.width_mm          = data['active_width_mm']
            layout.height_mm         = data['active_height_mm']
            layout.offset_x_mm       = data['offset_x_mm']
            layout.offset_y_mm       = data['offset_y_mm']
        return jsonify({'success': True})

    if layout:
        return jsonify({
            'machine':    {'width_mm':  layout.machine_width_mm,
                           'height_mm': layout.machine_height_mm},
            'active':     {'width_mm':  layout.width_mm,
                           'height_mm': layout.height_mm,
                           'offset_x':  layout.offset_x_mm,
                           'offset_y':  layout.offset_y_mm},
            'placements': layout.placements,
        })
    return jsonify({'machine': {}, 'active': {}, 'placements': []}), 503


# ── Laser commands ───────────────────────────────────────────
@app.route('/api/laser_command', methods=['POST'])
def laser_command():
    cmd = (request.json or {}).get('command', '')
    if not cmd:
        return jsonify({'success': False, 'message': 'No command'})
    success, response = laser.send_command(cmd)
    return jsonify({'success': success, 'response': response})

@app.route('/api/laser_home',      methods=['POST'])
def laser_home():
    s, r = laser.home();      return jsonify({'success': s, 'message': r})

@app.route('/api/laser_unlock',    methods=['POST'])
def laser_unlock():
    s, r = laser.unlock();    return jsonify({'success': s, 'message': r})

@app.route('/api/laser_stop',      methods=['POST'])
def laser_stop():
    s, r = laser.stop();      return jsonify({'success': s, 'message': r})

@app.route('/api/laser_reconnect', methods=['POST'])
def laser_reconnect():
    ok = laser.reconnect()
    return jsonify({'success': ok, 'connected': laser.connected,
                    'message': 'Reconnected' if ok else 'Reconnect failed'})


# ── NEW: Engraving progress & stop endpoints ─────────────────
@app.route('/api/engrave_progress')
def engrave_progress():
    """Return current engraving progress."""
    with _engrave_lock:
        return jsonify(_engrave_progress.copy())

@app.route('/api/engrave_stop', methods=['POST'])
def engrave_stop():
    """Request immediate stop of active engraving."""
    global _engrave_stop_flag
    with _engrave_lock:
        if not _engrave_progress['active']:
            return jsonify({'success': False, 'message': 'No active engraving'})
        _engrave_stop_flag = True
    laser.stop()  # Send E-stop immediately
    return jsonify({'success': True, 'message': 'Stop requested'})


# ── Engraving ─────────────────────────────────────────────────
@app.route('/api/test_engrave', methods=['POST'])
def test_engrave():
    global _engrave_stop_flag
    data = request.json or {}
    text = data.get('text', '').strip()
    if not text:
        return jsonify({'success': False, 'message': 'No text provided'})

    try:
        text_height    = config.get('text_settings.initial_height_mm', 5.0)
        laser_settings = config.get('laser_settings', {})
        rect = data.get('rect')

        # Reset stop flag and initialize progress
        with _engrave_lock:
            _engrave_stop_flag = False
            _engrave_progress['active'] = True
            _engrave_progress['current'] = 0
            _engrave_progress['total'] = 0
            _engrave_progress['text'] = text

        def progress_cb(current, total):
            """Progress callback for send_gcode."""
            with _engrave_lock:
                _engrave_progress['current'] = current
                _engrave_progress['total'] = total

        if rect:
            x1 = float(rect['x1']); y1 = float(rect['y1'])
            x2 = float(rect['x2']); y2 = float(rect['y2'])
            x_machine = min(x1, x2); y_machine = min(y1, y2)
            box_h     = abs(y2 - y1)
            min_h     = config.get('text_settings.min_height_mm', 2.0)
            final_h   = max(min(text_height, box_h * 0.85), min_h)
            gcode, aw, ah = gcode_gen.text_to_gcode(
                text, x_machine, y_machine, final_h,
                passes=laser_settings.get('passes', 1))
            if obs_ctrl: obs_ctrl.on_engrave_start(name=text)
            success, message = laser.send_gcode(gcode.split('\n'), progress_callback=progress_cb)
            if obs_ctrl: obs_ctrl.on_engrave_finish(name=text, success=success)
            
            with _engrave_lock:
                _engrave_progress['active'] = False
            
            if success:
                layout.add_placement(text,
                    x_machine - layout.offset_x_mm,
                    y_machine - layout.offset_y_mm, aw, ah, final_h)
                return jsonify({'success': True,
                    'message': f'Engraved "{text}" at ({x_machine:.1f},{y_machine:.1f})  {aw:.1f}x{ah:.1f} mm',
                    'position': {'x': x_machine, 'y': y_machine},
                    'size':     {'width': aw, 'height': ah}})
            return jsonify({'success': False, 'message': message})
        else:
            width, height = gcode_gen.estimate_dimensions(text, text_height)
            position = layout.find_empty_space(width, height, text_height)
            if not position:
                with _engrave_lock:
                    _engrave_progress['active'] = False
                return jsonify({'success': False, 'message': 'No space available on board'})
            x_local, y_local, final_h = position
            x_machine = x_local + layout.offset_x_mm
            y_machine = y_local + layout.offset_y_mm
            gcode, aw, ah = gcode_gen.text_to_gcode(
                text, x_machine, y_machine, final_h,
                passes=laser_settings.get('passes', 1))
            if obs_ctrl: obs_ctrl.on_engrave_start(name=text)
            success, message = laser.send_gcode(gcode.split('\n'), progress_callback=progress_cb)
            if obs_ctrl: obs_ctrl.on_engrave_finish(name=text, success=success)
            
            with _engrave_lock:
                _engrave_progress['active'] = False
            
            if success:
                layout.add_placement(text, x_local, y_local, aw, ah, final_h)
                return jsonify({'success': True,
                    'message': f'Engraved "{text}" machine=({x_machine:.1f},{y_machine:.1f})  {aw:.1f}x{ah:.1f} mm',
                    'position': {'x': x_machine, 'y': y_machine},
                    'size':     {'width': aw, 'height': ah}})
            return jsonify({'success': False, 'message': message})

    except Exception as e:
        with _engrave_lock:
            _engrave_progress['active'] = False
        return jsonify({'success': False, 'message': str(e)})


# ── Manual placement ──────────────────────────────────────────
@app.route('/api/add_placement', methods=['POST'])
def add_placement():
    data = request.json or {}
    name = data.get('name', '').strip()
    if not name:
        return jsonify({'success': False, 'message': 'Name is required'})
    try:
        x1 = float(data['x1']); y1 = float(data['y1'])
        x2 = float(data['x2']); y2 = float(data['y2'])
    except (KeyError, ValueError) as e:
        return jsonify({'success': False, 'message': f'Invalid coordinates: {e}'})
    x_machine = min(x1, x2); y_machine = min(y1, y2)
    width = abs(x2 - x1);    height    = abs(y2 - y1)
    if width < 0.1 or height < 0.1:
        return jsonify({'success': False, 'message': 'Rectangle too small (min 0.1mm)'})
    layout.add_placement(name,
        x_machine - layout.offset_x_mm,
        y_machine - layout.offset_y_mm, width, height, height * 0.8)
    return jsonify({'success': True,
        'message': f'Added "{name}" at ({x_machine:.1f},{y_machine:.1f})  {width:.1f}x{height:.1f} mm'})


# ── Placements ────────────────────────────────────────────────
@app.route('/api/placements')
def get_placements():
    return jsonify({
        'placements':     layout.placements,
        'machine_width':  layout.machine_width_mm,
        'machine_height': layout.machine_height_mm,
        'active_width':   layout.width_mm,
        'active_height':  layout.height_mm,
        'offset_x':       layout.offset_x_mm,
        'offset_y':       layout.offset_y_mm,
    })

@app.route('/api/clear_placements', methods=['POST'])
def clear_placements():
    layout.clear_all()
    return jsonify({'success': True, 'message': 'Placements cleared'})


# ── Reset board ───────────────────────────────────────────────
@app.route('/api/reset_board', methods=['POST'])
def reset_board():
    backup = layout.archive_and_reset()
    msg    = (f'Board reset – backup → {os.path.basename(backup)}'
              if backup else 'Board reset (nothing to back up).')
    debug_print(msg)
    return jsonify({'success': True, 'message': msg, 'backup': backup})


# ── Restart service ───────────────────────────────────────────
@app.route('/api/restart_service', methods=['POST'])
def restart_service():
    try:
        subprocess.Popen(['sudo', 'systemctl', 'restart', 'twitchlaser'])
        return jsonify({'success': True, 'message': 'Service restarting…'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


# ── Twitch / Queue ────────────────────────────────────────────
@app.route('/api/twitch_toggle', methods=['POST'])
def toggle_twitch():
    if twitch.is_running():
        twitch.stop()
        return jsonify({'success': True, 'running': False})
    success = twitch.start()
    return jsonify({'success': success, 'running': success})

@app.route('/api/twitch_reconnect', methods=['POST'])
def twitch_reconnect():
    ok = twitch.reconnect()
    return jsonify({'success': ok, 'running': twitch.is_running(),
                    'message': 'Reconnecting…' if ok else 'Reconnect failed'})

@app.route('/api/queue')
def get_queue():
    return jsonify({'queue': list(engraving_queue)})


# ── OBS ─────────────────────────────────────────────────────
@app.route('/api/obs_reconnect', methods=['POST'])
def obs_reconnect():
    if not obs_ctrl:
        return jsonify({'success': False, 'message': 'OBS controller not initialized'})
    ok = obs_ctrl.reconnect()
    return jsonify({'success': ok, 'connected': obs_ctrl.is_connected(),
                    'message': 'OBS reconnected' if ok else 'OBS reconnect failed'})

@app.route('/api/obs_test_action', methods=['POST'])
def obs_test_action():
    """
    Test a single OBS action immediately.
    Body: { event: 'start'|'finish' }  or  { action: {...} }
    """
    if not obs_ctrl:
        return jsonify({'success': False, 'message': 'OBS not initialized'})

    data  = request.json or {}
    event = data.get('event', '')

    if event == 'start':
        obs_ctrl.on_engrave_start(name='TestUser')
        return jsonify({'success': True, 'message': 'Start actions fired'})
    elif event == 'finish':
        obs_ctrl.on_engrave_finish(name='TestUser', success=True)
        return jsonify({'success': True, 'message': 'Finish actions fired'})
    elif 'action' in data:
        ok, msg = obs_ctrl.test_action(data['action'], name='TestUser')
        return jsonify({'success': ok, 'message': msg})
    else:
        return jsonify({'success': False, 'message': 'Provide event or action'})

@app.route('/api/obs_config', methods=['GET', 'POST'])
def obs_config():
    """Get or save OBS settings (host, port, password, actions)."""
    if request.method == 'POST':
        data = request.json or {}
        config.set('obs', data)
        if obs_ctrl:
            obs_ctrl.reconnect()
        return jsonify({'success': True})
    return jsonify(config.get('obs', {}))


# ── Camera stream ──────────────────────────────────────────────
@app.route('/video_feed')
def video_feed():
    def generate():
        import time
        while True:
            frame = camera.get_frame() if camera else None
            if frame:
                yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
            else:
                time.sleep(0.1)
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')


def run_server(host='0.0.0.0', port=5000):
    app.run(host=host, port=port, debug=False, threaded=True)
