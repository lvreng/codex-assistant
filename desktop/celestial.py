"""Desktop-only celestial artwork, matched to explicit per-conversation models."""
from __future__ import annotations

import re
import time
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter

from celestial_motion import AstraOptions, CelestialSurface, MotionClock
from thinker_transition import ThinkerTransition

ARTWORK = Path(__file__).resolve().parents[1] / "output/imagegen/celestial-v1"
MODELS = {
    # Asset slugs retain their original filenames across model generations.
    "sol": ("gpt-5.6-sol", "GPT-6 Sol", "SUN", "#ffca79"),
    "terra": ("gpt-5.6-terra", "GPT-5.6 Terra", "EARTH", "#8eceff"),
    "luna": ("gpt-5.6-luna", "GPT-6 Luna", "MOON", "#dddfe7"),
    "astra": ("gpt-6-astra", "GPT-6 Astra", "STARS", "#b8d9ef"),
}
MODEL_PATTERN = re.compile(
    r"(?:^|/)(gpt-(?:5\.6|6)-(sol|terra|luna)|gpt-6-(astra))"
    r"(?:-(?:\d{4}-\d{2}-\d{2}|[a-z]))?$"
)


def model_art(model):
    if not isinstance(model, str):
        return None
    normalized = re.sub(r"[\s_]+", "-", model.strip().lower())
    match = MODEL_PATTERN.search(normalized)
    return (match.group(2) or match.group(3)) if match else None


