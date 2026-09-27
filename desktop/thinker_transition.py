"""Full-resolution desktop binding to the same compositor used by ESP32."""
import ctypes

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from renderer import LIBRARY

MODEL_IDS = {"sol": 1, "terra": 2, "luna": 3, "astra": 4}


class ThinkerTransition:
    DURATION = 2.60
    INTERRUPTED_DURATION = 0.65

    def __init__(self):
        self.library = ctypes.CDLL(str(LIBRARY))
        self.render = self.library.pet_thinker_render
        self.render.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                               ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                               ctypes.c_float, ctypes.c_int]
        self.render.restype = None

    def frame(self, before, after, source, target, progress):
        if before.size() != after.size():
            before = before.scaled(after.size(), Qt.AspectRatioMode.IgnoreAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
        before = before.convertToFormat(QImage.Format.Format_RGB32)
        after = after.convertToFormat(QImage.Format.Format_RGB32)
        output = QImage(after.size(), QImage.Format.Format_RGB32)
        buffers = [(ctypes.c_ubyte * image.sizeInBytes()).from_buffer(image.bits())
                   for image in (before, after, output)]
        self.render(*buffers, after.width(), after.height(), MODEL_IDS.get(source, 0),
                    MODEL_IDS.get(target, 0), progress, 32)
        return output
