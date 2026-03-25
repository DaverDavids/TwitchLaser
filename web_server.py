"""
Web Server - Flask-based web interface for TwitchLaser
"""

import os
import subprocess
import threading
import logging

from flask import Flask, render_template, request, jsonify, Response

from config import config, debug_print
from gcode_generator import FONT_PROFILES

# Disable Flask/Werkzeug HTTP GET logging to prevent log spam
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

app = Flask(__name__)

laser = layout = gcode_gen = twitch = camera = job_mgr = obs_ctrl = alarm_led = None

# Thread-safe engraving progress tracking
_engrave_lock = threading.Lock()
_engrave_progress = {'active': False, 'current': 0, 'total': 0, 'text': ''}
_engrave_stop_flag = False


def init_web_server(laser_ctrl, layout_mgr, gcode_generator,
                    twitch_mon, camera_stream, job_manager,
                    obs_controller=None, alarm_indicator=None):
    global laser, layout, gcode_gen, twitch, camera, job_mgr, obs_ctrl, alarm_led
    laser     = laser_ctrl
    layout    = layout_mgr
    gcode_gen = gcode_generator
    twitch    = twitch_mon
    camera    = camera_stream
    job_mgr   = job_manager
    obs_ctrl  = obs_controller
    alarm_led = alarm_indicator


# ── Pages ─────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')


# ── Status ───────────────────────────────────────────────
@app.route('/api/status')
def get_status():
    stats = layout.get_statistics() if layout else {}
    pending_count = len([j for j in job_mgr.get_jobs() if j['status'] == 'pending']) if job_mgr else 0

    mpos  = laser.mpos          if laser else {'x': 0.0, 'y': 0.0, 'z': 0.0}
    state = laser.machine_state if laser else 'Offline'

    return jsonify({
        'laser_connected': laser.connected if laser else False,
        'machine_state':   state,
        'mpos':            mpos,
        'twitch_running':  twitch.is_running()      if twitch    else False,
        'camera_running':  camera.is_running()      if camera    else False,
        'obs_connected':   obs_ctrl.is_connected()  if obs_ctrl  else False,
        'queue_size':      pending_count,
        'placements':      stats.get('total', 0),
        'coverage':        round(stats.get('coverage_percent', 0), 1),
        'config':          config.config,
    })


# ── Config ───────────────────────────────────────────────
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
            gcode_gen._load_settings()

        if obs_ctrl and 'obs' in updates:
            obs_ctrl.reconnect()

        return jsonify({'success': True})

    return jsonify(config.config)


# ── GPIO config ───────────────────────────────────────────
@app.route('/api/gpio_config', methods=['GET', 'POST'])
def gpio_config():
    if request.method == 'GET':
        return jsonify({
            'alarm_led_gpio_pin':       config.get('alarm_led_gpio_pin', 17),
            'recovery_button_gpio_pin': config.get('recovery_button_gpio_pin', 27),
        })

    data = request.json or {}
    errors = []

    led_pin = data.get('alarm_led_gpio_pin')
    btn_pin = data.get('recovery_button_gpio_pin')

    # Validate — must be integers in the usable BCM range
    for label, val in [('alarm_led_gpio_pin', led_pin),
                       ('recovery_button_gpio_pin', btn_pin)]:
        if val is None:
            continue
        try:
            v = int(val)
            if not (2 <= v <= 27):
                raise ValueError()
        except (ValueError, TypeError):
            errors.append(f'{label} must be an integer between 2 and 27')

    if errors:
        return jsonify({'success': False, 'message': '; '.join(errors)}), 400

    if led_pin is not None:
        config.set('alarm_led_gpio_pin', int(led_pin))
    if btn_pin is not None:
        config.set('recovery_button_gpio_pin', int(btn_pin))

    # Restart the AlarmIndicator live so changes take effect without a service restart
    if alarm_led:
        try:
            alarm_led.stop()
            alarm_led._led_pin    = config.get('alarm_led_gpio_pin', 17)
            alarm_led._button_pin = config.get('recovery_button_gpio_pin', 27)
            alarm_led._init_gpio()
            alarm_led.start()
            debug_print(f"AlarmIndicator restarted: LED=GPIO{alarm_led._led_pin} "
                        f"BTN=GPIO{alarm_led._button_pin}")
        except Exception as e:
            return jsonify({'success': False,
                            'message': f'Config saved but GPIO restart failed: {e}'})

    return jsonify({'success': True,
                    'message': 'GPIO pins updated and applied live.'
                               ' No service restart needed.'})