class CelestialLayer:
    DURATION = ThinkerTransition.DURATION
    AREA = QRectF(0, 64, 556, 416)
    BACKGROUND = QColor("#071019")

    def __init__(self):
        self.pictures = {}
        self.current = None
        self.previous = None
        self.changed = 0.0
        self.clock = MotionClock()
        self.astra_options = AstraOptions()
        self.transition = None
        self.duration = self.DURATION
        self.source_picture = None
        self.source_key = None
        self.last_picture = None

    def set_astra_options(self, options, now=None, animate=True):
        self.astra_options = AstraOptions.validated(
            options.style, options.minimum, options.maximum)
        if "astra" in self.pictures:
            phase = self.clock.sample(time.monotonic() if now is None else now).phase
            self.pictures["astra"].configure_astra(
                self.astra_options, phase if animate else None)

    def preload(self, quality=1.0):
        for key, (slug, *_rest) in MODELS.items():
            if key not in self.pictures:
                try:
                    self.pictures[key] = CelestialSurface(key, ARTWORK / (slug + ".png"))
                    if key == "astra":
                        self.pictures[key].configure_astra(self.astra_options)
                except OSError:
                    pass
            if key in self.pictures:
                self.pictures[key].warm(quality)

    def select(self, key, now=None):
        now = time.monotonic() if now is None else now
        if key and key not in self.pictures:
            try:
                self.pictures[key] = CelestialSurface(key, ARTWORK / (MODELS[key][0] + ".png"))
                if key == "astra":
                    self.pictures[key].configure_astra(self.astra_options)
            except OSError:
                key = None
        if key != self.current:
            interrupted = bool(self.previous and self.progress(now) < 1 and self.last_picture is not None)
            self.source_picture = QImage(self.last_picture) if self.last_picture is not None and self.current else None
            self.source_key = None if interrupted else self.current
            self.duration = ThinkerTransition.INTERRUPTED_DURATION if interrupted else self.DURATION
            self.previous, self.current = self.current, key
            self.changed = now
            if key is None:
                self.last_picture = self.source_picture = None
        return self.current

    def progress(self, now):
        linear = min(1.0, max(0.0, (now - self.changed) / self.duration))
        return linear * linear * (3 - 2 * linear)

    def set_state(self, state, connected=True, now=None):
        self.clock.set_state(state, connected, time.monotonic() if now is None else now)

    def _image(self, painter, key, opacity, motion, transitioning=False):
        if not key or opacity <= 0:
            return
        surface = self.pictures[key]
        quality = painter.deviceTransform().m11() * (
            surface.TRANSITION_QUALITY if transitioning else 1)
        picture = (surface.composited_frame(motion, quality) if transitioning
                   else surface.frame(motion, quality))
        target = QRectF(0, self.AREA.y(), 624, self.AREA.height())
        painter.save()
        painter.setOpacity(opacity)
        painter.drawImage(target, picture, QRectF(picture.rect()))
        if not transitioning:
            painter.translate(0, self.AREA.y())
            surface.paint_light(painter, motion)
        painter.restore()

    def paint(self, painter, detail_left, now=None):
        if not self.current or detail_left <= 0:
            return
        now = time.monotonic() if now is None else now
        motion = self.clock.sample(now)
        quality = min(2.0, max(1.0, painter.deviceTransform().m11()))
        picture = self.scene(self.current, motion, quality)
        progress = min(1.0, max(0.0, (now-self.changed)/self.duration))
        if self.previous and progress < 1:
            if self.source_picture is None:
                self.source_picture = self.scene(self.previous, motion, quality)
            if self.transition is None:
                self.transition = ThinkerTransition()
            picture = self.transition.frame(self.source_picture, picture,
                                            self.source_key, self.current, progress)
        else:
            self.source_picture = None
        self.last_picture = picture
        painter.save()
        painter.setClipRect(self.AREA.intersected(QRectF(0, 0, detail_left, 480)))
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(QRectF(0, 64, 624, 416), picture)
        painter.restore()

    def scene(self, key, motion, quality=1.0):
        picture = QImage(round(624*quality), round(416*quality), QImage.Format.Format_RGB32)
        picture.fill(self.BACKGROUND)
        painter = QPainter(picture)
        painter.scale(quality, quality)
        painter.translate(0, -64)
        self._paint_scene(painter, key, motion)
        painter.end()
        return picture

    def _paint_scene(self, painter, key, motion):
        painter.save()
        painter.setClipRect(self.AREA)
        painter.fillRect(self.AREA, self.BACKGROUND)
        self._image(painter, key, 1, motion)
        self.paint_overlays(painter, key)
        painter.restore()

    def paint_overlays(self, painter, key):
        painter.save()
        # Fade only the artwork edges, keeping the native header and data readable.
        solar = float(key == "sol")
        vertical = QLinearGradient(0, 64, 0, 480)
        vertical.setColorAt(0, self.BACKGROUND)
        vertical.setColorAt(0.09, QColor(7, 16, 25, 0))
        vertical.setColorAt(0.73 + 0.23 * solar, QColor(7, 16, 25, 0))
        vertical.setColorAt(0.92 + 0.07 * solar, QColor(7, 16, 25, round(238 * (1 - solar))))
        vertical.setColorAt(1, QColor(7, 16, 25, round(255 * (1 - solar))))
        painter.fillRect(self.AREA, vertical)
        right_start = 510 + 34 * solar
        right = QLinearGradient(right_start, 0, 556, 0)
        right.setColorAt(0, QColor(7, 16, 25, 0))
        right.setColorAt(1, self.BACKGROUND)
        painter.fillRect(QRectF(right_start, 64, 556 - right_start, 416), right)

        for key, opacity in ((key, 1),):
            if not key or opacity <= 0:
                continue
            _slug, name, body, accent = MODELS[key]
            lift = float(key == "sol")
            # Change docks with a short dissolve, not a flight across the hot disc.
            layouts = (
                (1 - lift, 30, 413 + 10 * lift, 28, 432 + 10 * lift, 21),
                (lift, 26, 84 + 10 * (1 - lift), 66, 78 + 10 * (1 - lift), 18),
            )
            for weight, bx, by, nx, ny, size in layouts:
                if weight < 1e-5:
                    continue
                painter.setOpacity(opacity * weight)
                font = QFont("DejaVu Sans")
                font.setPixelSize(11)
                painter.setFont(font)
                painter.setPen(QColor(accent))
                painter.drawText(QRectF(bx, by, 480, 18), Qt.AlignmentFlag.AlignLeft, body)
                font.setPixelSize(size)
                font.setWeight(QFont.Weight.Medium)
                painter.setFont(font)
                painter.setPen(QColor("#edf3f9"))
                painter.drawText(QRectF(nx, ny, 490, 32), Qt.AlignmentFlag.AlignLeft, name)
        painter.restore()
