"""
Place overlapping rain grains into one stream without turning them into hiss.

√(a²+b²) energy-merge of many uncorrelated ticks fills every sample and
reads as static. Rain needs *gaps*: a new drop occupies silence; where two
attacks collide, keep the stronger hit and lift it slightly.
"""

from __future__ import annotations

import numpy as np

# Don't let a fuse bus grow past ~2 s of pending audio
_MAX_PENDING = 96000
_QUIET = 2.5e-4


def _pad_mono(a: np.ndarray, b: np.ndarray):
    a = np.asarray(a, dtype=np.float64).reshape(-1)
    b = np.asarray(b, dtype=np.float64).reshape(-1)
    n = max(len(a), len(b))
    if n == 0:
        z = np.zeros(0, dtype=np.float64)
        return z, z
    if len(a) < n:
        aa = np.zeros(n, dtype=np.float64)
        aa[: len(a)] = a
        a = aa
    if len(b) < n:
        bb = np.zeros(n, dtype=np.float64)
        bb[: len(b)] = b
        b = bb
    return a, b


def wet_merge(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Merge two mono rain grains, keeping pitter-patter gaps.

    Quiet + drop → the drop.
    Two attacks at once → the louder waveform, up to ~1.2× (not a stack).
    """
    a, b = _pad_mono(a, b)
    if a.size == 0:
        return a
    ea = np.abs(a)
    eb = np.abs(b)
    out = np.where(eb > ea, b, a)
    both = (ea > _QUIET) & (eb > _QUIET)
    if np.any(both):
        peak = np.maximum(ea[both], eb[both])
        makeup = np.sqrt(ea[both] * ea[both] + eb[both] * eb[both]) / (peak + 1e-12)
        makeup = np.clip(makeup, 1.0, 1.18)
        out = out.copy()
        out[both] = out[both] * makeup
    return out


def energy_fuse(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Back-compat alias — wet_merge (gap-preserving), not √(a²+b²) hash."""
    return wet_merge(a, b)


def wet_merge_2d(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Stereo wet-merge, per channel."""
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    if a.ndim == 1:
        a = np.stack([a, a], axis=1)
    if b.ndim == 1:
        b = np.stack([b, b], axis=1)
    if a.shape[1] < 2:
        pad = np.zeros((a.shape[0], 2), dtype=np.float64)
        pad[:, : a.shape[1]] = a
        a = pad
    if b.shape[1] < 2:
        pad = np.zeros((b.shape[0], 2), dtype=np.float64)
        pad[:, : b.shape[1]] = b
        b = pad
    n = max(a.shape[0], b.shape[0])
    if a.shape[0] < n:
        aa = np.zeros((n, 2), dtype=np.float64)
        aa[: a.shape[0]] = a[:, :2]
        a = aa
    else:
        a = a[:n, :2]
    if b.shape[0] < n:
        bb = np.zeros((n, 2), dtype=np.float64)
        bb[: b.shape[0]] = b[:, :2]
        b = bb
    else:
        b = b[:n, :2]
    out = np.empty_like(a)
    out[:, 0] = wet_merge(a[:, 0], b[:, 0])
    out[:, 1] = wet_merge(a[:, 1], b[:, 1])
    return out


def energy_fuse_2d(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return wet_merge_2d(a, b)


class FuseBus:
    """One running mono stream. New grains fuse at a write offset (delay)."""

    __slots__ = ("buf", "max_n")

    def __init__(self, max_n: int = _MAX_PENDING):
        self.buf = np.zeros(0, dtype=np.float64)
        self.max_n = int(max_n)

    def clear(self) -> None:
        self.buf = np.zeros(0, dtype=np.float64)

    @property
    def remaining(self) -> int:
        return int(self.buf.shape[0])

    def fuse(self, grain: np.ndarray, delay_n: int = 0) -> None:
        grain = np.asarray(grain, dtype=np.float64).reshape(-1)
        if grain.size == 0:
            return
        start = max(0, int(delay_n))
        end = start + int(grain.shape[0])
        if end > self.max_n:
            # Keep the live head; drop anything that would sit too far ahead
            end = self.max_n
            grain = grain[: max(0, end - start)]
            if grain.size == 0:
                return
            end = start + int(grain.shape[0])
        if end > len(self.buf):
            self.buf = np.pad(self.buf, (0, end - len(self.buf)))
        sl = self.buf[start:end]
        self.buf[start:end] = energy_fuse(sl, grain)

    def read(self, frames: int) -> np.ndarray:
        frames = int(frames)
        if frames <= 0:
            return np.zeros(0, dtype=np.float64)
        if len(self.buf) <= 0:
            return np.zeros(frames, dtype=np.float64)
        if len(self.buf) < frames:
            out = np.zeros(frames, dtype=np.float64)
            out[: len(self.buf)] = self.buf
            self.buf = np.zeros(0, dtype=np.float64)
            return out
        out = np.array(self.buf[:frames], copy=True)
        self.buf = self.buf[frames:]
        return out


class FuseStereo:
    """One running stereo stream (You / headphones)."""

    __slots__ = ("buf", "max_n")

    def __init__(self, max_n: int = _MAX_PENDING):
        self.buf = np.zeros((0, 2), dtype=np.float64)
        self.max_n = int(max_n)

    def clear(self) -> None:
        self.buf = np.zeros((0, 2), dtype=np.float64)

    @property
    def remaining(self) -> int:
        return int(self.buf.shape[0])

    def fuse(self, grain: np.ndarray, delay_n: int = 0) -> None:
        g = np.asarray(grain, dtype=np.float64)
        if g.size == 0:
            return
        if g.ndim == 1:
            g = np.stack([g, g], axis=1)
        else:
            if g.shape[1] < 2:
                pad = np.zeros((g.shape[0], 2), dtype=np.float64)
                pad[:, : g.shape[1]] = g
                g = pad
            else:
                g = g[:, :2]
        start = max(0, int(delay_n))
        end = start + int(g.shape[0])
        if end > self.max_n:
            end = self.max_n
            g = g[: max(0, end - start)]
            if g.size == 0:
                return
            end = start + int(g.shape[0])
        if end > self.buf.shape[0]:
            pad = np.zeros((end, 2), dtype=np.float64)
            if self.buf.shape[0]:
                pad[: self.buf.shape[0]] = self.buf
            self.buf = pad
        sl = self.buf[start:end]
        self.buf[start:end] = energy_fuse_2d(sl, g)

    def read(self, frames: int) -> np.ndarray:
        frames = int(frames)
        if frames <= 0:
            return np.zeros((0, 2), dtype=np.float64)
        if self.buf.shape[0] <= 0:
            return np.zeros((frames, 2), dtype=np.float64)
        if self.buf.shape[0] < frames:
            out = np.zeros((frames, 2), dtype=np.float64)
            out[: self.buf.shape[0]] = self.buf
            self.buf = np.zeros((0, 2), dtype=np.float64)
            return out
        out = np.array(self.buf[:frames], copy=True)
        self.buf = self.buf[frames:]
        return out
