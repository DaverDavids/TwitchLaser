#!/usr/bin/env python3
"""
TwitchLaser - Twitch-controlled laser engraver
Main application entry point
"""

import sys
import time
import threading
from collections import deque

from config import config, debug_print
from laser_controller import LaserController
from layout_manager import LayoutManager
from gcode_generator import GCodeGenerator, FONT_PROFILES
from twitch_monitor import TwitchMonitor

CAMERA_AVAILABLE = False
CameraStream = None

from web_server import init_web_server, run_server

# ── Queue state ───────────────────────────────────────────────
engraving_queue = deque()
queue_lock      = threading.Lock()
processing      = False


def enqueue_name(name, source='twitch'):
    with queue_lock:
        engraving_queue.append({'name': name, 'source': source})
    debug_print(f'Queued: {name}  (from {source})')


def process_queue(laser, layout, gcode_gen):
    """Worker thread: pop names from queue, place them, engrave."""
    global processing

    while True:
        try:
            with queue_lock:
                if not engraving_queue or processing:
                    pass
                else:
                    processing = True
                    job = engraving_queue.popleft()

            if not processing:
                time.sleep(1)
                continue

            name = job['name']
            debug_print(f'Processing: {name}')

            text_height    = config.get('text_settings.initial_height_mm', 5.0)
            laser_settings = config.get('laser_settings', {})

            # True bounding-box size for this name at the requested height
            width, height = gcode_gen.estimate_dimensions(name, text_height)

            # Find a free spot (auto-shrinks if board is filling up)
            position = layout.find_empty_space(width, height, text_height)

            if not position:
                debug_print(f"No space for '{name}' — requeueing")
                with queue_lock:
                    engraving_queue.append(job)
                processing = False
                time.sleep(5)
                continue

            x_local, y_local, final_height = position
            x_machine = x_local + layout.offset_x_mm
            y_machine  = y_local + layout.offset_y_mm

            gcode, actual_w, actual_h = gcode_gen.text_to_gcode(
                name,
                x_machine,
                y_machine,
                final_height,
                passes=laser_settings.get('passes', 1),
            )

            debug_print(
                f"Engraving '{name}': "
                f"local=({x_local:.1f},{y_local:.1f})  "
                f"machine=({x_machine:.1f},{y_machine:.1f})  "
                f"size={actual_w:.1f}x{actual_h:.1f} mm  "
                f"height={final_height:.1f} mm"
            )

            success, message = laser.send_gcode(gcode.split('\n'))

            if success:
                layout.add_placement(name, x_local, y_local, actual_w, actual_h, final_height)
                debug_print(f'Completed: {name}')
            else:
                debug_print(f'Failed: {name} — {message}')
                with queue_lock:
                    engraving_queue.appendleft(job)   # retry at front of queue

            processing = False

        except Exception as e:
            debug_print(f'Queue processing error: {e}')
            processing = False
            time.sleep(5)


def _build_gcode_gen():
    """Create GCodeGenerator from current config values."""
    laser_settings = config.get('laser_settings', {})
    text_settings  = config.get('text_settings',  {})

    font_key = text_settings.get('font', 'simplex')
    ttf_path = text_settings.get('ttf_path', None)

    gen = GCodeGenerator(
        laser_power      = laser_settings.get('power_percent',    50),
        speed_mm_per_min = laser_settings.get('speed_mm_per_min', 1000),
        spindle_max      = laser_settings.get('spindle_max',       1000),
        font_key         = font_key,
        ttf_path         = ttf_path,
    )
    debug_print(
        f'GCodeGenerator: font={font_key}  power={gen.laser_power}%  '
        f'speed={gen.speed} mm/min  spindle_max={gen.spindle_max}'
    )
    return gen


def main():
    print('=' * 50)
    print('TwitchLaser - Twitch-controlled Laser Engraver')
    print('=' * 50)
    print('\nInitializing components...')

    # ── Laser ─────────────────────────────────────────────────
    print('Connecting to FluidNC...')
    laser = LaserController()
    if not laser.connected:
        print('WARNING: FluidNC not connected.')

    # ── Layout ────────────────────────────────────────────────
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

    # ── G-code generator ──────────────────────────────────────
    gcode_gen = _build_gcode_gen()

    # ── Twitch monitor ────────────────────────────────────────
    twitch = TwitchMonitor(enqueue_callback=enqueue_name)
    if config.get('twitch_enabled', True):
        print('Starting Twitch monitor...')
        if twitch.start():
            print('Twitch monitor started')
        else:
            print('WARNING: Twitch monitor failed to start')

    camera = None   # camera disabled

    # ── Queue processor thread ────────────────────────────────
    print('Starting queue processor...')
    queue_thread = threading.Thread(
        target=process_queue,
        args=(laser, layout, gcode_gen),
        daemon=True,
    )
    queue_thread.start()

    # ── Web server ────────────────────────────────────────────
    print('Starting web server...')
    init_web_server(laser, layout, gcode_gen, twitch, camera, engraving_queue)

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
