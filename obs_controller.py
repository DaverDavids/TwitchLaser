"""
OBS Controller - Controls OBS Studio over WebSocket (OBS 28+ built-in)

Protocol: OBS WebSocket v5 (obsws-python library)

Supported action types per event (engrave_start / engrave_finish):

  show_source    - Make a scene item visible
                   { "type": "show_source", "scene": "Live", "source": "LaserOverlay" }

  hide_source    - Make a scene item invisible
                   { "type": "hide_source", "scene": "Live", "source": "LaserOverlay" }

  switch_scene   - Switch to a different scene
                   { "type": "switch_scene", "scene": "Laser Cam" }

  trigger_hotkey - Trigger a hotkey by its OBS name
                   { "type": "trigger_hotkey", "hotkey": "OBSBasic.StartRecording" }

  set_text       - Update a Text (GDI+/Freetype) source's text
                   { "type": "set_text", "scene": "Live", "source": "NowEngravingText",
                     "text": "Now engraving: {name}" }
                   Use {name} as a placeholder for the subscriber name.

Config lives in config.yaml under the 'obs' key:

  obs:
    enabled: true
    host: 192.168.1.100
    port: 4455
    password: your_obs_ws_password
    engrave_start_actions:
      - { type: show_source, scene: Live, source: LaserCamOverlay }
      - { type: set_text,   scene: Live, source: NowEngravingLabel, text: "Engraving: {name}" }
    engrave_finish_actions:
      - { type: hide_source, scene: Live, source: LaserCamOverlay }
"""

import threading
import time
from config import debug_print, config

try:
    import obsws_python as obs
    _OBS_AVAILABLE = True
except ImportError:
    _OBS_AVAILABLE = False
    debug_print('obsws-python not installed; OBS integration disabled')


class OBSController:
    def __init__(self):
        self._client  = None
        self._lock    = threading.Lock()
        self._enabled = False
        self._connect()

    # ── Connection ─────────────────────────────────────────
    def _connect(self):
        if not _OBS_AVAILABLE:
            return

        obs_cfg = config.get('obs', {})
        if not obs_cfg.get('enabled', False):
            debug_print('OBS integration disabled in config')
            return

        host     = obs_cfg.get('host',     '127.0.0.1')
        port     = int(obs_cfg.get('port', 4455))
        password = obs_cfg.get('password', '')

        try:
            self._client = obs.ReqClient(
                host=host, port=port, password=password, timeout=5)
            self._enabled = True
            debug_print(f'OBS WebSocket connected: {host}:{port}')
        except Exception as e:
            debug_print(f'OBS WebSocket connection failed: {e}')
            self._client  = None
            self._enabled = False

    def reconnect(self):
        """Re-read config and reconnect."""
        with self._lock:
            if self._client:
                try:
                    self._client.disconnect()
                except Exception:
                    pass
            self._client  = None
            self._enabled = False
        self._connect()
        return self._enabled

    def is_connected(self):
        return self._enabled and self._client is not None

    # ── Action dispatch ──────────────────────────────────
    def _run_action(self, action, name=''):
        """Execute a single action dict. name = subscriber name for {name} substitution."""
        if not self.is_connected():
            return

        atype  = action.get('type', '').lower()
        scene  = action.get('scene',  '')
        source = action.get('source', '')

        try:
            if atype == 'show_source':
                self._set_source_visible(scene, source, True)

            elif atype == 'hide_source':
                self._set_source_visible(scene, source, False)

            elif atype == 'switch_scene':
                self._client.set_current_program_scene(scene)
                debug_print(f'OBS: switched to scene "{scene}"')

            elif atype == 'trigger_hotkey':
                hotkey = action.get('hotkey', '')
                self._client.trigger_hotkey_by_name(hotkey)
                debug_print(f'OBS: triggered hotkey "{hotkey}"')

            elif atype == 'set_text':
                text = action.get('text', '').replace('{name}', name)
                self._set_text_source(scene, source, text)

            else:
                debug_print(f'OBS: unknown action type "{atype}"')

        except Exception as e:
            debug_print(f'OBS action "{atype}" failed: {e}')
            # Mark disconnected so next call will skip cleanly
            self._enabled = False

    def _set_source_visible(self, scene_name, source_name, visible):
        """Show or hide a source in a scene."""
        # Get the scene item ID first
        resp = self._client.get_scene_item_id(scene_name, source_name)
        item_id = resp.scene_item_id
        self._client.set_scene_item_enabled(
            scene_name, item_id, visible)
        state = 'shown' if visible else 'hidden'
        debug_print(f'OBS: {state} source "{source_name}" in "{scene_name}"')

    def _set_text_source(self, scene_name, source_name, text):
        """Update a Text GDI+/Freetype source."""
        self._client.set_input_settings(
            source_name, {'text': text}, overlay=True)
        debug_print(f'OBS: set "{source_name}" text to "{text}"')

    # ── Public event hooks ────────────────────────────────
    def on_engrave_start(self, name=''):
        """Call this when an engraving job begins."""
        actions = config.get('obs.engrave_start_actions', [])
        if not actions:
            return
        debug_print(f'OBS: running {len(actions)} start action(s) for "{name}"')
        with self._lock:
            for action in actions:
                self._run_action(action, name=name)

    def on_engrave_finish(self, name='', success=True):
        """Call this when an engraving job completes."""
        actions = config.get('obs.engrave_finish_actions', [])
        if not actions:
            return
        debug_print(f'OBS: running {len(actions)} finish action(s) for "{name}"')
        with self._lock:
            for action in actions:
                self._run_action(action, name=name)

    def test_action(self, action, name='test'):
        """Execute a single action immediately. Used by the web UI test button."""
        if not self.is_connected():
            return False, 'OBS not connected'
        try:
            with self._lock:
                self._run_action(action, name=name)
            return True, 'Action executed'
        except Exception as e:
            return False, str(e)
