"""
Camera Stream - USB webcam streaming with MJPEG
"""
import cv2
import threading
import time
from config import debug_print

class CameraStream:
    def __init__(self, camera_index=0, width=640, height=480, fps=15):
        self.camera_index = camera_index
        self.width = width
        self.height = height
        self.fps = fps
        self.camera = None
        self.frame = None
        self.running = False
        self.thread = None
        self.lock = threading.Lock()

    def _try_open_camera(self, index):
        """Helper to try opening a specific camera index"""
        debug_print(f"Testing camera index {index}...")
        
        # Try V4L2 first
        cam = cv2.VideoCapture(index, cv2.CAP_V4L2)
        if not cam.isOpened():
            # Try default backend
            cam = cv2.VideoCapture(index)
            
        if not cam.isOpened():
            return None
            
        # Verify it actually returns frames (some Pi hardware devices open but return no video)
        ret, _ = cam.read()
        if not ret:
            cam.release()
            return None
            
        return cam

    def start(self):
        """Start camera capture"""
        if self.running:
            debug_print("Camera already running")
            return True

        try:
            # 1. Try the configured index first
            self.camera = self._try_open_camera(self.camera_index)
            
            # 2. If it fails, auto-scan indices 0 through 10 (Pi often has hardware nodes at 0-2)
            if self.camera is None:
                debug_print(f"Failed to open configured camera {self.camera_index}. Auto-scanning for working camera...")
                for i in range(10):
                    if i == self.camera_index:
                        continue
                    self.camera = self._try_open_camera(i)
                    if self.camera is not None:
                        debug_print(f"Successfully found working camera at index {i}!")
                        self.camera_index = i  # Update to the working index
                        break
                        
            if self.camera is None:
                debug_print("Failed to find any working cameras entirely. Check /dev/video* permissions or physical connection.")
                return False

            # Request MJPG format from the camera if available, drastically improves FPS on Pi USB
            self.camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
            
            self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            self.camera.set(cv2.CAP_PROP_FPS, self.fps)

            # Read back actual settings
            actual_w = self.camera.get(cv2.CAP_PROP_FRAME_WIDTH)
            actual_h = self.camera.get(cv2.CAP_PROP_FRAME_HEIGHT)
            actual_fps = self.camera.get(cv2.CAP_PROP_FPS)

            self.running = True
            self.thread = threading.Thread(target=self._capture_loop, daemon=True)
            self.thread.start()

            debug_print(f"Camera started: {actual_w}x{actual_h} @ {actual_fps}fps")
            return True

        except Exception as e:
            debug_print(f"Camera start error: {e}")
            return False

    def _capture_loop(self):
        """Continuous frame capture loop"""
        frame_time = 1.0 / self.fps
        consecutive_failures = 0

        while self.running:
            try:
                start = time.time()

                ret, frame = self.camera.read()

                if ret:
                    consecutive_failures = 0
                    with self.lock:
                        self.frame = frame
                else:
                    consecutive_failures += 1
                    if consecutive_failures % 30 == 0:
                        debug_print(f"Failed to read frame {consecutive_failures} times in a row")

                # Maintain target FPS
                elapsed = time.time() - start
                if elapsed < frame_time:
                    time.sleep(frame_time - elapsed)

            except Exception as e:
                debug_print(f"Capture loop error: {e}")
                time.sleep(1)

    def get_frame(self):
        """Get latest frame as JPEG bytes"""
        with self.lock:
            if self.frame is None:
                return None

            try:
                # Encode frame as JPEG
                ret, buffer = cv2.imencode('.jpg', self.frame, 
                                          [cv2.IMWRITE_JPEG_QUALITY, 80])
                if ret:
                    return buffer.tobytes()
            except Exception as e:
                debug_print(f"Frame encode error: {e}")

        return None

    def stop(self):
        """Stop camera capture"""
        self.running = False

        if self.thread:
            self.thread.join(timeout=2)

        if self.camera:
            self.camera.release()

        debug_print("Camera stopped")

    def is_running(self):
        """Check if camera is running"""
        return self.running and self.thread and self.thread.is_alive()

    def get_status(self):
        """Get camera status"""
        if not self.is_running():
            return "Stopped"

        with self.lock:
            if self.frame is not None:
                h, w = self.frame.shape[:2]
                return f"Running: {w}x{h}"

        return "Running: No frames"