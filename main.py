#!/usr/bin/env python3
"""
TwitchLaser - Twitch-controlled laser engraver
Main application entry point
"""

import sys
import time
import os
import signal
import threading

# Force unbuffered output so systemd/journalctl gets logs immediately
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(line_buffering=True)
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(line_buffering=True)

try:
    from config import config, debug_print
except ImportError:
    print("FATAL: config.py not found. Please rename config.py.template to config.py")
    sys.exit(1)
    
from laser_controller import LaserController
from layout_manager import LayoutManager
from gcode_generator import GCodeGenerator, FONT_PROFILES
from twitch_monitor import TwitchMonitor
from obs_controller import OBSController
from job_manager import JobManager

try:
    from camera_stream import CameraStream
    CAMERA_AVAILABLE = True
except Exception as e:
    print(f"WARNING: Camera module failed to load ({e}). Running without camera.")
    CAMERA_AVAILABLE = False
    CameraStream = None

from web_server import init_web_server, run_server

# ── Queue state ─────────────────────────────────────────────
job_mgr    = JobManager()
queue_lock = threading.Lock()
processing = False


def enqueue_name(name, source='twitch'):
    with queue_lock:
        job_mgr.add_job(name, source)
    debug_print(f'Queued: {name}  (from {source})')


def process_queue(laser, layout, gcode_gen, obs):
    """Worker thread: pop names from queue, place them, engrave."""
    global processing

    while True:
        try:
            job = None
            with queue_lock:
                if not processing:
                    job = job_mgr.get_next_pending()
                    if job:
                        processing = True
                        job_mgr.update_job(job['id'], status='active')

            if not job:
                time.sleep(1)
                continue

            name = job['name']
            debug_print(f"Processing job {job['id']}: {name}")

            gcode_gen._load_settings()

            gcode_path = job_mgr.get_gcode_path(job['id'])
            if gcode_path and os.path.exists(gcode_path):
                with open(gcode_path, 'r') as f:
                    gcode = f.read()
                
                actual_w = job['settings'].get('width', 0)
                actual_h = job['settings'].get('height', 0)
                x_local = job['settings'].get('x_local', 0)
                y_local = job['settings'].get('y_local', 0)
                final_height = job['settings'].get('text_height', 0)
                
                success, message = _run_engrave(job, gcode, name, laser, obs)
                
            else:
                text_height    = config.get('text_settings.initial_height_mm', 5.0)
                laser_settings = config.get('laser_settings', {})

                _, _, _, _, raw_width = gcode_gen._get_ttf_commands(name, text_height)
                width = raw_width
                height = text_height
                target_w = width
                
                override_rect = job['settings'].get('override_rect') if job.get('settings') else None
                
                if override_rect:
                    x1 = override_rect.get('x1')
                    y1 = override_rect.get('y1')
                    x2 = override_rect.get('x2')
                    y2 = override_rect.get('y2')

                    x_local = x1 - layout.offset_x_mm
                    y_local = y1 - layout.offset_y_mm
                    
                    if x2 is not None and y2 is not None:
                        manual_w = abs(x2 - x1)
                        manual_h = abs(y2 - y1)
                        scale_factor = min(manual_w / width, manual_h / height) if width > 0 and height > 0 else 1.0
                        final_height = text_height * scale_factor
                        target_w = manual_w
                        debug_print(f"Using manual bounding box override: {override_rect}")
                    else:
                        final_height = text_height
                        target_w = width
                        debug_print(f"Using manual start point override (natural dimensions): X={x1}, Y={y1}")
                        
                    position = (x_local, y_local, final_height)

                else:
                    position = layout.find_empty_space(width, height, text_height)
                    if position:
                        _, _, final_height = position
                        target_w = width * (final_height / text_height)

                if not position:
                    debug_print(f"No space for '{name}' — board full")
                    with queue_lock:
                        job_mgr.update_job(job['id'], status='failed', error='Board is full! Reset board and click Redo.')
                    processing = False
                    continue

                x_local, y_local, final_height = position
                x_machine = x_local + layout.offset_x_mm
                y_machine  = y_local + layout.offset_y_mm

                gcode = gcode_gen.generate(
                    name, x_machine, y_machine, target_w, final_height
                )
                
                actual_w = target_w
                actual_h = final_height
                
                settings = {
                    'x_local': round(x_local, 2),
                    'y_local': round(y_local, 2),
                    'x_machine': round(x_machine, 2),
                    'y_machine': round(y_machine, 2),
                    'width': round(actual_w, 2),
                    'height': round(actual_h, 2),
                    'text_height': round(final_height, 2),
                    'font': gcode_gen.font_key,
                    'passes': laser_settings.get('passes', 1),
                    'power': gcode_gen.laser_power,
                    'speed': gcode_gen.speed,
                    'bold_repeats': config.get('text_settings.bold_repeats', 1),
                    'bold_offset_mm': config.get('text_settings.bold_offset_mm', 0.15)
                }
                job_mgr.update_job(job['id'], settings=settings)
                job_mgr.save_gcode(job['id'], gcode)

                debug_print(
                    f"Engraving '{name}': "
                    f"local=({x_local:.1f},{y_local:.1f})  "
                    f"machine=({x_machine:.1f},{y_machine:.1f})  "
                    f"size={actual_w:.1f}x{actual_h:.1f} mm  "
                    f"height={final_height:.1f} mm"
                )

                success, message = _run_engrave(job, gcode, name, laser, obs)
                
                if success:
                    layout.add_placement(name, x_local, y_local, actual_w, actual_h, final_height)

            with queue_lock:
                if success:
                    job_mgr.update_job(job['id'], status='finished')
                    debug_print(f'Completed: {name}')
                else:
                    if "stopped" in message.lower() or "alarm" in message.lower():
                        job_mgr.update_job(job['id'], status='stopped', error=message)
                    else:
                        job_mgr.update_job(job['id'], status='failed', error=message)
                    debug_print(f'Failed/Stopped: {name} — {message}')

                processing = False

        except Exception as e:
            debug_print(f'Queue processing error: {e}')
            if 'job' in locals() and job:
                job_mgr.update_job(job['id'], status='failed', error=str(e))
            with queue_lock:
                processing = False
            time.sleep(5)


