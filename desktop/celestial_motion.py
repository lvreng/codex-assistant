"""Element-aware, CPU-rendered motion for the approved celestial photographs."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import heapq
import math
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush, QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient,
)

# Small remaps are faster and more predictable without OpenCV's large thread pool.
cv2.setNumThreads(1)
WIDTH, HEIGHT = 624, 416
CENTERS = {
    "sol": (231.6, 204.7, 140),
    "terra": (241, 207, 167),
    "luna": (237, 203, 161),
    "astra": (268, 206, 240),
}
ASTRA_DEFAULTS = {"classic": (5.0, 7.5), "linked": (10.0, 15.0)}


@dataclass(frozen=True)
class AstraOptions:
    style: str = "linked"
    minimum: float = 10.0
    maximum: float = 15.0

    @classmethod
    def validated(cls, style="linked", minimum=None, maximum=None):
        style = style if style in ASTRA_DEFAULTS else "linked"

        def seconds(value, default):
            try:
                value = float(value)
            except (TypeError, ValueError):
                value = default
            if not math.isfinite(value):
                value = default
            return round(min(120.0, max(1.0, value)), 1)

        low, high = ASTRA_DEFAULTS[style]
        low, high = seconds(minimum, low), seconds(maximum, high)
        return cls(style, low, max(low, high))


def animation_mode(state, connected=True):
    if not connected:
        return None
    if state in (1, 2, 8):
        return "working"
    return "completed" if state == 4 else None


@dataclass(frozen=True)
class Motion:
    phase: float
    activity: float
    visibility: float


@dataclass(frozen=True)
class StarLink:
    source: int
    target: int
    path: QPainterPath
    pen: QPen
    rgb: tuple
    bounds: QRectF
    image: QImage
    classic_image: QImage


class MotionClock:
    """Integrate speed analytically: packet repeats cannot restart a loop."""

    TAU = 0.65

    def __init__(self):
        self.mode = None
        self.changed = None
        self.origin = 0.0
        self.speed = self.energy = self.visibility = 0.0
        self.energy_velocity = self.visibility_velocity = 0.0

    def _values(self, now):
        dt = max(0.0, now - self.changed) if self.changed is not None else 0.0
        decay = math.exp(-dt / self.TAU)
        target_speed = {"working": 1.0, "completed": 0.62}.get(self.mode, 0.0)
        target_energy = float(self.mode == "working")
        target_visibility = float(self.mode is not None)
        phase = (self.origin + target_speed * dt
                 + (self.speed - target_speed) * self.TAU * (1 - decay))
        def settle(value, velocity, target):
            frequency = 2 / self.TAU
            remainder = value - target
            slope = velocity + frequency * remainder
            damp = math.exp(-frequency * dt)
            return (target + (remainder + slope * dt) * damp,
                    (velocity - frequency * slope * dt) * damp)

        energy, energy_velocity = settle(self.energy, self.energy_velocity, target_energy)
        visibility, visibility_velocity = settle(
            self.visibility, self.visibility_velocity, target_visibility)
        return (
            phase,
            target_speed + (self.speed - target_speed) * decay,
            energy, visibility, energy_velocity, visibility_velocity,
        )

    def set_state(self, state, connected, now):
        mode = animation_mode(state, connected)
        if self.changed is None or mode != self.mode:
            (self.origin, self.speed, self.energy, self.visibility,
             self.energy_velocity, self.visibility_velocity) = self._values(now)
            self.changed, self.mode = now, mode

    def sample(self, now):
        phase, _speed, energy, visibility, *_velocities = self._values(now)
        return Motion(phase, energy, visibility)


def smooth(low, high, value):
    t = np.clip((value - low) / (high - low), 0, 1)
    return t * t * (3 - 2 * t)


class CelestialSurface:
    """Warp local layers, not the picture frame; keep stars and limbs registered."""

    TRANSITION_QUALITY = 0.50
    SOL_CENTER = (278, 280, 258)
    SOL_SPIN = 0.105
    SOL_LIGHT_QUALITY = 1.25
    EJECTION_PERIODS = (14.8, 17.3, 19.1)
    EJECTION_ANGLES = (-0.78, -2.28, -1.43)
    RELAY_CHARGE = 3.28
    RELAY_RISE = 0.104
    RELAY_DECAY = 0.92
    STAR_PERIOD_MIN = 10.0
    STAR_PERIOD_MAX = 15.0
    RELAY_ORIGIN_DELAYS = (0.0, 1.86, 3.82, 5.76)
    CLASSIC_RELAY_CHARGE = 1.64
    CLASSIC_RELAY_RISE = 0.052
    CLASSIC_RELAY_DECAY = 0.46
    CLASSIC_PERIODS = (5.0, 5.37, 5.83, 6.29)
    CLASSIC_PERIOD_MIN = 5.0
    CLASSIC_PERIOD_MAX = 7.5
    CLASSIC_ORIGIN_DELAYS = (0.0, 0.93, 1.91, 2.88)
    STAR_COUNT = 96
    COMPLETED_STAR_COUNT = 38
    BUFFER_NAMES = (
        "quality size source x y dx dy r angle disc depth rim edge grid_y grid_x "
        "map_x map_y pixels display gain image land clouds cloud_pixels cloud_u "
        "cloud_v gas starlight star_pixels cities city_pixels city_regions lunar_albedo plasma_pixels "
        "city_u city_v city_grid_x city_grid_y city_gain city_emission composite "
        "solar_longitude solar_latitude solar_u solar_pixels solar_alpha solar_inverse solar_backdrop "
        "solar_weight solar_weight_inverse star_weights working_gas working_starlight "
        "gas_blend starlight_blend"
    ).split()

    def __init__(self, key: str, path: Path):
        self.key = key
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            raise OSError(f"Cannot load celestial artwork: {path.name}")
        self.original = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        self._buffers = OrderedDict()
        self.quality = None
        self.paths = self._paths()
        self.stars = []
        self.star_links = []
        self.astra_options = AstraOptions()
        self.astra_background_only = False
        self.star_transition = None
        self.earth_layers = None
        if key == "sol":
            self.solar_atlas = self._solar_atlas()
            self.solar_light = QImage(round(WIDTH * self.SOL_LIGHT_QUALITY),
                                      round(HEIGHT * self.SOL_LIGHT_QUALITY),
                                      QImage.Format.Format_ARGB32_Premultiplied)
        if key == "terra":
            self.earth_layers = self._earth_layers()
            self.city_atlas = self._city_atlas()
        self._prepare(1.0)

    def _quality(self, quality):
        # Multilayer planets need headroom for their light passes at high DPI.
        maximum = 1.75 if self.key in ("sol", "terra") else 2.0
        return min(maximum, max(1.0, round(quality * 4) / 4))

    def warm(self, quality):
        for value in (quality * self.TRANSITION_QUALITY, quality):
            self._prepare(self._quality(value))

    def _solar_atlas(self):
        """Wrap overlapping interior patches, never stretch the photographed limb."""
        width, height = 1536, 768
        longitude = np.linspace(-math.pi, math.pi, width, endpoint=False, dtype=np.float32)[None, :]
        latitude = np.linspace(-math.pi / 2, math.pi / 2, height, dtype=np.float32)[:, None]
        sx, sy, radius = CENTERS["sol"]
        qx, qy = self.original.shape[1] / WIDTH, self.original.shape[0] / HEIGHT
        atlas = np.zeros((height, width, 3), np.float32)
        weights = np.zeros((1, width, 1), np.float32)
        for center in (-math.tau / 3, 0, math.tau / 3):
            local = (longitude - center + math.pi) % math.tau - math.pi
            weight = (1 - smooth(math.pi / 3 - 0.18, math.pi / 3 + 0.18, np.abs(local)))[..., None]
            horizontal = radius * np.cos(latitude) * np.sin(local) * 0.995
            vertical = (radius * np.sin(latitude) * 0.995
                        + 5 * np.cos(latitude) * np.sin(local) * math.sin(center))
            patch = cv2.remap(
                self.original, ((sx + horizontal) * qx).astype(np.float32),
                ((sy + vertical) * qy).astype(np.float32), cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_REPLICATE)
            atlas += patch * weight
            weights += weight
        atlas = np.clip(atlas / np.maximum(weights, 1e-6), 0, 255).astype(np.uint8)
        # Repeated gutters keep bilinear samples continuous across longitude zero.
        return np.tile(atlas, (1, 3, 1))

    def _earth_layers(self):
        """Recover a low-frequency underlay so moving clouds leave no dark holes."""
        source = cv2.resize(self.original, (WIDTH * 2, HEIGHT * 2), interpolation=cv2.INTER_AREA)
        thumbnail = source.astype(np.float32) / 255
        white = np.min(thumbnail, axis=2)
        saturation = np.max(thumbnail, axis=2) - white
        yy, xx = np.mgrid[:HEIGHT * 2, :WIDTH * 2].astype(np.float32) / 2
        radius = np.hypot((xx - 241) / 167, (yy - 207) / 167)
        mask = ((white > 0.44) & (saturation < 0.24) & (radius < 0.94)).astype(np.uint8)
        mask = cv2.dilate(mask, np.ones((5, 5), np.uint8))
        underlay = cv2.inpaint(source, mask * 255, 8, cv2.INPAINT_TELEA)
        opacity = cv2.GaussianBlur(mask.astype(np.float32), (0, 0), 2.4)[..., None]
        # Leave a little of the original cloud body to preserve fine surface detail.
        base = np.clip(source * (1 - opacity * 0.8) + underlay * opacity * 0.8, 0, 255).astype(np.uint8)
        return base, cv2.subtract(source, base)

    def _city_atlas(self):
        """Register city clusters to land in this artwork, not to the screen."""
        scale = 2
        source = cv2.resize(self.original, (WIDTH * scale, HEIGHT * scale))
        rgb = source.astype(np.float32)
        yy, xx = np.mgrid[:HEIGHT * scale, :WIDTH * scale].astype(np.float32) / scale
        radius = np.hypot((xx - 241) / 167, (yy - 207) / 167)
        land = ((rgb[..., 0] > rgb[..., 2] * 0.78)
                & (rgb[..., 1] > rgb[..., 2] * 0.71) & (radius < 0.965))
        # Elliptical population patches, following the photographed coastlines.
        clusters = (
            (168, 107, 3, 8, 90), (175, 124, 7, 6, 210),
            (195, 116, 9, 6, 240), (211, 119, 10, 6, 200),
            (205, 94, 7, 4, 105), (160, 135, 6, 4, 120),
            (190, 136, 4, 7, 100), (225, 127, 10, 6, 160),
            (243, 141, 11, 3, 190), (218, 165, 3, 9, 150),
            (219, 187, 2, 12, 130), (174, 159, 15, 2, 125),
            (251, 166, 8, 6, 115), (269, 158, 10, 5, 140),
            (270, 190, 9, 5, 150), (308, 180, 8, 5, 200),
            (326, 182, 10, 6, 320), (320, 199, 5, 7, 210),
            (347, 171, 7, 5, 180), (359, 153, 8, 6, 240),
            (375, 164, 4, 8, 180), (367, 196, 5, 10, 180),
            (381, 221, 4, 8, 140), (359, 239, 9, 3, 130),
            (154, 223, 8, 5, 190), (177, 228, 8, 5, 140),
            (237, 244, 4, 7, 100), (221, 278, 4, 9, 80),
            (214, 304, 7, 5, 170),
        )
        lights = np.zeros(source.shape[:2], np.float32)
        rng = np.random.default_rng(4817)
        for cx, cy, sx, sy, count in clusters:
            points = rng.normal(size=(count, 2))
            points *= (sx, sy)
            points += (cx, cy)
            for x, y in points:
                ix, iy = round(x * scale), round(y * scale)
                if 0 <= ix < source.shape[1] and 0 <= iy < source.shape[0] and land[iy, ix]:
                    lights[iy, ix] += rng.uniform(0.32, 1.0)
        core = cv2.GaussianBlur(lights, (0, 0), 0.46)
        bloom = cv2.GaussianBlur(lights, (0, 0), 2.1)
        intensity = np.clip(core * 3.7 + bloom * 2.0, 0, 1)
        # Retain the original dense coastal lights on the already-dark east limb.
        existing = smooth(12, 65, rgb[..., 0] - rgb[..., 2])
        existing *= smooth(319, 365, xx) * (1 - smooth(0.95, 0.985, radius))
        intensity = np.maximum(intensity, existing * 0.85)
        return np.clip(intensity[..., None] * (255, 201, 116), 0, 255).astype(np.uint8)

    def _star_network(self):
        # Local angular neighbors, not a ring: simultaneous wavefronts can cross.
        count = len(self.stars)
        positions = np.array([star[:2] for star in self.stars], np.float64)
        vectors = positions[None, :, :] - positions[:, None, :]
        distances = np.linalg.norm(vectors, axis=2)
        np.fill_diagonal(distances, np.inf)
        edges = set()
        for first in range(count):
            nearby = np.argsort(distances[first])[:8]
            limit = max(130, distances[first, nearby[3]] * 1.05)
            nearby = [int(i) for i in nearby if distances[first, i] <= limit]
            quadrants = {}
            for second in nearby:
                dx, dy = vectors[first, second]
                quadrant = int((math.atan2(dy, dx) + math.pi) / (math.pi / 2)) % 4
                quadrants.setdefault(quadrant, second)
            neighbors = list(quadrants.values())
            for second in nearby:
                if len(neighbors) >= 4:
                    break
                if second not in neighbors:
                    neighbors.append(second)
            for second in neighbors:
                edges.add(tuple(sorted((first, second))))
        self.star_neighbors = [set() for _ in range(count)]
        for first, second in sorted(edges):
            self.star_neighbors[first].add(second)
            self.star_neighbors[second].add(first)

        seeds = [0]
        while len(seeds) < 4:
            candidates = [i for i in range(count) if i not in seeds]
            seeds.append(max(candidates, key=lambda i: min(distances[i, j] for j in seeds)))
        self.star_origins = [tuple(seeds)]
        self._star_schedule(self.astra_options)
        return [self._star_link(first, second) for first, second in sorted(edges)]

    def _star_schedule(self, options):
        count = len(self.stars)
        seeds = list(self.star_origins[0])
        size_factor = (
            (self.star_sizes.astype(np.float64) - self.star_sizes.min())
            / max(1e-6, float(self.star_sizes.max() - self.star_sizes.min())))
        linked_periods = self.STAR_PERIOD_MIN + size_factor * (
            self.STAR_PERIOD_MAX - self.STAR_PERIOD_MIN)
        classic = options.style == "classic"
        arrivals = np.full(count, np.inf)
        arrivals[seeds] = np.array(
            self.CLASSIC_ORIGIN_DELAYS if classic else self.RELAY_ORIGIN_DELAYS)
        self.star_parents = np.full(count, -1, np.int32)
        groups = np.full(count, -1, np.int32)
        groups[seeds] = np.arange(len(seeds))
        roots = set(seeds)
        queue = [(float(arrivals[i]), i) for i in seeds]
        heapq.heapify(queue)
        while queue:
            arrival, source = heapq.heappop(queue)
            if arrival > arrivals[source]:
                continue
            for target in sorted(self.star_neighbors[source]):
                if target in roots:
                    continue
                # A parent's flash starts its child's charge, not its flash.
                charge = (self.CLASSIC_RELAY_CHARGE + self.CLASSIC_RELAY_RISE
                          if classic else (self.RELAY_CHARGE + self.RELAY_RISE)
                          * linked_periods[source] / self.STAR_PERIOD_MIN)
                following = arrival + charge + self._relay_delay(source, target)
                if following < arrivals[target]:
                    arrivals[target] = following
                    self.star_parents[target] = source
                    groups[target] = groups[source]
                    heapq.heappush(queue, (following, target))
        if classic:
            # Preserve the old four-family timing, including its shorter flashes.
            family = np.asarray(self.CLASSIC_PERIODS)[groups]
            family_factor = (family - family.min()) / (family.max() - family.min())
            rank = 0.42 * family_factor + 0.58 * np.maximum(size_factor, family_factor)
            baseline = options.minimum + rank * (options.maximum - options.minimum)
            periods = baseline
            self.star_time_scale = np.ones((1, count))
            self.star_periods = periods[None, :]
            self.star_times = arrivals[None, :]
        else:
            rank = size_factor
            periods = options.minimum + rank * (options.maximum - options.minimum)
            self.star_periods = periods[None, :]
            self.star_time_scale = (periods / self.STAR_PERIOD_MIN)[None, :]
            self.star_times = arrivals[None, :]

    def configure_astra(self, options, phase=None):
        options = AstraOptions.validated(options.style, options.minimum, options.maximum)
        if self.key != "astra" or options == self.astra_options:
            return
        if phase is not None:
            pulses, charge = self._star_envelopes(phase)
            mix = self._star_link_mix(phase)
            progress = ((phase - self.star_times) % self.star_periods) / self.star_periods
        self.astra_options = options
        self._star_schedule(options)
        if phase is not None:
            # Keep every star's position in its cycle; dissolve any change of
            # envelope shape without restarting the sky or the global clock.
            self.star_times = phase - progress * self.star_periods
            self.star_transition = (phase, pulses, charge, mix)
        else:
            self.star_transition = None

    def _star_transition_progress(self, phase):
        if self.star_transition is None:
            return 1.0
        return float(smooth(0, 0.65, phase - self.star_transition[0]))

    def _star_link_mix(self, phase):
        target = float(self.astra_options.style == "linked")
        blend = self._star_transition_progress(phase)
        return target if self.star_transition is None else (
            self.star_transition[3] * (1 - blend) + target * blend)

    def _relay_delay(self, source, target):
        x1, y1, *_ = self.stars[source]
        x2, y2, *_ = self.stars[target]
        # Slightly different response delays avoid lockstep generations.
        return (0.16 + math.hypot(x2 - x1, y2 - y1) / 650
                + 0.34 * float((self.star_sizes[target] - 0.75) / 1.25))

    def _star_link(self, first, second):
        x1, y1, *_ = self.stars[first]
        x2, y2, *_ = self.stars[second]
        path = QPainterPath(QPointF(x1, y1))
        path.lineTo(QPointF(x2, y2))
        gradient = QLinearGradient(QPointF(x1, y1), QPointF(x2, y2))
        colors = ((112, 207, 255), (190, 157, 255), (255, 190, 112),
                  (123, 255, 205), (255, 133, 190))
        rgb = colors[(first * 3 + second * 5) % len(colors)]
        for position, alpha in ((0, 200), (0.10, 180), (0.23, 115), (0.40, 0),
                                (0.60, 0), (0.77, 115), (0.90, 180), (1, 200)):
            gradient.setColorAt(position, QColor(*rgb, alpha))
        brush = QBrush(gradient)
        pen = QPen(brush, 1.25)
        # The geometry never changes. Cache at 2x for crisp HiDPI lines without
        # hundreds of gradient strokes on every frame.
        bounds = path.boundingRect().adjusted(-3, -3, 3, 3)
        image = QImage(round(bounds.width() * 2), round(bounds.height() * 2),
                       QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        painter.scale(2, 2)
        painter.translate(-bounds.x(), -bounds.y())
        painter.setOpacity(0.22)
        painter.setPen(QPen(brush, 3.5))
        painter.drawPath(path)
        painter.setOpacity(1)
        painter.setPen(pen)
        painter.drawPath(path)
        painter.end()
        classic_gradient = QLinearGradient(QPointF(x1, y1), QPointF(x2, y2))
        for position, alpha in ((0, 178), (0.08, 52), (0.18, 10), (0.40, 1),
                                (0.60, 1), (0.82, 10), (0.92, 52), (1, 178)):
            classic_gradient.setColorAt(position, QColor(*rgb, alpha))
        classic_image = QImage(image.size(), image.format())
        classic_image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(classic_image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(2, 2)
        painter.translate(-bounds.x(), -bounds.y())
        painter.setPen(QPen(QBrush(classic_gradient), 1.05))
        painter.drawPath(path)
        painter.end()
        return StarLink(first, second, path, pen, rgb, bounds, image, classic_image)

    def _star_traits(self):
        source = cv2.resize(self.original, (WIDTH, HEIGHT), interpolation=cv2.INTER_AREA)
        light = np.max(source, axis=2).astype(np.float32)
        emission = np.maximum(0, light - cv2.GaussianBlur(light, (0, 0), 5))
        yy, xx = np.mgrid[-5:6, -5:6]
        sizes = []
        for x, y, *_ in self.stars:
            core = emission[y - 5:y + 6, x - 5:x + 6]
            weight = core * core
            sizes.append(math.sqrt(float((weight * (xx * xx + yy * yy)).sum())
                                   / max(1, float(weight.sum())) / 2))
        sizes = np.asarray(sizes, np.float32)
        low, high = np.percentile(sizes, (10, 90))
        rank = smooth(low, high, sizes)
        self.star_sizes = 0.75 + 1.25 * rank
        self.star_power = 0.40 + 0.85 * rank

    def _prepare(self, quality):
        if quality in self._buffers:
            self._buffers.move_to_end(quality)
            self.__dict__.update(self._buffers[quality])
            return
        self.quality = quality
        self.size = (round(WIDTH * quality), round(HEIGHT * quality))
        self.source = cv2.resize(self.original, self.size, interpolation=cv2.INTER_AREA)
        yy, xx = np.mgrid[:104, :156].astype(np.float32)
        self.x, self.y = (xx + 0.5) * 4, (yy + 0.5) * 4
        cx, cy, radius = CENTERS[self.key]
        self.dx, self.dy = (self.x - cx) / radius, (self.y - cy) / radius
        self.r = np.hypot(self.dx, self.dy)
        self.angle = np.arctan2(self.dy, self.dx)
        self.disc = 1 - smooth(0.91, 1.025, self.r)
        self.depth = np.sqrt(np.maximum(0, 1 - self.r * self.r))
        self.rim = np.exp(-((self.r - 1.015) / 0.04) ** 2)
        self.edge = (smooth(0, 24, self.x) * (1 - smooth(600, 624, self.x))
                     * smooth(0, 18, self.y) * (1 - smooth(397, 416, self.y)))
        self.grid_y, self.grid_x = np.mgrid[:self.size[1], :self.size[0]].astype(np.float32)
        self.map_x = np.empty(self.size[::-1], np.float32)
        self.map_y = np.empty_like(self.map_x)
        self.pixels = np.empty_like(self.source)
        self.display = np.empty((*self.size[::-1], 4), np.uint8)
        self.gain = np.empty((*self.size[::-1], 3), np.float32)
        self.image = QImage(self.display.data, *self.size, self.display.strides[0],
                            QImage.Format.Format_RGB32)
        self.composite = QImage(*self.size, QImage.Format.Format_RGB32)
        if self.key == "sol":
            self.plasma_pixels = np.empty_like(self.source)
            cx, cy, radius = self.SOL_CENTER
            nx = (self.grid_x / quality - cx) / radius
            ny = (self.grid_y / quality - cy) / radius
            depth = np.sqrt(np.maximum(0, 1 - nx * nx - ny * ny))
            longitude = np.arctan2(nx, depth)
            latitude = np.arcsin(np.clip(ny, -1, 1))
            atlas_width = self.solar_atlas.shape[1] // 3
            self.solar_longitude = (longitude / math.tau + 1.5) * atlas_width
            self.solar_latitude = (latitude / math.pi + 0.5) * (self.solar_atlas.shape[0] - 1)
            self.solar_u = np.empty_like(self.map_x)
            self.solar_pixels = np.empty_like(self.source)
            self.solar_alpha = 1 - smooth(0.955, 0.995, np.hypot(nx, ny))
            self.solar_inverse = 1 - self.solar_alpha
            self.solar_weight = np.empty_like(self.solar_alpha)
            self.solar_weight_inverse = np.empty_like(self.solar_alpha)
            u, v, _light = self._solar_fields(Motion(0, 1, 1))
            cv2.resize(u * quality, self.size, dst=self.map_x, interpolation=cv2.INTER_LINEAR)
            cv2.resize(v * quality, self.size, dst=self.map_y, interpolation=cv2.INTER_LINEAR)
            cv2.add(self.map_x, self.grid_x, dst=self.map_x)
            cv2.add(self.map_y, self.grid_y, dst=self.map_y)
            self.solar_backdrop = cv2.remap(self.source, self.map_x, self.map_y, cv2.INTER_LINEAR,
                                           borderMode=cv2.BORDER_CONSTANT)
        if self.key == "terra":
            self.land, self.clouds = (
                cv2.resize(layer, self.size, interpolation=cv2.INTER_CUBIC)
                for layer in self.earth_layers)
            self.cloud_pixels = np.empty_like(self.source)
            self.cloud_u = np.empty_like(self.map_x)
            self.cloud_v = np.empty_like(self.map_y)
            # Emissive lights need subpixel motion, not a second high-DPI material pass.
            self.cities = cv2.resize(self.city_atlas, (WIDTH, HEIGHT), interpolation=cv2.INTER_AREA)
            self.city_pixels = np.empty_like(self.cities)
            self.city_grid_y, self.city_grid_x = np.mgrid[:HEIGHT, :WIDTH].astype(np.float32)
            self.city_u = np.empty((HEIGHT, WIDTH), np.float32)
            self.city_v = np.empty_like(self.city_u)
            self.city_gain = np.empty((HEIGHT, WIDTH, 3), np.float32)
            self.city_emission = np.empty_like(self.source)
            regions = ((189, 119), (225, 179), (267, 163), (322, 190),
                       (366, 169), (162, 228), (227, 275))
            weights = np.array([np.exp(-((self.x - x) / 43) ** 2 - ((self.y - y) / 44) ** 2)
                                for x, y in regions], np.float32)
            self.city_regions = weights / np.maximum(weights.sum(axis=0), 1e-6)
        if self.key == "luna":
            thumbnail = cv2.resize(self.original, (156, 104), interpolation=cv2.INTER_AREA)
            luminance = cv2.cvtColor(thumbnail, cv2.COLOR_RGB2GRAY).astype(np.float32)
            illumination = cv2.GaussianBlur(luminance, (0, 0), 3.0)
            # Undo only broad baked-in lighting; crater relief stays in the photograph.
            self.lunar_albedo = np.clip(156 / np.maximum(illumination, 12), 0.72, 8)
        if self.key == "astra":
            # Keep point stars stationary while the lower-frequency gas advects.
            median = cv2.medianBlur(self.source, max(3, round(5 * quality) | 1))
            residual = np.max(self.source.astype(np.int16) - median, axis=2)
            mask = smooth(16, 65, residual).astype(np.float32)
            mask = cv2.GaussianBlur(mask, (0, 0), 0.65 * quality)
            self.gas = np.clip(self.source * (1 - mask[..., None])
                               + median * mask[..., None], 0, 255).astype(np.uint8)
            self.starlight = cv2.subtract(self.source, self.gas)
            self.star_pixels = np.empty_like(self.source)
            if not self.stars:
                self.stars = self._stars()
                self._star_traits()
                self.star_links = self._star_network()
            mask = np.zeros(self.size[::-1], np.uint8)
            for x, y, *_ in self.stars:
                cv2.circle(mask, (round(x * quality), round(y * quality)),
                           round(6 * quality), 255, -1)
            # Recover the gas behind bright cores once. Dimming then removes
            # stellar emission, instead of punching dark holes in the nebula.
            self.working_gas = cv2.inpaint(self.gas, mask, 5 * quality, cv2.INPAINT_TELEA)
            self.working_starlight = cv2.subtract(self.source, self.working_gas)
            self.gas_blend = np.empty_like(self.source)
            self.starlight_blend = np.empty_like(self.source)
            self.star_weights = np.array([
                np.exp(-((self.x - x) ** 2 + (self.y - y) ** 2) / (24 + 20 * size))
                for (x, y, *_), size in zip(self.stars, self.star_sizes)], np.float32)
        self._buffers[quality] = {
            name: getattr(self, name) for name in self.BUFFER_NAMES if hasattr(self, name)}
        # Normal and transition quality stay hot; resizing cannot grow this unbounded.
        while len(self._buffers) > 3:
            self._buffers.popitem(last=False)

    def _stars(self):
        small = cv2.resize(self.original, (WIDTH, HEIGHT), interpolation=cv2.INTER_AREA)
        light = np.max(small, axis=2)
        contrast = light.astype(np.float32) - cv2.GaussianBlur(light, (0, 0), 2)
        peaks = ((light == cv2.dilate(light, np.ones((9, 9), np.uint8)))
                 & (contrast > 38) & (light > 180))
        yy, xx = np.where(peaks)
        chosen = []
        for index in np.argsort(contrast[yy, xx])[::-1]:
            x, y = int(xx[index]), int(yy[index])
            if not (14 < x < 535 and 24 < y < 347):
                continue
            if any((x - a) ** 2 + (y - b) ** 2 < 18 ** 2 for a, b, *_ in chosen):
                continue
            sampled = small[y, x].astype(np.float32)
            # Original nebula colors are too clustered for a readable relay layer.
            # Preserve luminance, but assign a stable, saturated celestial hue.
            palettes = np.asarray((
                (102, 212, 255), (181, 147, 255), (255, 188, 104),
                (112, 255, 211), (255, 139, 187), (157, 232, 255),
            ), np.float32)
            palette = palettes[len(chosen) % len(palettes)]
            luminance = float(np.clip((sampled.mean() - 85) / 145, 0.55, 1.18))
            rgb = tuple(int(np.clip(channel * luminance, 0, 255)) for channel in palette)
            chosen.append((x, y, rgb, len(chosen) * 2.39996))
            if len(chosen) == self.STAR_COUNT:
                break
        return chosen

    def _paths(self):
        paths = []
        if self.key == "sol":
            # Follow the large upper-right prominence and smaller limb loops.
            curves = (
                ((290, 72), (291, 10), (358, 19), (334, 105)),
                ((122, 113), (69, 89), (63, 111), (107, 146)),
                ((111, 280), (55, 317), (84, 352), (125, 307)),
            )
        elif self.key == "astra":
            curves = (
                ((24, 58), (124, 66), (147, 156), (302, 154)),
                ((87, 327), (119, 230), (251, 190), (401, 230)),
                ((506, 307), (429, 326), (379, 234), (278, 240)),
            )
        else:
            curves = ()
        for start, c1, c2, end in curves:
            path = QPainterPath(QPointF(*start))
            path.cubicTo(QPointF(*c1), QPointF(*c2), QPointF(*end))
            paths.append(path)
        return paths

    def _fields(self, motion):
        t, energy = motion.phase, motion.activity
        dx, dy, angle = self.dx, self.dy, self.angle
        if self.key == "sol":
            u, v, light = self._solar_fields(motion)
        elif self.key == "terra":
            # Bounded spherical reprojection avoids inventing an unseen hemisphere.
            u = (18 + 7 * energy) * math.sin(t * 0.31) * self.depth
            v = 3.8 * math.sin(t * 0.23 + 0.4) * self.depth
            sunrise = np.exp(-((dx - 0.6 - 0.12 * math.sin(t * 0.7)) / 0.28) ** 2)
            light = (1 + self.disc * sunrise * (0.10 + energy * 0.13) * math.sin(t * 0.8)
                     + self.rim * (0.18 + energy * 0.25) * np.sin(angle * 3 - t * 1.8))
            light *= 1 - energy * 0.966 * self._night_mask(motion)
        elif self.key == "luna":
            # Libration moves crater texture coherently; no liquid deformation.
            u = (11 + 6 * energy) * math.sin(t * 0.43) * self.depth
            v = 4.5 * math.sin(t * 0.31 + 0.4) * self.depth
            sun = -0.65 + (0.38 + 0.24 * energy) * math.sin(t * 0.54)
            incidence = self.depth * math.cos(sun) + dx * math.sin(sun)
            shade = 0.42 + 0.60 * smooth(-0.06, 0.45, incidence)
            grazing = np.exp(-((incidence - 0.14) / 0.12) ** 2)
            light = 1 + (shade - 1 + grazing * 0.13) * self.disc
            lunar_mask = 1 - smooth(1.03, 1.09, self.r)
            working = 1 + (self._lunar_light(t) * self.lunar_albedo - 1) * lunar_mask
            light += (working - light) * energy
        else:
            # Counter-flowing layers follow the diagonal dust ridges in the art.
            wave = np.sin(self.x * 0.012 + self.y * 0.020 - t * 0.88)
            curl = np.cos(self.x * 0.018 - self.y * 0.015 + t * 0.67)
            eddy = np.sin(self.x * 0.026 + self.y * 0.008 - t * 0.37)
            amplitude = 12 - 8.4 * energy
            u = amplitude * (0.72 * wave + 0.4 * curl) * self.edge
            v = amplitude * (-0.48 * wave + 0.62 * curl + 0.15 * eddy) * self.edge
            light = 1 + ((0.09 - 0.078 * energy) * np.sin(
                self.x * 0.017 - self.y * 0.024 + t * 1.2)
                + (0.06 - 0.052 * energy) * eddy) * self.edge
        # OpenCV ignores a dst with the wrong dtype instead of converting into it.
        return np.asarray(u, np.float32), np.asarray(v, np.float32), np.asarray(light, np.float32)

    def solar_pose(self):
        cx, cy, radius = self.SOL_CENTER
        return cx, cy, radius / CENTERS["sol"][2]

    def _solar_fields(self, motion):
        t, energy = motion.phase, motion.activity
        cx, cy, scale = self.solar_pose()
        sx, sy, radius = CENTERS["sol"]
        x, y = sx + (self.x - cx) / scale, sy + (self.y - cy) / scale
        dx, dy = (x - sx) / radius, (y - sy) / radius
        r, angle = np.hypot(dx, dy), np.arctan2(dy, dx)
        depth = np.sqrt(np.maximum(0, 1 - r * r))
        disc = 1 - smooth(0.91, 1.025, r)
        amplitude = 3.8 + 2.2 * energy
        vortex = np.sin(angle * 3 - t * 1.2 + r * 7)
        convection = np.sin(dx * 9 + t * 1.65) * np.cos(dy * 8 - t * 1.15)
        corona = np.exp(-((r - 1.09) / 0.15) ** 2)
        radial = (corona * smooth(1.025, 1.16, r)
                  * (3.8 + energy * 5.2) * np.sin(angle * 9 - t * 2.0))
        interior = 1 - smooth(0.74, 0.95, r)
        rotation = 9 * math.sin(t * 0.28) * depth * (1 - energy)
        u = amplitude * (-dy * vortex + 0.5 * convection) * interior + dx * radial + rotation
        v = amplitude * (dx * vortex + 0.5 * np.sin(dx * 8 - dy * 9 + t * 1.5)) * interior + dy * radial
        light = (1 + (0.10 + 0.08 * energy) * convection * disc
                 + corona * (0.18 + 0.22 * energy) * np.sin(angle * 7 - t * 2.4))
        # In working mode the sphere supplies the moving material; the corona
        # keeps its light/eruption passes without a redundant background remap.
        u, v = u * (1 - energy) + x - self.x, v * (1 - energy) + y - self.y
        return u.astype(np.float32), v.astype(np.float32), light.astype(np.float32)

    def _solar_surface(self, motion):
        width = self.solar_atlas.shape[1] // 3
        offset = (motion.phase * self.SOL_SPIN % math.tau) * width / math.tau
        cv2.subtract(self.solar_longitude, offset, dst=self.solar_u)
        cv2.remap(self.solar_atlas, self.solar_u, self.solar_latitude, cv2.INTER_LINEAR,
                  dst=self.solar_pixels, borderMode=cv2.BORDER_REPLICATE)
        alpha, inverse = self.solar_alpha, self.solar_inverse
        if motion.activity < 1 - 1e-5:
            np.multiply(alpha, motion.activity, out=self.solar_weight)
            np.subtract(1, self.solar_weight, out=self.solar_weight_inverse)
            alpha, inverse = self.solar_weight, self.solar_weight_inverse
        cv2.blendLinear(self.pixels, self.solar_pixels, inverse, alpha, dst=self.pixels)

    @classmethod
    def _solar_feature(cls, x, y, phase):
        sx, sy, radius = CENTERS["sol"]
        dx, dy = (x - sx) / radius, (y - sy) / radius
        depth = math.sqrt(max(0, 1 - dx * dx - dy * dy))
        angle = phase * cls.SOL_SPIN
        horizontal = dx * math.cos(angle) + depth * math.sin(angle)
        forward = depth * math.cos(angle) - dx * math.sin(angle)
        return sx + radius * horizontal, y, float(smooth(0, 0.20, forward))

    def _lunar_light(self, phase):
        angle = math.tau * phase / 5.6
        incidence = self.dx * math.sin(angle) + self.depth * math.cos(angle)
        return 0.026 + 0.98 * smooth(0.008, 0.06, incidence) * (0.84 + 0.16 * self.depth)

    def _night_mask(self, motion):
        angle = -0.42 + motion.activity * 3.28
        incidence = self.dx * math.sin(angle) + self.depth * math.cos(angle)
        return (1 - smooth(-0.08, 0.18, incidence)) * self.disc

    def _city_light(self, motion):
        phases = motion.phase * 2.1 - np.arange(len(self.city_regions)) * 1.14
        breaths = 0.16 + 1.08 * (0.5 + 0.5 * np.sin(phases)) ** 2
        groups = np.einsum("i,ijk->jk", breaths.astype(np.float32), self.city_regions)
        return np.asarray(groups * self._night_mask(motion) * motion.activity, np.float32)

    def _plasma_field(self, motion):
        light = np.zeros_like(self.x)
        cx, cy, scale = self.solar_pose()
        body_radius = CENTERS["sol"][2]
        dx, dy = ((self.x - cx) / (body_radius * scale),
                  (self.y - cy) / (body_radius * scale))
        radius, theta_field = np.hypot(dx, dy), np.arctan2(dy, dx)
        for index, angle in enumerate(self.EJECTION_ANGLES):
            release, envelope = self._ejection(motion.phase, index)
            reach = 12 + (133 - index * 11) * release
            u = (radius - 0.985) * body_radius / reach
            along = np.clip(u, 0, 1.1)
            width = (0.055 * (1 - along)
                     + (0.14 + 0.27 * release) * np.sin(math.pi * along / 2))
            bend = (-0.10 if index == 0 else 0.09) * release * along
            bend += 0.065 * np.sin(along * 4.4) * along
            theta = (theta_field - angle - bend + math.pi) % math.tau - math.pi
            lane = theta / np.maximum(width, 0.03)
            squared = (lane * 1.1) ** 2
            plume = np.exp(-np.minimum(squared * squared, 80))
            plume *= smooth(0, 0.12, u) * (1 - smooth(0.78, 1.08, u))
            turbulence = (0.65 + 0.22 * np.sin(lane * 9 + along * 17 - motion.phase * 0.78)
                          + 0.17 * np.sin(lane * 21 - along * 13 + motion.phase * 0.53))
            front = np.exp(-((u - 0.83 + 0.07 * lane * lane) / 0.11) ** 2)
            ribs = (0.5 + 0.5 * np.cos(lane * 17 + along * 8 - motion.phase * 0.45)) ** 4
            light += plume * (turbulence + front * 0.8 + ribs * 0.25) * envelope
        return light * motion.activity * motion.visibility

    def frame(self, motion, quality=1.0):
        quality = self._quality(quality)
        if self.quality != quality:
            self._prepare(quality)
        u, v, light = self._fields(motion)
        cv2.resize(u * quality, self.size, dst=self.map_x, interpolation=cv2.INTER_LINEAR)
        cv2.resize(v * quality, self.size, dst=self.map_y, interpolation=cv2.INTER_LINEAR)
        cv2.add(self.map_x, self.grid_x, dst=self.map_x)
        cv2.add(self.map_y, self.grid_y, dst=self.map_y)
        source = self.gas if self.key == "astra" else self.land if self.key == "terra" else self.source
        if self.key == "astra":
            star_source = self.starlight
            if motion.activity >= 1 - 1e-5:
                source, star_source = self.working_gas, self.working_starlight
            elif motion.activity > 1e-5:
                cv2.addWeighted(self.gas, 1 - motion.activity, self.working_gas,
                                motion.activity, 0, dst=self.gas_blend)
                cv2.addWeighted(self.starlight, 1 - motion.activity, self.working_starlight,
                                motion.activity, 0, dst=self.starlight_blend)
                source, star_source = self.gas_blend, self.starlight_blend
        if self.key == "sol" and motion.activity >= 1 - 1e-5:
            np.copyto(self.pixels, self.solar_backdrop)
        else:
            cv2.remap(source, self.map_x, self.map_y, cv2.INTER_LINEAR,
                      dst=self.pixels, borderMode=(
                          cv2.BORDER_CONSTANT if self.key == "sol"
                          else cv2.BORDER_REFLECT_101))
        if self.key == "terra":
            t, energy = motion.phase, motion.activity
            jet = (9 + 7 * energy) * np.sin(self.dy * 3.6 - t * 0.65) * self.depth
            lift = 3.5 * np.cos(self.dx * 4 + t * 0.52) * self.depth
            cv2.resize(jet * quality, self.size, dst=self.cloud_u, interpolation=cv2.INTER_LINEAR)
            cv2.resize(lift * quality, self.size, dst=self.cloud_v, interpolation=cv2.INTER_LINEAR)
            cv2.add(self.cloud_u, self.map_x, dst=self.cloud_u)
            cv2.add(self.cloud_v, self.map_y, dst=self.cloud_v)
            cv2.remap(self.clouds, self.cloud_u, self.cloud_v, cv2.INTER_LINEAR,
                      dst=self.cloud_pixels, borderMode=cv2.BORDER_CONSTANT)
            cv2.add(self.pixels, self.cloud_pixels, dst=self.pixels)
        if self.key == "sol" and motion.activity > 1e-5:
            self._solar_surface(motion)
        gain = cv2.resize(light, self.size, interpolation=cv2.INTER_LINEAR)
        cv2.merge((gain, gain, gain), dst=self.gain)
        cv2.multiply(self.pixels, self.gain, dst=self.pixels, dtype=cv2.CV_8U)
        if self.key == "sol" and motion.activity > 1e-5:
            emission = self._plasma_field(motion)
            plasma = np.clip(emission[..., None] * (236, 114, 35), 0, 255).astype(np.uint8)
            cv2.resize(plasma, self.size, dst=self.plasma_pixels, interpolation=cv2.INTER_LINEAR)
            cv2.add(self.pixels, self.plasma_pixels, dst=self.pixels)
        if self.key == "terra" and motion.activity > 1e-5:
            cv2.resize(u, (WIDTH, HEIGHT), dst=self.city_u, interpolation=cv2.INTER_LINEAR)
            cv2.resize(v, (WIDTH, HEIGHT), dst=self.city_v, interpolation=cv2.INTER_LINEAR)
            cv2.add(self.city_u, self.city_grid_x, dst=self.city_u)
            cv2.add(self.city_v, self.city_grid_y, dst=self.city_v)
            cv2.remap(self.cities, self.city_u, self.city_v, cv2.INTER_LINEAR,
                      dst=self.city_pixels, borderMode=cv2.BORDER_CONSTANT)
            gain = cv2.resize(self._city_light(motion), (WIDTH, HEIGHT), interpolation=cv2.INTER_LINEAR)
            cv2.merge((gain, gain, gain), dst=self.city_gain)
            cv2.multiply(self.city_pixels, self.city_gain, dst=self.city_pixels, dtype=cv2.CV_8U)
            cv2.resize(self.city_pixels, self.size, dst=self.city_emission, interpolation=cv2.INTER_LINEAR)
            cv2.add(self.pixels, self.city_emission, dst=self.pixels)
        if self.key == "astra" and not self.astra_background_only:
            twinkle = 0.9 + 0.66 * np.sin(
                self.x * 0.47 + self.y * 0.39 + motion.phase * 2.2)
            if motion.activity > 0:
                pulses, charge = self._star_envelopes(motion.phase)
                signal = np.einsum("i,ijk->jk",
                                   5.8 * pulses * self.star_power - 0.70 * charge,
                                   self.star_weights)
                twinkle = twinkle * (1 - motion.activity) + (0.77 + signal) * motion.activity
            gain = cv2.resize(twinkle, self.size, interpolation=cv2.INTER_LINEAR)
            cv2.merge((gain, gain, gain), dst=self.gain)
            cv2.multiply(star_source, self.gain, dst=self.star_pixels, dtype=cv2.CV_8U)
            cv2.add(self.pixels, self.star_pixels, dst=self.pixels)
        # RGB32 is Qt's native raster format; convert once instead of per blend.
        cv2.cvtColor(self.pixels, cv2.COLOR_RGB2BGRA, dst=self.display)
        return self.image

    def composited_frame(self, motion, quality):
        """Budget raster and light together while two models share a transition."""
        image = self.frame(motion, quality)
        painter = QPainter(self.composite)
        painter.drawImage(0, 0, image)
        painter.scale(self.quality, self.quality)
        self.paint_light(painter, motion)
        painter.end()
        return self.composite

    @staticmethod
    def _glow(painter, x, y, radius, rgb, opacity):
        if opacity < 0.005:
            return
        gradient = QRadialGradient(QPointF(x, y), radius)
        gradient.setColorAt(0, QColor(*rgb, round(180 * min(1, opacity))))
        gradient.setColorAt(0.16, QColor(*rgb, round(95 * min(1, opacity))))
        gradient.setColorAt(0.48, QColor(*rgb, round(25 * min(1, opacity))))
        gradient.setColorAt(1, QColor(*rgb, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawEllipse(QPointF(x, y), radius, radius)

    @staticmethod
    def _stroke(painter, path, rgb, opacity, width):
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(*rgb, round(255 * max(0, min(1, opacity)))),
                            width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                            Qt.PenJoinStyle.RoundJoin))
        painter.drawPath(path)

    def _streams(self, painter, motion, rgb, strength, period, count=3, paths=None):
        if strength < 0.001:
            return
        for index, path in enumerate(self.paths if paths is None else paths):
            for number in range(count):
                progress = (motion.phase / period + number / count + index * 0.23) % 1
                alpha = math.sin(math.pi * progress) ** 2 * strength
                for tail in range(12):
                    position = max(0, progress - tail * 0.009)
                    point = path.pointAtPercent(position)
                    painter.setPen(QPen(QColor(*rgb, round(225 * alpha * (1 - tail / 12))),
                                        2.2 - tail * 0.12, Qt.PenStyle.SolidLine,
                                        Qt.PenCapStyle.RoundCap))
                    painter.drawPoint(point)
                point = path.pointAtPercent(progress)
                self._glow(painter, point.x(), point.y(), 9, rgb, alpha)

    def _prominences(self, painter, motion):
        t, energy, visible = motion.phase, motion.activity, motion.visibility
        for index, base in enumerate(self.paths):
            cycle = t * (0.62 - index * 0.08) + index * 2.3
            eruption = (0.5 + 0.5 * math.sin(cycle)) ** 3
            displacement = 3 + (10 + 15 * energy) * eruption
            path = QPainterPath()
            for step in range(41):
                u = step / 40
                point = base.pointAtPercent(u)
                dx, dy = point.x() - CENTERS["sol"][0], point.y() - CENTERS["sol"][1]
                normal = max(1, math.hypot(dx, dy))
                swell = math.sin(math.pi * u) * displacement
                ripple = math.sin(u * 15 - t * 2.8 + index) * 1.7 * math.sin(math.pi * u)
                point += QPointF(dx / normal * swell - dy / normal * ripple,
                                 dy / normal * swell + dx / normal * ripple)
                path.moveTo(point) if step == 0 else path.lineTo(point)
            strength = (0.20 + 0.22 * energy + eruption * 0.38) * visible
            for width, alpha in ((9, 0.06), (4, 0.16), (1.4, 0.45)):
                self._stroke(painter, path, (255, 128, 34), strength * alpha, width)
            self._streams(painter, motion, (255, 217, 117), strength,
                          3.3 + index * 0.7, 3, [path])
            foot = path.pointAtPercent(0.035)
            self._glow(painter, foot.x(), foot.y(), 14 + 10 * eruption,
                       (255, 165, 63), strength * eruption)

    @classmethod
    def _ejection(cls, phase, index):
        cycle = (phase / cls.EJECTION_PERIODS[index] + index * 0.31) % 1
        release = float(smooth(0.09, 0.91, cycle))
        envelope = float(smooth(0.005, 0.20, cycle) * (1 - smooth(0.66, 1, cycle)))
        return release, envelope

    def _coronal_ejections(self, painter, motion):
        strength = motion.activity * motion.visibility
        if strength <= 0:
            return
        painter.save()
        cx, cy, scale = self.solar_pose()
        sx, sy, body_radius = CENTERS["sol"]
        painter.translate(cx, cy)
        painter.scale(scale, scale)
        painter.translate(-sx, -sy)
        for index, angle in enumerate(self.EJECTION_ANGLES):
            release, envelope = self._ejection(motion.phase, index)
            opacity = strength * envelope
            if opacity < 0.002:
                continue
            reach = 12 + (133 - index * 11) * release
            width = 0.14 + 0.27 * release
            drift = (-0.10 if index == 0 else 0.09) * release

            def point(u, lane):
                bend = (drift * u + 0.08 * math.sin(u * 4.4 + lane * 1.4) * u
                        + 0.021 * math.sin(u * 13 - motion.phase * 0.48 + lane * 2) * u)
                spread = 0.055 * (1 - u) + width * math.sin(math.pi * u / 2)
                theta = angle + bend + lane * spread
                radius = body_radius * 0.985 + reach * u
                return QPointF(sx + math.cos(theta) * radius, sy + math.sin(theta) * radius)

            root, front = point(0, 0), point(1, 0)
            # The raster emissivity supplies volume; a few filaments carry fine detail.
            for lane in range(7):
                fraction = (lane - 3) / 3
                filament = QPainterPath(point(0, fraction))
                for step in range(1, 15):
                    u = step / 14
                    filament.lineTo(point(u, fraction + 0.035 * math.sin(u * 11 + lane)))
                self._stroke(painter, filament, (255, 113, 25), opacity * 0.10, 5.5)
                self._stroke(painter, filament, (255, 213, 123),
                             opacity * (0.14 + 0.17 * math.sin(lane * 1.9) ** 2), 1.0)
            for knot in range(8):
                u = (release * 1.6 + knot * 0.618034) % 1
                lane = math.sin(knot * 2.39996) * 0.8
                p = point(u, lane)
                self._glow(painter, p.x(), p.y(), 8 + 16 * u,
                           (255, 144 + knot % 3 * 17, 45),
                           opacity * math.sin(math.pi * u) ** 2 * 0.5)
            # Curved shock fronts stretch and dissolve instead of resetting in place.
            arc = QPainterPath()
            for step in range(25):
                lane = -1 + step / 12
                p = point(1 - 0.09 * lane * lane, lane)
                arc.moveTo(p) if step == 0 else arc.lineTo(p)
            self._stroke(painter, arc, (255, 131, 32), opacity * 0.12, 8)
            self._stroke(painter, arc, (255, 216, 137), opacity * 0.44, 1.5)
            self._glow(painter, root.x(), root.y(), 22 + 17 * (1 - release),
                       (255, 211, 126), opacity * (1 - release * 0.5))
            self._glow(painter, front.x(), front.y(), 20 + 19 * release,
                       (255, 153, 63), opacity * 0.62)
        painter.restore()

    def _orbit(self, painter, motion, center, radii, tilt, speed, offset, rgb, strength):
        """A lit orbital trail with far-side occlusion against the actual limb."""
        cx, cy, body_radius = center
        cosine, sine = math.cos(tilt), math.sin(tilt)

        def point(angle):
            x, y = radii[0] * math.cos(angle), radii[1] * math.sin(angle)
            px, py = cx + x * cosine - y * sine, cy + x * sine + y * cosine
            limb = math.hypot(px - cx, py - cy) / body_radius
            occlusion = 1.0 if math.sin(angle) >= 0 else float(smooth(1.0, 1.055, limb))
            return QPointF(px, py), occlusion

        angle = motion.phase * speed + offset
        strength *= motion.visibility
        for index in range(32):
            segment = angle - index * 0.024
            head, visibility = point(segment)
            end, end_visibility = point(segment - 0.025)
            opacity = strength * visibility * end_visibility * (1 - index / 32) ** 1.7
            painter.setPen(QPen(QColor(*rgb, round(185 * opacity)), 1.25,
                                Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(head, end)
        head, visibility = point(angle)
        self._glow(painter, head.x(), head.y(), 12, rgb, strength * visibility)
        painter.setPen(QPen(QColor(*rgb, round(245 * strength * visibility)), 2.3,
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawPoint(head)

    def _aurora(self, painter, motion):
        t, energy, visible = motion.phase, motion.activity, motion.visibility
        strength = (0.28 + 0.62 * energy) * visible
        for ribbon in range(4):
            path = QPainterPath()
            for step in range(57):
                fraction = step / 56
                angle = -2.9 + fraction * 2.5
                ripple = (math.sin(fraction * 15 - t * 1.35 + ribbon * 0.45)
                          + 0.4 * math.sin(fraction * 29 + t * 0.9))
                radius = 160 - ribbon * 2.3 + ripple * (3 + 2 * energy)
                point = QPointF(241 + math.cos(angle) * radius,
                                207 + math.sin(angle) * radius)
                path.moveTo(point) if step == 0 else path.lineTo(point)
            color = (70 + ribbon * 17, 234 - ribbon * 14, 186 + ribbon * 20)
            self._stroke(painter, path, color, strength * 0.07, 7 - ribbon)
            self._stroke(painter, path, color, strength * 0.14, 1.5)

    def _starbursts(self, painter, motion):
        t, energy, visible = motion.phase, motion.activity, motion.visibility
        if visible < 0.001:
            return
        for x, y, rgb, offset in self.stars[:self.COMPLETED_STAR_COUNT]:
            # Different frequencies and long envelopes prevent synchronized blinking.
            breath = (0.5 + 0.5 * math.sin(t * (1.15 + 0.24 * math.sin(offset)) + offset)) ** 3
            flare = (0.5 + 0.5 * math.sin(t * 0.32 + offset * 1.7)) ** 8
            size = 4 + 6 * breath + (4 + 3 * energy) * flare
            strength = (0.18 + 0.57 * breath + (0.3 + energy * 0.25) * flare) * visible
            self._glow(painter, x, y, size, rgb, strength)
            if breath + flare > 0.4:
                opacity = min(0.65, (breath + flare - 0.4) * 0.5) * visible
                # Taper diffraction spikes instead of drawing flat crosses.
                for step in range(5):
                    length = size * (1.4 - step * 0.23)
                    painter.setPen(QPen(QColor(*rgb, round(100 * opacity * (step + 1) / 5)), 0.65))
                    painter.drawLine(QPointF(x - length, y), QPointF(x + length, y))
                    painter.drawLine(QPointF(x, y - length * 0.7), QPointF(x, y + length * 0.7))

        # Sparse foreground stardust follows curved paths, with a smooth lifecycle.
        for index in range(16):
            progress = (t * (0.065 + 0.012 * math.sin(index * 2.4)) + index * 0.618034) % 1
            envelope = math.sin(math.pi * progress) ** 2
            origin_x = 40 + (index * 113) % 450
            origin_y = 55 + (index * 71) % 250
            x = origin_x + (progress - 0.5) * (32 + energy * 24)
            y = origin_y + math.sin(progress * math.pi * 1.2 + index) * 16
            strength = envelope * (0.3 + 0.4 * energy) * visible
            self._glow(painter, x, y, 3.5, (170, 209, 251), strength)
            painter.setPen(QPen(QColor(218, 236, 255, round(200 * strength)), 1.1))
            painter.drawPoint(QPointF(x, y))

    def _star_envelopes(self, phase):
        classic = self.astra_options.style == "classic"
        age = ((phase - self.star_times) % self.star_periods) / self.star_time_scale
        if classic:
            charge_time = self.CLASSIC_RELAY_CHARGE
            rise_time = self.CLASSIC_RELAY_RISE
            decay_time = self.CLASSIC_RELAY_DECAY
            charge_tail = 0.10
            second_flash = 0.62
            second_width = 0.056
        else:
            charge_time = self.RELAY_CHARGE
            rise_time = self.RELAY_RISE
            decay_time = self.RELAY_DECAY
            charge_tail = 0.20
            second_flash = 1.24
            second_width = 0.112
        charge = smooth(0, charge_time, age) * (
            1 - smooth(charge_time, charge_time + charge_tail, age))
        flash_age = age - charge_time
        pulse = smooth(0, rise_time, flash_age) * np.exp(
            -np.maximum(0, flash_age - rise_time) / decay_time)
        pulse += (0.25 * np.exp(-((flash_age - (0.32 if classic else 0.64))
                                   / (0.044 if classic else 0.088)) ** 2)
                  + 0.10 * np.exp(-((flash_age - second_flash) / second_width) ** 2))
        if not classic:
            pulse *= 1 - smooth(self.STAR_PERIOD_MIN - 0.8, self.STAR_PERIOD_MIN, age)
        pulse = pulse.max(axis=0).astype(np.float32)
        charge = charge.max(axis=0).astype(np.float32)
        if self.star_transition is not None:
            blend = self._star_transition_progress(phase)
            pulse = self.star_transition[1] * (1 - blend) + pulse * blend
            charge = self.star_transition[2] * (1 - blend) + charge * blend
        return pulse, charge

    def _star_connections(self, painter, motion, pulses):
        strength = motion.activity * motion.visibility
        if strength <= 0:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        opacity = painter.opacity()
        mix = self._star_link_mix(motion.phase)
        for link in self.star_links:
            activation = max(float(pulses[link.source]), float(pulses[link.target]))
            # Broad endpoint segments survive rasterization against the nebula.
            # The middle stays dark; no traveling dot or repeated endpoint glows.
            endpoint = strength * min(1.0, 0.16 + activation * 0.84)
            if mix > 0:
                painter.setOpacity(opacity * endpoint * mix)
                painter.drawImage(link.bounds, link.image)
            if mix < 1 and activation > 0.01:
                painter.setOpacity(opacity * strength * (1 - mix) * min(1, activation * 0.8))
                painter.drawImage(link.bounds, link.classic_image)
        painter.restore()

    def _star_relay(self, painter, motion):
        strength = motion.activity * motion.visibility
        if strength <= 0:
            return
        classic = self.astra_options.style == "classic"
        pulses, _charge = self._star_envelopes(motion.phase)
        self._star_connections(painter, motion, pulses)
        for index, (pulse, (x, y, rgb, _offset)) in enumerate(zip(pulses, self.stars)):
            size = float(self.star_sizes[index])
            if classic:
                breath = 0.5 + 0.5 * math.sin(
                    motion.phase * (0.22 + 0.015 * (index % 7)) + index * 2.17)
                alpha = min(1, 0.08 * breath + float(pulse * self.star_power[index]))
            else:
                alpha = min(1, float(pulse * self.star_power[index]))
            alpha *= strength
            if alpha < 0.012:
                continue
            self._glow(painter, x, y, (7 + 19 * float(pulse)) * size, rgb, alpha)
            self._glow(painter, x, y, (3 + 5 * float(pulse)) * size, (224, 242, 255), alpha)
            painter.setPen(QPen(QColor(236, 247, 255, round(255 * min(1, alpha))),
                                0.9 + size * float(pulse) * 1.5, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap))
            painter.drawPoint(QPointF(x, y))
            for band in range(4):
                length = (3 + 10 * float(pulse)) * size * (1 - band * 0.20)
                painter.setPen(QPen(QColor(*rgb, round(72 * alpha)), 0.65))
                painter.drawLine(QPointF(x - length, y), QPointF(x + length, y))
                painter.drawLine(QPointF(x, y - length * 0.66), QPointF(x, y + length * 0.66))

    def paint_light(self, painter, motion):
        # The enlarged soft corona has a bounded raster budget; the photosphere
        # keeps its full-resolution material pass and is never downsampled here.
        if (self.key == "sol"
                and painter.deviceTransform().m11() > self.SOL_LIGHT_QUALITY):
            self.solar_light.fill(Qt.GlobalColor.transparent)
            light = QPainter(self.solar_light)
            light.scale(self.SOL_LIGHT_QUALITY, self.SOL_LIGHT_QUALITY)
            self._paint_light(light, motion)
            light.end()
            painter.save()
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(QRectF(0, 0, WIDTH, HEIGHT), self.solar_light)
            painter.restore()
        else:
            self._paint_light(painter, motion)

    def _paint_light(self, painter, motion):
        """Choreograph light along each body's material and depth, never the UI."""
        if self.key == "astra" and self.astra_background_only:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        t, energy, visible = motion.phase, motion.activity, motion.visibility
        if self.key == "sol":
            cx, cy, scale = self.solar_pose()
            painter.save()
            painter.translate(cx, cy)
            painter.scale(scale, scale)
            painter.translate(-CENTERS["sol"][0], -CENTERS["sol"][1])
            self._prominences(painter, motion)
            for rotating, weight in ((False, 1 - energy), (True, energy)):
                if weight <= 0.00001:
                    continue
                for i, (x, y, size) in enumerate(((304, 179, 31), (170, 265, 27), (233, 114, 21))):
                    breath = (0.5 + 0.5 * math.sin(t * (1.3 + i * 0.17) + i * 2.1)) ** 3
                    facing = 1
                    if rotating:
                        x, y, facing = self._solar_feature(x, y, t)
                    self._glow(painter, x, y, size, (255, 173, 56),
                               (0.20 + energy * 0.38) * breath * visible * weight * facing)
            painter.restore()
            self._coronal_ejections(painter, motion)
        elif self.key == "terra":
            self._aurora(painter, motion)
            self._orbit(painter, motion, CENTERS["terra"], (207, 77), -0.42,
                        0.85, 0.8, (103, 213, 255), 0.48 + energy * 0.42)
        elif self.key == "luna":
            self._orbit(painter, motion, CENTERS["luna"], (205, 66), -0.35,
                        0.95, 0.0, (200, 222, 250), 0.60 + energy * 0.35)
            self._orbit(painter, motion, CENTERS["luna"], (194, 62), 0.65,
                        -0.68, 2.0, (133, 185, 240), energy * 0.6)
        else:
            self._streams(painter, motion, (168, 215, 253),
                          0.16 * (1 - energy) * visible, 5.5, 2)
            self._starbursts(painter, Motion(t, 0, visible * (1 - energy)))
            self._star_relay(painter, motion)
        painter.restore()
