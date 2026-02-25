"""
Web Server - Flask-based web interface for TwitchLaser
"""

import os
import subprocess

from flask import Flask, render_template, request, jsonify, Response

from config import config, debug_print
from gcode_generator import FONT_PROFILES

app = Flask(__name__)

laser = layout = gcode_gen = twitch = camera = engraving_queue = None


def init_web_server(laser_ctrl, layout_mgr, gcode_generator,
                    twitch_mon, camera_stream, queue):
    global laser, layout, gcode_gen, twitch, camera, engraving_queue
    laser           = laser_ctrl
    layout          = layout_mgr
    gcode_gen       = gcode_generator
    twitch          = twitch_mon
    camera          = camera_stream
    engraving_queue = queue


# ── Pages ─────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')


# ── Status ────────────────────────────────────────────────────
@app.route('/api/status')
def get_status():
    stats = layout.get_statistics() if layout else {}
    return jsonify({
        'laser_connected': laser.connected if laser else False,
        'twitch_running':  twitch.is_running() if twitch else False,
        'camera_running':  camera.is_running() if camera else False,
        'queue_size':      len(engraving_queue) if engraving_queue is not None else 0,
        'placements':      stats.get('total', 0),
        'coverage':        round(stats.get('coverage_percent', 0), 1),
        'config':          config.config,
    })


# ── Config ────────────────────────────────────────────────────
@app.route('/api/config', methods=['GET', 'POST'])
def handle_config():
    if request.method == 'POST':
        updates = request.json or {}
        config.update(updates)

        # Live-apply laser settings to running generator
        if gcode_gen and 'laser_settings' in updates:
            s = updates['laser_settings']
            if 'power_percent'    in s: gcode_gen.laser_power  = s['power_percent']
            if 'speed_mm_per_min' in s: gcode_gen.speed        = s['speed_mm_per_min']
            if 'spindle_max'      in s: gcode_gen.spindle_max  = s['spindle_max']

        # Live-apply font / text settings to running generator
        if gcode_gen and 'text_settings' in updates:
            ts       = updates['text_settings']
            font_key = ts.get('font', gcode_gen.font_key)
            profile  = FONT_PROFILES.get(font_key, FONT_PROFILES['simplex'])
            # profile is (label, line_width_mm, engine)
            gcode_gen.font_key      = font_key
            gcode_gen.line_width_mm = profile[1]
            gcode_gen.engine        = profile[2]
            gcode_gen._glyph_cache  = {}   # invalidate cache on font change
            # Always sync ttf_path from config (covers both explicit updates and inherited value)
            gcode_gen.ttf_path = config.get('text_settings.ttf_path', gcode_gen.ttf_path)
            gcode_gen._ttfont  = None  # force TTF reload

        return jsonify({'success': True})

    return jsonify(config.config)


# ── Font list ─────────────────────────────────────────────────
@app.route('/api/fonts')
def get_fonts():
    return jsonify([
        {'key': k, 'label': v[0], 'line_width_mm': v[1], 'engine': v[2]}
        for k, v in FONT_PROFILES.items()
    ])


# ── Work Area ─────────────────────────────────────────────────
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


# ── Laser commands ────────────────────────────────────────────
@app.route('/api/laser_command', methods=['POST'])
def laser_command():
    cmd = (request.json or {}).get('command', '')
    if not cmd:
        return jsonify({'success': False, 'message': 'No command'})
    success, response = laser.send_command(cmd)
    return jsonify({'success': success, 'response': response})

@app.route('/api/laser_home',   methods=['POST'])
def laser_home():
    s, r = laser.home();   return jsonify({'success': s, 'message': r})

@app.route('/api/laser_unlock', methods=['POST'])
def laser_unlock():
    s, r = laser.unlock(); return jsonify({'success': s, 'message': r})

@app.route('/api/laser_stop',   methods=['POST'])
def laser_stop():
    s, r = laser.stop();   return jsonify({'success': s, 'message': r})


# ── Engraving ─────────────────────────────────────────────────
@app.route('/api/test_engrave', methods=['POST'])
def test_engrave():
    text = (request.json or {}).get('text', '').strip()
    if not text:
        return jsonify({'success': False, 'message': 'No text provided'})

    try:
        text_height    = config.get('text_settings.initial_height_mm', 5.0)
        laser_settings = config.get('laser_settings', {})

        # Get true bounding-box size using active font engine
        width, height = gcode_gen.estimate_dimensions(text, text_height)

        # Find a free spot (auto-shrinks if needed)
        position = layout.find_empty_space(width, height, text_height)
        if not position:
            return jsonify({'success': False, 'message': 'No space available on board'})

        x_local, y_local, final_h = position
        x_machine = x_local + layout.offset_x_mm
        y_machine  = y_local + layout.offset_y_mm

        gcode, aw, ah = gcode_gen.text_to_gcode(
            text, x_machine, y_machine, final_h,
            passes=laser_settings.get('passes', 1),
        )

        success, message = laser.send_gcode(gcode.split('\n'))

        if success:
            layout.add_placement(text, x_local, y_local, aw, ah, final_h)
            return jsonify({
                'success': True,
                'message': f'Engraved "{text}"  machine=({x_machine:.1f},{y_machine:.1f})  {aw:.1f}x{ah:.1f} mm',
                'position': {'x': x_machine, 'y': y_machine},
                'size':     {'width': aw, 'height': ah},
            })
        return jsonify({'success': False, 'message': message})

    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


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

@app.route('/api/queue')
def get_queue():
    return jsonify({'queue': list(engraving_queue)})


# ── Camera stream ─────────────────────────────────────────────
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
