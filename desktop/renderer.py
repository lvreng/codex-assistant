"""Thin ctypes adapter. LVGL owns the RGB565 buffer for the process lifetime."""
from __future__ import annotations

import ctypes
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
LIBRARY = PROJECT / "build/desktop/libpet_desktop_ui.so"


class Renderer:
    def __init__(self, library=LIBRARY):
        self.native = ctypes.CDLL(str(library))
        signatures = {
            "pet_init": (ctypes.POINTER(ctypes.c_uint16), []),
            "pet_tick": (ctypes.c_uint64, [ctypes.c_uint32]),
            "pet_feed": (ctypes.c_int, [ctypes.c_void_p, ctypes.c_size_t]),
            "pet_pointer": (None, [ctypes.c_int, ctypes.c_int, ctypes.c_int]),
            "pet_theme": (None, [ctypes.c_int]),
            "pet_cycle_theme": (None, []),
            "pet_brightness": (None, [ctypes.c_int]),
            "pet_navigation": (ctypes.c_int, []),
            "pet_details": (None, [ctypes.c_int]),
            "pet_value": (ctypes.c_int, [ctypes.c_int]),
            "pet_account_text": (ctypes.c_char_p, [ctypes.c_int]),
        }
        for name, (result, arguments) in signatures.items():
            function = getattr(self.native, name)
            function.restype = result
            function.argtypes = arguments
        pointer = self.native.pet_init()
        self.buffer = (ctypes.c_ubyte * (800 * 480 * 2)).from_address(
            ctypes.addressof(pointer.contents)
        )

    def tick(self, milliseconds):
        return self.native.pet_tick(max(0, int(milliseconds)))

    def feed(self, data):
        return self.native.pet_feed(data, len(data))

    def pointer(self, x, y, pressed):
        self.native.pet_pointer(x, y, int(pressed))

    def theme(self, theme):
        self.native.pet_theme(theme)

    def cycle_theme(self):
        self.native.pet_cycle_theme()

    def navigate(self):
        return self.native.pet_navigation()

    def details(self, opened):
        self.native.pet_details(int(opened))

    def value(self, field):
        return self.native.pet_value(field)

    def account_text(self, field):
        return self.native.pet_account_text(field).decode("utf-8")
