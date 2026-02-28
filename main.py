#!/usr/bin/env python3
"""
TwitchLaser - Twitch-controlled laser engraver
Main application entry point
"""

import sys
import time
import os
import threading

from config import config, debug_print
from laser_controller import LaserController
from layout_manager import LayoutManager
from gcode_generator import GCodeGenerator, FONT_PROFILES
from twitch_monitor import TwitchMonitor
from obs_controller import OBSController
from job_manager import JobManager

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

            # Check if this job is a redo (already has gcode generated)
            gcode_path = job_mgr.get_gcode_path(job['id'])
            if gcode_path and os.path.exists(gcode_path):
                # It's a REDO job
                with open(gcode_path, 'r') as f:
                    gcode = f.read()
                
                # Assume settings were copied
                actual_w = job['settings'].get('width', 0)
                actual_h = job['settings'].get('height', 0)
                x_local = job['settings'].get('x_local', 0)
                y_local = job['settings'].get('y_local', 0)
                final_height = job['settings'].get('text_height', 0)
                
                success, message = _run_engrave(job, gcode, name, laser, obs)
                
            else:
                # NEW JOB
                text_height    = config.get('text_settings.initial_height_mm', 5.0)
                laser_settings = config.get('laser_settings', {})

                # Estimate dimensions
                # We do a quick dry-run generate of the raw commands to get exact mm width
                _, _, _, raw_width = gcode_gen._get_ttf_commands(name, text_height)
                width = raw_width
                height = text_height
                
                # Check if UI sent manual coordinate overrides
                override_rect = job['settings'].get('override_rect') if job.get('settings') else None
                
                if override_rect:
                    # User manually specified coordinates
                    x1 = override_rect.get('x1')
                    y1 = override_rect.get('y1')
                    x2 = override_rect.get('x2')
                    y2 = override_rect.get('y2')

                    # Convert start point to local coordinates
                    x_local = x1 - layout.offset_x_mm
                    y_local = y1 - layout.offset_y_mm
                    
                    if x2 is not None and y2 is not None:
                        # Full bounding box provided, scale text to fit inside
                        manual_w = abs(x2 - x1)
                        manual_h = abs(y2 - y1)
                        
                        scale_factor = min(manual_w / width, manual_h / height) if width > 0 and height > 0 else 1.0
                        final_height = text_height * scale_factor
                        debug_print(f"Using manual bounding box override: {override_rect}")
                    else:
                        # Only start coordinates provided, use default text height
                        final_height = text_height
                        debug_print(f"Using manual start point override (natural dimensions): X={x1}, Y={y1}")
                        
                    position = (x_local, y_local, final_height)

                else:
                    # Standard auto-placement
                    position = layout.find_empty_space(width, height, text_height)

                if not position:
                    debug_print(f"No space for '{name}' — requeueing")
                    with queue_lock:
                        job_mgr.update_job(job['id'], status='pending')
                    processing = False
                    time.sleep(5)
                    continue

                x_local, y_local, final_height = position
                x_machine = x_local + layout.offset_x_mm
                y_machine  = y_local + layout.offset_y_mm

                # Dry run again to get scaled bounds
                gcode = gcode_gen.generate(
                    name, x_machine, y_machine, width, final_height
                )
                
                actual_w = width
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
                
                # Only add placement to board if it actually ran successfully
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
    if obs:
        obs.on_engrave_start(name=name)

    success, message = laser.send_gcode(gcode.split('\n'))

    if obs:
        obs.on_engrave_finish(name=name, success=success)
        
    return success, message


def _build_gcode_gen():
    # In earlier versions GCodeGenerator took kwargs.
    # It now safely self-initializes directly from the JSON config singleton.
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

    # ── Laser ─────────────────────────────────────────────
    print('Connecting to FluidNC...')
    laser = LaserController()
    if not laser.connected:
        print('WARNING: FluidNC not connected.')

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
    print('Initializing OBS controller...')
    obs = OBSController()
    if obs.is_connected():
        print('OBS WebSocket connected')
    else:
        print('OBS not connected (disabled or unavailable)')

    # ── Twitch monitor ───────────────────────────────────
    twitch = TwitchMonitor(enqueue_callback=enqueue_name)
    if config.get('twitch_enabled', True):
        print('Starting Twitch monitor...')
        if twitch.start():
            print('Twitch monitor started')
        else:
            print('WARNING: Twitch monitor failed to start')

    camera = None

    # ── Queue processor thread ─────────────────────────────
    print('Starting queue processor...')
    queue_thread = threading.Thread(
        target=process_queue,
        args=(laser, layout, gcode_gen, obs),
        daemon=True,
    )
    queue_thread.start()

    # ── Web server ─────────────────────────────────────────
    print('Starting web server...')
    init_web_server(laser, layout, gcode_gen, twitch, camera, job_mgr, obs)

    hostname = config.get('hostname', 'twitchlaser')
    port     = 5000
    print(f'\n{"=" * 50}')
    print(f'Web interface: http://{hostname}.local:{port}')
    print(f'               http://localhost:{port}')
    print(f'{"=" * 50}\n')

    try:
        run_server(host='0.0.0.0', port=port)
    except KeyboardInterrupt:
        print('\nShutting down...')
        if twitch: twitch.stop()
        if laser:  laser.disconnect()
        print('Goodbye!')
        sys.exit(0)


if __name__ == '__main__':
    main()
