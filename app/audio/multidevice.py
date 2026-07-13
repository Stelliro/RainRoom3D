
"""Compatibility wrapper — multi-device rain now lives in spatial_engine."""

from __future__ import annotations

from app.audio.spatial_engine import SpatialRainEngine, list_output_devices
from app.models.room import Room


class MultiDeviceEngine(SpatialRainEngine):
    """Back-compat alias used by older call sites."""

    def __init__(self, room: Room, samplerate=48000, blocksize=1024):
        super().__init__(room, samplerate=samplerate, blocksize=blocksize)
        # Older UI expected .base
        self.base = self

    def _emit_block(self, n):
        # Stereo fold-down of spatial field for any legacy caller
        mix = self._advance(int(n))
        import numpy as np
        left = np.zeros(int(n), dtype=np.float64)
        right = np.zeros(int(n), dtype=np.float64)
        L = self.room.listener
        import math
        for i, spk in enumerate(self.room.speakers):
            mono = mix.get(i)
            if mono is None or not spk.enabled:
                continue
            dx = spk.x - L.x
            dz = spk.z - L.z
            dist = max(0.3, math.hypot(dx, dz))
            att = 1.0 / (1.0 + 0.45 * (dist - 0.3))
            ang = math.atan2(dx, dz)
            gL = abs(math.cos((ang + math.pi * 0.5) * 0.5))
            gR = abs(math.sin((ang + math.pi * 0.5) * 0.5))
            left += mono * att * gL
            right += mono * att * gR
        out = np.stack([left, right], axis=1)
        pk = float(np.max(np.abs(out))) + 1e-12
        if pk > 0.95:
            out *= 0.95 / pk
        return out.astype(np.float32)


# re-export
_list_output_devices = list_output_devices
