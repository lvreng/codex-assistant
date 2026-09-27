"""Continuity and native 16/32-bit transition contracts, without USB or accounts."""
import ctypes
import itertools
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from celestial import CelestialLayer
from thinker_transition import ThinkerTransition, MODEL_IDS


def pixels(image):
    image = image.convertToFormat(QImage.Format.Format_RGB32)
    return np.asarray(image.constBits()).reshape(image.height(), image.bytesPerLine()).copy()


@pytest.fixture(scope="module")
def scenes():
    application = QApplication.instance() or QApplication([])
    layer = CelestialLayer()
    layer.preload(1)
    layer.set_state(1, True, now=0)
    motion = layer.clock.sample(20)
    images = {key: layer.scene(key, motion) for key in MODEL_IDS}
    yield images
    assert application is QApplication.instance()


@pytest.mark.parametrize("source,target", list(itertools.permutations(MODEL_IDS, 2)))
def test_all_twelve_routes_have_exact_endpoints_and_visible_motion(scenes, source, target):
    engine = ThinkerTransition()
    first, last = scenes[source], scenes[target]
    assert np.array_equal(pixels(engine.frame(first,last,source,target,0)),pixels(first))
    assert np.array_equal(pixels(engine.frame(first,last,source,target,1)),pixels(last))
    middle = pixels(engine.frame(first,last,source,target,.5)).astype(float)
    assert middle.std() > 20
    assert np.abs(middle-pixels(first)).mean() > 3
    assert np.abs(middle-pixels(last)).mean() > 3
    # No first/last-frame discontinuity from newly introduced cutout masks.
    near_first = pixels(engine.frame(first,last,source,target,.001))
    near_last = pixels(engine.frame(first,last,source,target,.999))
    assert np.abs(near_first.astype(float)-pixels(first)).mean() < 1.0
    assert np.abs(near_last.astype(float)-pixels(last)).mean() < 1.0


def test_interrupted_switch_uses_current_composite_without_a_jump(scenes):
    layer = CelestialLayer()
    layer.select("sol",now=0)
    layer.last_picture = scenes["sol"]
    layer.select("astra",now=10)
    engine = ThinkerTransition()
    midway = engine.frame(scenes["sol"], scenes["astra"], "sol", "astra", .4)
    layer.last_picture = midway
    layer.select("terra",now=10.7)
    assert layer.duration == ThinkerTransition.INTERRUPTED_DURATION
    assert layer.source_key is None
    assert np.array_equal(pixels(layer.source_picture),pixels(midway))
    resumed = engine.frame(layer.source_picture,scenes["terra"],layer.source_key,"terra",0)
    assert np.array_equal(pixels(resumed),pixels(midway))


def test_esp32_rgb565_path_preserves_endpoints_and_buffer_guards():
    engine = ThinkerTransition()
    width,height=560,416
    a=np.full((height,width),0xf986,dtype=np.uint16)
    b=np.full((height,width),0x127a,dtype=np.uint16)
    guarded=np.full(width*height+2,0xdead,dtype=np.uint16)
    for p in (0,.01,.25,.5,.75,.99,1):
        engine.render(ctypes.c_void_p(a.ctypes.data),ctypes.c_void_p(b.ctypes.data),
                      ctypes.c_void_p(guarded[1:].ctypes.data),width,height,1,4,p,16)
        assert guarded[0] == guarded[-1] == 0xdead
        if p == 0:
            assert np.array_equal(guarded[1:-1],a.ravel())
        if p == 1:
            assert np.array_equal(guarded[1:-1],b.ravel())


@pytest.mark.parametrize("color", [0xffff,0xf800,0x07e0,0x001f,0x8410,0x0083])
def test_rgb565_blending_never_leaks_color_between_channels(color):
    engine=ThinkerTransition()
    a=np.full((8,8),color,dtype=np.uint16)
    b=np.zeros_like(a)
    output=np.zeros_like(a)
    engine.render(ctypes.c_void_p(a.ctypes.data),ctypes.c_void_p(b.ctypes.data),
                  ctypes.c_void_p(output.ctypes.data),8,8,0,1,.5,16)
    expected=(((color>>11)//2)<<11) | ((((color>>5)&63)//2)<<5) | ((color&31)//2)
    assert np.all(output==expected)