# ── Font list ──────────────────────────────────────────────
@app.route('/api/fonts')
def get_fonts():
    return jsonify([
        {'key': k, 'label': v[0], 'line_width_mm': v[1], 'engine': v[2]}
        for k, v in FONT_PROFILES.items()
    ])


# ── Work Area ────────────────────────────────────────────
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

        if 'edge_margin_mm'  not in data:
            data['edge_margin_mm']  = config.get('engraving_area.edge_margin_mm', 1.5)
        if 'name_padding_mm' not in data:
            data['name_padding_mm'] = config.get('engraving_area.name_padding_mm', 1.5)

        config.set('engraving_area', data)
        if layout:
            layout.machine_width_mm  = data['machine_width_mm']
            layout.machine_height_mm = data['machine_height_mm']
            layout.width_mm          = data['active_width_mm']
            layout.height_mm         = data['active_height_mm']
            layout.offset_x_mm       = data['offset_x_mm']
            layout.offset_y_mm       = data['offset_y_mm']
            layout.edge_margin_mm    = data['edge_margin_mm']
            layout.name_padding_mm   = data['name_padding_mm']
        return jsonify({'success': True})

    if layout:
        return jsonify({
            'machine':    {'width_mm':  layout.machine_width_mm,
                           'height_mm': layout.machine_height_mm},
            'active':     {'width_mm':  layout.width_mm,
                           'height_mm': layout.height_mm,
                           'offset_x':  layout.offset_x_mm,
                           'offset_y':  layout.offset_y_mm,
                           'edge_margin_mm':  getattr(layout, 'edge_margin_mm', 1.5),
                           'name_padding_mm': getattr(layout, 'name_padding_mm', 1.5)},
            'placements': layout.placements,
        })
    return jsonify({'machine': {}, 'active': {}, 'placements': []}), 503


# ── Laser commands ───────────────────────────────────────
@app.route('/api/laser_command', methods=['POST'])
def laser_command():
    cmd = (request.json or {}).get('command', '')
    if not cmd:
        return jsonify({'success': False, 'message': 'No command'})
    success, response = laser.send_command(cmd)
    return jsonify({'success': success, 'response': response})

@app.route('/api/laser_home',        methods=['POST'])
def laser_home():
    s, r = laser.home();         return jsonify({'success': s, 'message': r})

@app.route('/api/laser_unlock',      methods=['POST'])
def laser_unlock():
    s, r = laser.unlock();       return jsonify({'success': s, 'message': r})

@app.route('/api/laser_clear_alarm', methods=['POST'])
def laser_clear_alarm():
    s, r = laser.clear_alarm();  return jsonify({'success': s, 'message': r})

@app.route('/api/laser_reset',       methods=['POST'])
def laser_reset():
    s, r = laser.reset();        return jsonify({'success': s, 'message': r})

@app.route('/api/laser_resume',      methods=['POST'])
def laser_resume():
    s, r = laser.resume();       return jsonify({'success': s, 'message': r})

@app.route('/api/laser_stop',        methods=['POST'])
def laser_stop():
    s, r = laser.stop();         return jsonify({'success': s, 'message': r})

@app.route('/api/laser_reconnect',   methods=['POST'])
def laser_reconnect():
    if laser:
        laser.disconnect()
        ok = laser.reconnect()
        return jsonify({'success': ok, 'connected': laser.connected,
                        'message': 'Reconnected' if ok else 'Reconnect failed'})
    return jsonify({'success': False, 'message': 'Laser module not loaded'})


