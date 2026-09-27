"""Offscreen artwork worker; the bridge remains the sole serial/API owner."""
from __future__ import annotations

import multiprocessing as mp
import os
from pathlib import Path
import queue
import sys
import time
import struct

from art_protocol import KEYS, art_packets


def _render(configs, frames):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "desktop"))
    from PySide6.QtWidgets import QApplication
    from celestial import model_art
    from device_artwork import DeviceArtwork

    app = QApplication([])
    artwork = DeviceArtwork()
    config = None
    while True:
        try:
            config = configs.get(timeout=1) if config is None else configs.get_nowait()
            while True:
                config = configs.get_nowait()
        except queue.Empty:
            pass
        if config is None:
            continue
        if config == "STOP":
            return
        generation, theme, preview, style, minimum, maximum, model, state, connected = config
        key = KEYS[preview] if preview else model_art(model) if theme == 8 else None
        began = time.monotonic()
        artwork.configure(key, state, connected, style, minimum, maximum, bool(preview), began)
        data = artwork.frame(began)
        # Latest-frame mailbox: never build a queue of stale animation.
        try:
            frames.get_nowait()
        except queue.Empty:
            pass
        try:
            frames.put_nowait((generation, KEYS.index(key), data))
        except queue.Full:
            pass
        app.processEvents()
        time.sleep(max(0.001, 1 / 30 - (time.monotonic() - began)))


class ArtStream:
    def __init__(self):
        self.worker = None
        self.configs = self.frames = None
        self.last_config = None
        self.chunks = iter(())
        self.pending_frame = None
        self.sent_at = 0.0
        self.frame = 0
        self.last_empty = 0.0
        self.retry_at = 0.0

    def configure(self, request, model, state, connected=True):
        config = (*request, model, state, connected)
        if self.worker is None or not self.worker.is_alive():
            if self.worker is not None:
                self.close()
                self.retry_at = time.monotonic() + 5
            if time.monotonic() < self.retry_at:
                return
            context = mp.get_context("spawn")
            self.configs = context.Queue(maxsize=2)
            self.frames = context.Queue(maxsize=2)
            self.worker = context.Process(target=_render, args=(self.configs, self.frames),
                                          name="p4-artwork", daemon=True)
            self.worker.start()
            self.last_config = None
        if config != self.last_config:
            if self.last_config and config[0] != self.last_config[0]:
                self.chunks = iter(())
                self.pending_frame = None
            try:
                self.configs.get_nowait()
            except queue.Empty:
                pass
            try:
                self.configs.put_nowait(config)
                self.last_config = config
            except queue.Full:
                pass

    def pump(self, connection, now):
        if self.worker is None or connection.art_request is None:
            return
        if self.pending_frame is not None:
            if connection.art_ack == self.pending_frame or now - self.sent_at > 1:
                self.pending_frame = None
            else:
                return
        try:
            chunk = next(self.chunks)
        except StopIteration:
            result = None
            try:
                while True:
                    result = self.frames.get_nowait()
            except queue.Empty:
                pass
            if result is None:
                return
            generation, key, data = result
            if generation != connection.art_request[0]:
                return
            if key == 0 and now - self.last_empty < 0.5:
                return
            if key == 0:
                self.last_empty = now
            self.frame = (self.frame + 1) & 0xFFFF
            connection.art_ack = None
            connection.art_ack_status = None
            self.chunks = iter(art_packets(self.frame, key, data, generation))
            chunk = next(self.chunks)
        if connection.port.write(chunk) != len(chunk):
            raise RuntimeError("Incomplete artwork write")
        # One chunk per bridge pump permits telemetry and touch ACK interleaving.
        # Determine completion without duplicating or losing the next chunk.
        length = struct.unpack_from("<H", chunk, 4)[0]
        offset, total = struct.unpack_from("<II", chunk, 14)
        if offset + length - 14 == total:
            self.pending_frame = self.frame
            self.sent_at = now

    def close(self):
        if self.worker is not None:
            self.worker.terminate()
            self.worker.join(timeout=3)
            if self.worker.is_alive():
                self.worker.kill()
                self.worker.join()
            self.worker = None
        for mailbox in (self.configs, self.frames):
            if mailbox is not None:
                mailbox.cancel_join_thread()
                mailbox.close()
        self.configs = self.frames = None
        self.last_config = None
        self.chunks = iter(())
        self.pending_frame = None