def _run_engrave(job, gcode, name, laser, obs):
    """Run a single engrave job: fire LED on, engrave, LED to end value."""
    # led_pwm and led_pwm_end are stored as 0-100 percent.
    # M67 Q value is also 0-100 percent in FluidNC.
    led_pwm = int(config.get('laser_settings.led_pwm', 0))
    led_pwm = max(0, min(100, led_pwm))

    led_pwm_end = int(config.get('laser_settings.led_pwm_end', 0))
    led_pwm_end = max(0, min(100, led_pwm_end))

    if led_pwm > 0:
        debug_print(f'LED on: M67 E0 Q{led_pwm}')
        laser.send_command(f'M67 E0 Q{led_pwm}')

    if obs:
        obs.on_engrave_start(name=name)

    try:
        success, message = laser.send_gcode(gcode.split('\n'))
    finally:
        debug_print(f'LED end: M67 E0 Q{led_pwm_end}')
        laser.send_command(f'M67 E0 Q{led_pwm_end}')

    if obs:
        obs.on_engrave_finish(name=name, success=success)
        
    return success, message


def _build_gcode_gen():
    gen = GCodeGenerator()
    debug_print(
        f'GCodeGenerator: font={gen.font_key}  power={gen.laser_power}%  '
        f'speed={gen.speed} mm/min  spindle_max={gen.spindle_max}'
    )
    return gen


def main():
    print('=' * 50)
    print('TwitchLaser - Twitch-controlled Laser Engraver')
    print('=' * 50)
    print('\nInitializing components...')
    print('NOTE: Laser, OBS, Twitch, and Camera connect in the background.')
    print('      The web UI will be available immediately.')

    # ── Laser ─────────────────────────────────────────────
    # LaserController.__init__ now returns immediately; the monitor
    # thread handles the initial connect attempt in the background.
    print('Laser controller initializing (background)...')
    laser = LaserController()

    # ── Layout ────────────────────────────────────────────
    ea = config.get('engraving_area', {})
    layout = LayoutManager(
        width_mm          = ea.get('active_width_mm',   None),
        height_mm         = ea.get('active_height_mm',  None),
        machine_width_mm  = ea.get('machine_width_mm',  None),
        machine_height_mm = ea.get('machine_height_mm', None),
        offset_x_mm       = ea.get('offset_x_mm',       0.0),
        offset_y_mm       = ea.get('offset_y_mm',       0.0),
    )
    print(f'Layout: active {layout.width_mm}x{layout.height_mm} mm  '
          f'offset ({layout.offset_x_mm},{layout.offset_y_mm})')

    # ── G-code generator ──────────────────────────────────
    gcode_gen = _build_gcode_gen()

    # ── OBS controller ───────────────────────────────────
    # OBSController.__init__ now returns immediately; connection happens
    # in a background thread and retries every 15s until OBS is reachable.
    print('OBS controller initializing (background)...')
    obs = OBSController()

    # ── Twitch monitor ───────────────────────────────────
    # TwitchMonitor.start() already spawns a daemon thread; always non-blocking.
    print('Starting Twitch monitor (background)...')
    twitch = TwitchMonitor(enqueue_callback=enqueue_name)
    if config.get('twitch', {}).get('enabled', True):
        twitch.start()

    # ── Camera stream ────────────────────────────────────
    camera = None
    if CAMERA_AVAILABLE and config.get('camera_enabled', True):
        print('Starting camera stream (background)...')
        camera = CameraStream()
        camera.start()
    elif not config.get('camera_enabled', True):
        print('Camera stream disabled in config. Skipping initialization.')

    # ── Queue processor thread ─────────────────────────────
    print('Starting queue processor...')
    queue_thread = threading.Thread(
        target=process_queue,
        args=(laser, layout, gcode_gen, obs),
        daemon=True,
    )
    queue_thread.start()

    # ── Handle Graceful Exit for Systemd ───────────────────
    def sigterm_handler(signum, frame):
        print('\nSIGTERM received, shutting down gracefully...')
        if twitch: twitch.stop()
        if laser:  laser.disconnect()
        if camera: camera.stop()
        print('Goodbye!')
        os._exit(0)

    signal.signal(signal.SIGTERM, sigterm_handler)
    signal.signal(signal.SIGINT, sigterm_handler)

    # ── Web server ─────────────────────────────────────────
    print('Starting web server...')
    init_web_server(laser, layout, gcode_gen, twitch, camera, job_mgr, obs)

    hostname = config.get('hostname', 'twitchlaser')
    port     = 5000
    print(f'\n{"=" * 50}')
    print(f'Web interface: http://{hostname}.local:{port}')
    print(f'               http://localhost:{port}')
    print(f'{"=" * 50}\n')

    run_server(host='0.0.0.0', port=port)

if __name__ == '__main__':
    main()
