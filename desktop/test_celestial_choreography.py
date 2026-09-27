"""Check the meaning of each working animation, not just pixel movement."""
import os
from pathlib import Path
import sys
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cv2
import numpy as np
import pytest
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from celestial import CelestialLayer
from celestial_motion import AstraOptions, CENTERS, Motion, MotionClock


@pytest.fixture(scope="module")
def surfaces():
    app = QApplication.instance() or QApplication([])
    layer = CelestialLayer()
    layer.preload()
    yield layer.pictures
    assert app is QApplication.instance()


def pixels(image):
    image = image.convertToFormat(QImage.Format.Format_RGB888)
    return np.asarray(image.constBits()).reshape(image.height(), image.bytesPerLine())[
        :, :image.width() * 3].reshape(image.height(), image.width(), 3).copy()


def overlay(surface, motion, method):
    image = QImage(624, 416, QImage.Format.Format_RGB32)
    image.fill(0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
    getattr(surface, method)(painter, motion)
    painter.end()
    return pixels(image).astype(float)


def test_astra_background_track_ignores_flash_periods(surfaces):
    surface = surfaces["astra"]
    old_options = surface.astra_options
    try:
        surface.astra_background_only = True
        before = pixels(surface.composited_frame(Motion(27, 1, 1), 1))
        surface.configure_astra(AstraOptions("linked", 80, 120), 27)
        after = pixels(surface.composited_frame(Motion(27, 1, 1), 1))
        assert np.array_equal(before, after)
        later = pixels(surface.composited_frame(Motion(28, 1, 1), 1))
        assert not np.array_equal(after, later)
    finally:
        surface.astra_background_only = False
        surface.configure_astra(old_options)


def test_lunar_cycle_has_full_quarters_and_new_moon(surfaces):
    moon = surfaces["luna"]
    interior = moon.r < 0.9
    fractions = [(moon._lunar_light(t)[interior] > 0.3).mean()
                 for t in (0, 1.4, 2.8, 4.2, 5.6)]
    assert fractions[0] > 0.98 and fractions[-1] > 0.98
    assert 0.42 < fractions[1] < 0.53
    assert fractions[2] < 0.01
    assert 0.42 < fractions[3] < 0.53
    right = interior & (moon.dx > 0.15)
    assert moon._lunar_light(1.4)[right].mean() > 0.8
    assert moon._lunar_light(4.2)[right].mean() < 0.04


def test_new_moon_does_not_leave_a_bright_outer_ring(surfaces):
    moon = surfaces["luna"]
    full = pixels(moon.frame(Motion(0, 1, 1))).astype(float)
    new = pixels(moon.frame(Motion(2.8, 1, 1))).astype(float)
    yy, xx = np.mgrid[:416, :624]
    radius = np.hypot((xx - 237) / 161, (yy - 203) / 161)
    assert full[radius < 0.9].mean() > new[radius < 0.9].mean() * 10
    assert np.percentile(new[(radius > 0.94) & (radius < 1.04)].max(axis=1), 95) < 28


def test_earth_becomes_night_with_warm_clustered_city_lights(surfaces):
    earth = surfaces["terra"]
    day = pixels(earth.frame(Motion(8, 0, 1))).astype(float)
    night = pixels(earth.frame(Motion(8, 1, 1))).astype(float)
    yy, xx = np.mgrid[:416, :624]
    radius = np.hypot((xx - 241) / 167, (yy - 207) / 167)
    interior = radius < 0.88
    assert night[interior].mean() < day[interior].mean() * 0.3
    lights = cv2.resize(earth.city_atlas, (624, 416))
    assert lights[..., 0].max() > 180
    assert lights[radius > 1.04].max() == 0
    warm = (night[..., 0] > night[..., 2] * 1.5) & (night[..., 0] > 40) & interior
    assert warm.sum() > 500
    assert warm.sum() < interior.sum() * 0.15


def test_city_breaths_have_regional_delays_and_fade_out_at_completion(surfaces):
    earth = surfaces["terra"]
    phases = np.linspace(0, 3, 50)
    groups = np.array([earth._city_light(Motion(float(t), 1, 1)) for t in phases])
    europe = groups[:, 30, 48]
    india = groups[:, 47, 80]
    assert europe.max() > europe.min() * 3
    assert india.max() > india.min() * 3
    assert abs(int(europe.argmax()) - int(india.argmax())) > 5
    assert earth._city_light(Motion(8, 0, 1)).max() == 0


def test_stars_spread_from_multiple_origins_through_local_straight_links(surfaces):
    stars = surfaces["astra"]
    assert len(stars.stars) == 96
    assert np.isfinite(stars.star_times).all()
    assert len(stars.star_origins) == 1
    for wave, seeds in enumerate(stars.star_origins):
        assert len(seeds) == 4
        assert np.allclose(stars.star_times[wave, list(seeds)], stars.RELAY_ORIGIN_DELAYS)
        for target, arrival in enumerate(stars.star_times[wave]):
            if target in seeds:
                continue
            parent_arrivals = [
                stars.star_times[wave, source] + (stars.RELAY_CHARGE + stars.RELAY_RISE)
                * stars.star_time_scale[wave, source]
                + stars._relay_delay(source, target)
                for source in stars.star_neighbors[target]]
            assert arrival == pytest.approx(min(parent_arrivals))
    quadrants = []
    for source, neighbors in enumerate(stars.star_neighbors):
        assert 4 <= len(neighbors) <= 8
        x, y, *_ = stars.stars[source]
        directions = {int((np.arctan2(stars.stars[i][1] - y, stars.stars[i][0] - x)
                           + np.pi) / (np.pi / 2)) % 4 for i in neighbors}
        quadrants.append(len(directions))
    assert (np.array(quadrants) >= 3).mean() > 0.6
    for link in stars.star_links:
        start, end = link.path.pointAtPercent(0), link.path.pointAtPercent(1)
        assert (start.x(), start.y()) == stars.stars[link.source][:2]
        assert (end.x(), end.y()) == stars.stars[link.target][:2]
        assert link.path.elementCount() == 2
        middle = link.path.pointAtPercent(0.5)
        assert middle.x() == pytest.approx((start.x() + end.x()) / 2)
        assert middle.y() == pytest.approx((start.y() + end.y()) / 2)
        stops = link.pen.brush().gradient().stops()
        assert stops[0][1].alpha() == stops[-1][1].alpha() == 200
        assert stops[3][1].alpha() == stops[4][1].alpha() == 0
    a = overlay(stars, Motion(1.70, 1, 1), "_star_relay")
    b = overlay(stars, Motion(7.7, 1, 1), "_star_relay")
    assert np.abs(a - b).mean() > 0.1
    assert overlay(stars, Motion(0.65, 0, 1), "_star_relay").max() == 0


def test_charge_is_slow_flash_is_fast_and_working_flow_is_thirty_percent(surfaces):
    stars = surfaces["astra"]
    assert stars.RELAY_CHARGE == pytest.approx(1.64 * 2)
    assert stars.RELAY_RISE == pytest.approx(0.052 * 2)
    assert stars.RELAY_DECAY == pytest.approx(0.46 * 2)
    assert stars.star_periods.min() == pytest.approx(5 * 2)
    assert stars.star_periods.max() == pytest.approx(7.5 * 2)
    assert np.corrcoef(stars.star_sizes, stars.star_periods[0])[0, 1] > 0.999
    assert len(np.unique(stars.star_periods)) > 20
    for index, arrival in enumerate(stars.star_times[0]):
        times = arrival + stars.star_time_scale[0, index] * np.array([
            0,
            stars.RELAY_CHARGE / 2,
            stars.RELAY_CHARGE - 0.03,
            stars.RELAY_CHARGE,
            stars.RELAY_CHARGE + stars.RELAY_RISE,
            stars.RELAY_CHARGE + 2.30,
        ])
        values = [stars._star_envelopes(float(t)) for t in times]
        charge = [value[1][index] for value in values]
        pulse = [value[0][index] for value in values]
        assert charge[0] < 0.01 < charge[1] < charge[2] <= charge[3]
        assert pulse[2] < 0.001 and pulse[3] < 0.001
        assert pulse[4] > 0.95 and pulse[5] < 0.20
    assert stars.star_periods.min() > stars.RELAY_CHARGE + 1.6
    for phase in (1, 7, 32):
        work = stars._fields(Motion(phase, 1, 1))
        done = stars._fields(Motion(phase, 0, 1))
        assert np.hypot(work[0], work[1]).mean() == pytest.approx(
            np.hypot(done[0], done[1]).mean() * 0.3, rel=1e-5)


def test_classic_astra_period_controls_change_the_actual_schedule(surfaces):
    stars = surfaces["astra"]
    original = stars.astra_options
    try:
        stars.configure_astra(AstraOptions("classic", 3.0, 9.0))
        assert stars.star_periods.min() == pytest.approx(3.0)
        assert stars.star_periods.max() == pytest.approx(9.0)
        assert len(np.unique(stars.star_periods)) > 20
    finally:
        stars.configure_astra(original)


def test_parent_flash_triggers_a_full_neighbor_charge_not_an_instant_flash(surfaces):
    stars = surfaces["astra"]
    seeds = set(stars.star_origins[0])
    for target, start in enumerate(stars.star_times[0]):
        if target in seeds:
            continue
        parent = min(stars.star_neighbors[target], key=lambda source: (
            stars.star_times[0, source] + (stars.RELAY_CHARGE + stars.RELAY_RISE)
            * stars.star_time_scale[0, source]
            + stars._relay_delay(source, target)))
        parent_flash = stars.star_times[0, parent] + (
            stars.RELAY_CHARGE + stars.RELAY_RISE) * stars.star_time_scale[0, parent]
        assert stars.star_parents[target] == parent
        assert stars.star_periods[0, target] >= stars.STAR_PERIOD_MIN
        assert stars._star_envelopes(parent_flash)[0][parent] > 0.98
        assert 0.16 < start - parent_flash < 0.9
        pulses, charge = stars._star_envelopes(start)
        assert pulses[target] < 0.001 and charge[target] == 0
        charge_duration = stars.RELAY_CHARGE * stars.star_time_scale[0, target]
        rise_duration = stars.RELAY_RISE * stars.star_time_scale[0, target]
        pulses, charge = stars._star_envelopes(start + charge_duration / 2)
        assert pulses[target] < 0.001 and charge[target] == pytest.approx(0.5)
        pulses, charge = stars._star_envelopes(start + charge_duration)
        assert pulses[target] < 0.001 and charge[target] > 0.999
        pulses, _charge = stars._star_envelopes(start + charge_duration + rise_duration)
        assert pulses[target] > 0.98


def test_star_size_controls_flash_brightness(surfaces):
    stars = surfaces["astra"]
    assert stars.star_sizes.max() / stars.star_sizes.min() > 2.5
    assert stars.star_power.max() / stars.star_power.min() > 3
    assert np.corrcoef(stars.star_sizes, stars.star_power)[0, 1] > 0.999
    phase = stars.RELAY_CHARGE + stars.RELAY_RISE
    total = overlay(stars, Motion(phase, 1, 1), "_star_relay")
    assert total.max() > 230


@pytest.mark.parametrize("quality", [1, 2])
def test_link_segments_are_visible_on_artwork_but_centers_stay_dark(surfaces, quality):
    stars = surfaces["astra"]
    links = [link for link in stars.star_links if link.path.length() > 45][::12]
    assert len(links) >= 6
    for link in links:
        image = stars.frame(Motion(4, 1, 1), quality).copy()
        before = pixels(image).astype(float)
        painter = QPainter(image)
        painter.scale(quality, quality)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        pulses = np.zeros(len(stars.stars))
        pulses[link.source] = 1
        with patch.object(stars, "star_links", [link]):
            stars._star_connections(painter, Motion(4, 1, 1), pulses)
        painter.end()
        contrast = pixels(image).astype(float) - before

        def segment_light(fraction):
            point = link.path.pointAtPercent(fraction)
            x, y = round(point.x() * quality), round(point.y() * quality)
            return contrast[y - 1:y + 2, x - 1:x + 2].max()

        # Sample actual line segments away from the star, not endpoint glows.
        ends = [segment_light(t) for t in (0.12, 0.22, 0.78, 0.88)]
        assert min(ends) > 35
        assert segment_light(0.5) <= 1


def test_solar_globe_fills_the_view_and_rotates_about_the_vertical_axis(surfaces):
    sun = surfaces["sol"]
    sx, sy, _source_radius = CENTERS["sol"]
    cx, cy, scale = sun.solar_pose()
    radius = CENTERS["sol"][2] * scale
    assert cx == 556 / 2
    assert 0.92 < radius * 2 / 556 < 0.98
    assert 416 + 100 < cy + radius < 416 + 140
    assert cy - radius > 15
    sun.frame(Motion(3, 1, 1), 1)
    first = sun.solar_u.copy()
    latitude = sun.solar_latitude.copy()
    sun.frame(Motion(5, 1, 1), 1)
    atlas_width = sun.solar_atlas.shape[1] // 3
    assert np.allclose(first - sun.solar_u,
                       2 * sun.SOL_SPIN * atlas_width / (2 * np.pi), atol=3e-4)
    assert np.array_equal(latitude, sun.solar_latitude)
    for x, y in ((304, 179), (170, 265), (233, 114)):
        original = sun._solar_feature(x, y, 0)
        assert original == pytest.approx((x, y, 1))
        for angle in np.linspace(0, 2 * np.pi, 30):
            px, py, facing = sun._solar_feature(x, y, angle / sun.SOL_SPIN)
            assert py == y
            assert 0 <= facing <= 1
            assert (px - sx) ** 2 + (py - sy) ** 2 <= _source_radius ** 2 + 1e-6
        assert sun._solar_feature(x, y, np.pi / sun.SOL_SPIN)[2] == 0
    xs = [sun._solar_feature(sx, sy, angle / sun.SOL_SPIN)[0]
          for angle in (0, 0.1, 1.3, 1.4)]
    assert xs[1] - xs[0] > 4 * (xs[3] - xs[2]), "surface motion must foreshorten at the limb"


def test_solar_texture_wrap_is_continuous_and_latitude_never_rolls(surfaces):
    sun = surfaces["sol"]
    sun.frame(Motion(0, 1, 1))
    latitude = sun.solar_latitude.copy()
    mask = sun.solar_alpha.copy()
    period = 2 * np.pi / sun.SOL_SPIN
    samples = []
    for phase in (period - 1e-5, period + 1e-5):
        sun._solar_surface(Motion(phase, 1, 1))
        samples.append(sun.solar_pixels.copy().astype(float))
        assert np.array_equal(sun.solar_latitude, latitude)
        assert np.array_equal(sun.solar_alpha, mask)
        assert sun.solar_u.min() >= 0 and sun.solar_u.max() < sun.solar_atlas.shape[1] - 1
    assert np.abs(samples[1] - samples[0]).mean() < 0.01


def test_photographed_stars_dim_after_trigger_then_flash_after_full_charge(surfaces):
    stars = surfaces["astra"]
    for index in (0, 6, 15, 25):
        x, y, *_ = stars.stars[index]
        arrival = float(stars.star_times[0, index])
        brightness, emission, backgrounds = [], [], []
        scale = stars.star_time_scale[0, index]
        charge = stars.RELAY_CHARGE
        peak = charge + stars.RELAY_RISE
        for offset in np.array((0, charge / 2, charge - 0.06, charge, peak)) * scale:
            motion = Motion(arrival + offset, 1, 1)
            image = pixels(stars.frame(motion, 1))
            brightness.append(image[y - 2:y + 3, x - 2:x + 3].mean())
            emission.append(stars.star_pixels[y - 2:y + 3, x - 2:x + 3].mean())
            # The nebula keeps moving during the longer charge; remove its
            # contribution when measuring the photographed star's dimming.
            background = cv2.remap(stars.working_gas, stars.map_x, stars.map_y,
                                   cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT_101)
            light = cv2.resize(stars._fields(motion)[2], stars.size)
            background = np.clip(np.rint(background * light[..., None]), 0, 255)
            backgrounds.append(background[y - 2:y + 3, x - 2:x + 3].mean())
        assert emission[0] > emission[1] > emission[2]
        assert emission[3] <= emission[2] + 0.1
        assert emission[4] > emission[3] + 30
        assert emission[0] > emission[2] + 3
        contrast = np.array(brightness) - backgrounds
        assert contrast[0] > contrast[1] > contrast[2]
        assert contrast[3] <= contrast[2] + 0.1
        assert contrast[4] > contrast[3] + 30
        charge_motion = Motion(arrival + charge * scale, 1, 1)
        charged = pixels(stars.composited_frame(charge_motion, 1))
        assert contrast[3] >= 0, "charge must not black out the nebula"
        assert charged[y - 2:y + 3, x - 2:x + 3].mean() >= brightness[3]
        flash = pixels(stars.composited_frame(Motion(arrival + peak * scale, 1, 1), 1))
        assert flash[y - 2:y + 3, x - 2:x + 3].mean() > brightness[3] + 100


def test_relay_timing_keeps_subframe_precision_after_many_days(surfaces):
    stars = surfaces["astra"]
    phase = 0.638
    first = np.array(stars._star_envelopes(phase))
    for index, period in enumerate(stars.star_periods[0]):
        much_later = np.array(stars._star_envelopes(phase + period * 1_000_000))
        assert np.allclose(first[:, index], much_later[:, index], atol=1e-6)


def test_star_field_never_has_a_collective_pause(surfaces):
    stars = surfaces["astra"]
    # Visible emissions, not tiny numerical tails, must overlap between cycles.
    for origin in (0, 300, 3600, 86400):
        samples = np.array([
            stars._star_envelopes(origin + frame / 30)[0] * stars.star_power
            for frame in range(30 * 30)])
        assert samples.sum(axis=1).min() > 1.0
        assert samples.max(axis=1).min() > 0.22
        assert (samples > 0.08).sum(axis=1).min() >= 3
    first = stars._star_envelopes(1.7)
    later = stars._star_envelopes(1.7 + 5)
    assert not np.allclose(first, later), "the whole sky must not restart on a five-second beat"


def test_ejections_travel_outward_then_dissipate(surfaces):
    sun = surfaces["sol"]
    assert min(sun.EJECTION_PERIODS) > 14
    period = sun.EJECTION_PERIODS[0]
    early, _ = sun._ejection(0.18 * period, 0)
    expanded, _ = sun._ejection(0.75 * period, 0)
    _, faded = sun._ejection(0.99 * period, 0)
    assert expanded > early + 0.65
    assert faded < 0.01
    image = overlay(sun, Motion(7, 1, 1), "_coronal_ejections")
    yy, xx = np.mgrid[:416, :624]
    cx, cy, scale = sun.solar_pose()
    radius = np.hypot(xx - cx, yy - cy)
    outside = radius > CENTERS["sol"][2] * scale + 35
    assert image[outside].max() > 50
    assert (image[outside].max(axis=1) > 10).sum() > 500
    assert overlay(sun, Motion(7, 0, 1), "_coronal_ejections").max() == 0
    plasma = sun._plasma_field(Motion(7, 1, 1))
    radius = np.hypot(sun.x - cx, sun.y - cy) / (CENTERS["sol"][2] * scale)
    assert plasma[radius > 1.15].max() > 0.4
    assert plasma[radius < 0.95].max() == 0
    assert sun._plasma_field(Motion(2.8, 0, 1)).max() == 0


@pytest.mark.parametrize("key,method,period", [
    ("sol", "_coronal_ejections", 14.8),
    ("astra", "_star_relay", 10.0),
])
def test_effect_cycle_wraps_do_not_pop(surfaces, key, method, period):
    before = overlay(surfaces[key], Motion(period - 1e-5, 1, 1), method)
    after = overlay(surfaces[key], Motion(period + 1e-5, 1, 1), method)
    assert np.abs(before - after).mean() < 0.01


@pytest.mark.parametrize("key", CENTERS)
def test_quality_changes_restore_all_material_buffers(surfaces, key):
    surface = surfaces[key]
    for requested in (1, 1.25, 1.5, 1.75, 2, 1, 2, 1):
        image = surface.frame(Motion(5, 1, 1), requested)
        assert image.width() == round(624 * surface._quality(requested))
        assert image.height() == round(416 * surface._quality(requested))
        assert np.isfinite(surface.pixels).all()
        assert len(surface._buffers) <= 3
        assert pixels(image).max() > 100


@pytest.mark.parametrize("key", CENTERS)
@pytest.mark.parametrize("quality", [1, 2])
def test_transition_composite_keeps_the_material_buffer_unmodified(surfaces, key, quality):
    surface = surfaces[key]
    motion = Motion(5, 1, 1)
    material = pixels(surface.frame(motion, quality))
    combined = pixels(surface.composited_frame(motion, quality))
    assert np.array_equal(pixels(surface.image), material)
    expected = surface.frame(motion, quality).copy()
    painter = QPainter(expected)
    painter.scale(surface.quality, surface.quality)
    surface.paint_light(painter, motion)
    painter.end()
    assert np.array_equal(combined, pixels(expected))
    assert np.array_equal(pixels(surface.composited_frame(motion, quality)), combined)


@pytest.mark.parametrize("key", CENTERS)
@pytest.mark.parametrize("states", [(1, 4), (4, 1)])
@pytest.mark.parametrize("at", [5, 8.2, 10.4])
def test_state_transition_is_continuous_at_different_cycle_positions(surfaces, key, states, at):
    surface = surfaces[key]
    clock = MotionClock()
    clock.set_state(states[0], True, 0)
    before_motion = clock.sample(at)
    before = pixels(surface.frame(before_motion)).astype(float)
    clock.set_state(states[1], True, at)
    assert clock.sample(at) == before_motion
    at_switch = pixels(surface.frame(clock.sample(at)))
    assert np.array_equal(before, at_switch)
    after = pixels(surface.frame(clock.sample(at + 1 / 60))).astype(float)
    assert np.abs(before - after).mean() < 2
