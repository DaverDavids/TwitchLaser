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

    def start(self):
        """Start camera capture"""
        if self.running:
            debug_print("Camera already running")
            return True

        try:
            self.camera = cv2.VideoCapture(self.camera_index)

            if not self.camera.isOpened():
                debug_print(f"Failed to open camera {self.camera_index}")
                return False

            self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            self.camera.set(cv2.CAP_PROP_FPS, self.fps)

            self.running = True
            self.thread = threading.Thread(target=self._capture_loop, daemon=True)
            self.thread.start()

            debug_print(f"Camera started: {self.width}x{self.height} @ {self.fps}fps")
            return True

        except Exception as e:
            debug_print(f"Camera start error: {e}")
            return False

    def _capture_loop(self):
        """Continuous frame capture loop"""
        frame_time = 1.0 / self.fps

        while self.running:
            try:
                start = time.time()

                ret, frame = self.camera.read()

                if ret:
                    with self.lock:
                        self.frame = frame
                else:
                    debug_print("Failed to read frame")

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
