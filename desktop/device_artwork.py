"""The same desktop artwork compositor, without a desktop window."""
import time

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QColor, QImage, QImageWriter, QPainter

from celestial import CelestialLayer
from celestial_motion import AstraOptions


class DeviceArtwork:
    def __init__(self):
        self.layer = CelestialLayer()
        self.layer.preload(1)

    def configure(self, key, state, connected, style="linked", minimum=10,
                  maximum=15, preview=False, now=None):
        now = time.monotonic() if now is None else now
        self.layer.select(key, now)
        self.layer.set_astra_options(AstraOptions(style, minimum, maximum), now)
        if preview:
            state = state if state in (1, 2, 4, 8) else 1
            connected = True
        self.layer.set_state(state, connected, now)

    def image(self, now=None):
        if self.layer.current is None:
            return QImage()
        image = QImage(560, 416, QImage.Format.Format_RGB888)
        image.fill(QColor("#071019"))
        painter = QPainter(image)
        painter.translate(0, -64)
        self.layer.paint(painter, 556, now)
        painter.end()
        return image

    @staticmethod
    def encode(image, quality=74):
        output = QBuffer()
        output.open(QIODevice.OpenModeFlag.WriteOnly)
        writer = QImageWriter(output, b"JPEG")
        writer.setQuality(quality)
        writer.setOptimizedWrite(True)
        writer.setProgressiveScanWrite(False)
        if not writer.write(image):
            raise RuntimeError("Cannot encode device artwork")
        return bytes(output.data())

    def frame(self, now=None, quality=74):
        image = self.image(now)
        return b"" if image.isNull() else self.encode(image, quality)