# ── Engraving progress & stop endpoints ───────────────────
@app.route('/api/engrave_progress')
def engrave_progress():
    with _engrave_lock:
        return jsonify(_engrave_progress.copy())

@app.route('/api/engrave_stop', methods=['POST'])
def engrave_stop():
    global _engrave_stop_flag
    with _engrave_lock:
        _engrave_stop_flag = True
    laser.stop()
    return jsonify({'success': True, 'message': 'Stop requested'})


# ── Test Engraving ─────────────────────────────────────────
@app.route('/api/test_engrave', methods=['POST'])
def test_engrave():
    data = request.json or {}
    text = data.get('text', '').strip()
    if not text:
        return jsonify({'success': False, 'message': 'No text provided'})

    settings = {}
    if 'rect' in data:
        rect_data = data['rect']
        if 'x2' not in rect_data or rect_data['x2'] == '' or rect_data['x2'] is None:
            settings['override_rect'] = {
                'x1': float(rect_data.get('x1', 0)),
                'y1': float(rect_data.get('y1', 0))
            }
        else:
            settings['override_rect'] = {
                'x1': float(rect_data.get('x1', 0)),
                'y1': float(rect_data.get('y1', 0)),
                'x2': float(rect_data.get('x2', 0)),
                'y2': float(rect_data.get('y2', 0))
            }

    job_mgr.add_job(text, source='Web UI Test', settings=settings)
    return jsonify({'success': True, 'message': 'Added to queue'})


# ── Focus Test ────────────────────────────────────────────
@app.route('/api/focus_test', methods=['POST'])
def focus_test():
    if not laser or not laser.connected:
        return jsonify({'success': False, 'message': 'Laser not connected'})

    data = request.json or {}
    try:
        x1 = float(data['x1']); y1 = float(data['y1'])
        x2 = float(data['x2']); y2 = float(data['y2'])
        start_z = float(data['start_z'])
        end_z   = float(data['end_z'])
        power   = float(data['power'])
        speed   = float(data['speed'])
        ticks   = int(data.get('ticks', 5))
    except (KeyError, ValueError) as e:
        return jsonify({'success': False, 'message': f'Invalid parameters: {e}'})

    spindle_max = float(config.get('laser_settings.spindle_max', 1000))
    s_val = int((power / 100.0) * spindle_max)

    import math
    dx = x2 - x1; dy = y2 - y1
    z_dist = end_z - start_z
    length = math.hypot(dx, dy)

    if length < 1e-3:
        return jsonify({'success': False, 'message': 'Start and end points are too close'})

    ux = dx / length; uy = dy / length
    px = -uy;         py = ux

    tick_len = 3.0
    if px < 0 and x1 < tick_len: px = -px; py = -py
    if py < 0 and y1 < tick_len: px = -px; py = -py
    if ticks < 1: ticks = 1

    gc = [
        '; Focus Test', 'G21', 'G10 L2 P1 X0 Y0', 'G54', 'G90',
        f'G0 Z{start_z:.4f}', f'G0 X{x1:.4f} Y{y1:.4f}', f'M4 S{s_val}'
    ]
    for i in range(1, ticks + 1):
        fraction = i / float(ticks)
        cx = x1 + fraction * dx; cy = y1 + fraction * dy
        cz = start_z + fraction * z_dist
        gc.append(f'G1 X{cx:.4f} Y{cy:.4f} Z{cz:.4f} F{speed:.1f}')
        if i < ticks:
            tx = cx + px * tick_len; ty = cy + py * tick_len
            gc.append(f'G1 X{tx:.4f} Y{ty:.4f} Z{cz:.4f} F{speed:.1f}')
            gc.append(f'G1 X{cx:.4f} Y{cy:.4f} Z{cz:.4f} F{speed:.1f}')
    gc.extend(['M5', 'G90', 'G0 Z0', '$H', '$MD'])

    threading.Thread(target=lambda: laser.send_gcode(gc), daemon=True).start()
    return jsonify({'success': True, 'message': 'Focus test started'})


# ── Manual placement ───────────────────────────────────────
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


# ── Placements ─────────────────────────────────────────────
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


# ── Reset board ────────────────────────────────────────────
@app.route('/api/reset_board', methods=['POST'])
def reset_board():
    backup = layout.archive_and_reset()
    msg    = (f'Board reset – backup → {os.path.basename(backup)}'
              if backup else 'Board reset (nothing to back up).')
    debug_print(msg)
    return jsonify({'success': True, 'message': msg, 'backup': backup})


# ── Restart service ───────────────────────────────────────
@app.route('/api/restart_service', methods=['POST'])
def restart_service():
    def restart_task():
        import time
        time.sleep(1)
        subprocess.Popen(['sudo', '/bin/systemctl', 'restart', 'twitchlaser'])
    threading.Thread(target=restart_task, daemon=True).start()
    return jsonify({'success': True, 'message': 'Service restarting…'})


# ── Twitch / Queue / Jobs ──────────────────────────────────
@app.route('/api/twitch_config', methods=['GET', 'POST'])
def twitch_config():
    if request.method == 'POST':
        data = request.json or {}
        config.set('twitch', data)
        if twitch:
            if data.get('enabled', True):
                if not twitch.is_running(): twitch.start()
                else:                       twitch.reconnect()
            else:
                twitch.stop()
        return jsonify({'success': True})
    return jsonify(config.get('twitch', {}))

@app.route('/api/twitch_toggle', methods=['POST'])
def toggle_twitch():
    if twitch.is_running():
        twitch.stop()
        config.set('twitch.enabled', False)
        return jsonify({'success': True, 'running': False})
    config.set('twitch.enabled', True)
    success = twitch.start()
    return jsonify({'success': success, 'running': success})

@app.route('/api/twitch_reconnect', methods=['POST'])
def twitch_reconnect():
    ok = twitch.reconnect()
    return jsonify({'success': ok, 'running': twitch.is_running(),
                    'message': 'Reconnecting…' if ok else 'Reconnect failed'})

@app.route('/api/jobs', methods=['GET'])
def get_jobs():
    return jsonify({'jobs': job_mgr.get_jobs()})

@app.route('/api/jobs/<job_id>/action', methods=['POST'])
def job_action(job_id):
    action = (request.json or {}).get('action', '')
    if action == 'redo':
        new_job = job_mgr.redo_job(job_id)
        if new_job:
            return jsonify({'success': True, 'message': 'Job re-added to queue', 'job': new_job})
        return jsonify({'success': False, 'message': 'Job not found'})
    elif action == 'stop':
        laser.stop()
        return jsonify({'success': True, 'message': 'Stop requested'})
    return jsonify({'success': False, 'message': 'Unknown action'})

@app.route('/api/jobs/<job_id>/gcode', methods=['GET'])
def download_gcode(job_id):
    path = job_mgr.get_gcode_path(job_id)
    if path and os.path.exists(path):
        with open(path, 'r') as f:
            content = f.read()
        return Response(content, mimetype='text/plain',
            headers={'Content-disposition': f'attachment; filename=job_{job_id}.gcode'})
    return 'G-Code not found', 404


# ── OBS ──────────────────────────────────────────────────
@app.route('/api/obs_reconnect', methods=['POST'])
def obs_reconnect():
    if not obs_ctrl:
        return jsonify({'success': False, 'message': 'OBS controller not initialized'})
    ok = obs_ctrl.reconnect()
    return jsonify({'success': ok, 'connected': obs_ctrl.is_connected(),
                    'message': 'OBS reconnected' if ok else 'OBS reconnect failed'})

@app.route('/api/obs_test_action', methods=['POST'])
def obs_test_action():
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
    return jsonify({'success': False, 'message': 'Provide event or action'})

@app.route('/api/obs_config', methods=['GET', 'POST'])
def obs_config():
    if request.method == 'POST':
        data = request.json or {}
        config.set('obs', data)
        if obs_ctrl: obs_ctrl.reconnect()
        return jsonify({'success': True})
    return jsonify(config.get('obs', {}))


# ── Camera stream ──────────────────────────────────────────
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
    app.run(host=host, port=port, debug=False, threaded=True, use_reloader=False)
