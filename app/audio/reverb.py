"""
Mild indoor room reverb for RainRoom3D.

Lightweight early reflections + short freeverb-style comb/allpass late tail.
Uses vectorized delay histories for realtime block processing.
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

import numpy as np

try:
    from scipy import signal as sp_signal
    _HAS_SCIPY = True
except Exception:  # pragma: no cover
    sp_signal = None
    _HAS_SCIPY = False


def _lp_alpha(fc: float, sr: float) -> float:
    dt = 1.0 / max(1.0, sr)
    rc = 1.0 / (2.0 * math.pi * max(20.0, fc))
    return dt / (rc + dt)


class _HistDelay:
    def __init__(self, max_n: int):
        self.max_n = max(8, int(max_n) + 8)
        self.hist = np.zeros(self.max_n, dtype=np.float64)

    def process(self, x: np.ndarray, delay_n: int) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64).reshape(-1)
        n = len(x)
        if n == 0:
            return x
        d = int(max(1, min(delay_n, self.max_n - 1)))
        combined = np.concatenate([self.hist, x])
        start = len(self.hist) - d
        y = combined[start : start + n].copy()
        self.hist = combined[-self.max_n :]
        return y


class MonoRoomReverb:
    """Stateful mono room: multi-tap early + 4 comb + 2 allpass late."""

    _COMB_BASE = (1557, 1617, 1491, 1422)
    _AP_BASE = (225, 556)
    _EARLY_MS = (7.0, 11.0, 17.0, 23.0, 31.0)
    _EARLY_G = (0.42, 0.32, 0.24, 0.18, 0.12)

    def __init__(self, sr: int = 48000, room_size: float = 0.45, damping: float = 0.45, wet: float = 0.18):
        self.sr = int(sr)
        self.room_size = float(max(0.15, min(1.0, room_size)))
        self.damping = float(max(0.05, min(0.95, damping)))
        self.wet = float(max(0.0, min(0.7, wet)))
        self._build()

    def _build(self):
        sr = self.sr
        scale = sr / 48000.0
        size = 0.55 + 0.9 * self.room_size

        self._early_d: List[_HistDelay] = []
        self._early_n: List[int] = []
        self._early_g: List[float] = []
        for ms, g in zip(self._EARLY_MS, self._EARLY_G):
            n = max(2, int(ms * 0.001 * sr * size))
            self._early_d.append(_HistDelay(n + 64))
            self._early_n.append(n)
            self._early_g.append(g * (0.7 + 0.3 * self.room_size))

        self._comb_d: List[_HistDelay] = []
        self._comb_n: List[int] = []
        self._comb_fb: List[float] = []
        self._comb_filter: List[float] = []
        self._comb_damp = 1.0 - self.damping * 0.55
        for base in self._COMB_BASE:
            n = max(4, int(base * scale * size))
            self._comb_d.append(_HistDelay(n + 64))
            self._comb_n.append(n)
            self._comb_fb.append(0.72 + 0.18 * self.room_size)
            self._comb_filter.append(0.0)

        self._ap_d: List[_HistDelay] = []
        self._ap_n: List[int] = []
        self._ap_g = 0.5
        for base in self._AP_BASE:
            n = max(2, int(base * scale))
            self._ap_d.append(_HistDelay(n + 32))
            self._ap_n.append(n)

        self._pre_lp = 0.0
        self._pre_a = _lp_alpha(6500.0, sr)

    def reset(self):
        self._build()

    def set_params(self, room_size: Optional[float] = None, damping: Optional[float] = None, wet: Optional[float] = None):
        rebuild = False
        if room_size is not None and abs(float(room_size) - self.room_size) > 0.02:
            self.room_size = float(max(0.15, min(1.0, room_size)))
            rebuild = True
        if damping is not None:
            self.damping = float(max(0.05, min(0.95, damping)))
            self._comb_damp = 1.0 - self.damping * 0.55
        if wet is not None:
            self.wet = float(max(0.0, min(0.7, wet)))
        if rebuild:
            self._build()
        else:
            for i in range(len(self._comb_fb)):
                self._comb_fb[i] = 0.72 + 0.18 * self.room_size

    def process(self, dry: np.ndarray) -> np.ndarray:
        x = np.asarray(dry, dtype=np.float64).reshape(-1)
        n = len(x)
        if n == 0 or self.wet <= 1e-4:
            return x.copy()

        # Input LP (stateful)
        a = self._pre_a
        if _HAS_SCIPY:
            pre, zf = sp_signal.lfilter([a], [1.0, -(1.0 - a)], x, zi=[self._pre_lp])
            self._pre_lp = float(zf[0])
        else:
            pre = np.empty(n, dtype=np.float64)
            s = self._pre_lp
            for i, v in enumerate(x):
                s = s + a * (v - s)
                pre[i] = s
            self._pre_lp = s

        early = np.zeros(n, dtype=np.float64)
        for dl, dn, g in zip(self._early_d, self._early_n, self._early_g):
            early += dl.process(pre, dn) * g

        # Combs: approximate freeverb with delayed feedback (block-wise)
        # y[n] = x[n] + fb * lp(y[n-D])  — use last comb output stored in delay hist
        comb_sum = np.zeros(n, dtype=np.float64)
        damp = self._comb_damp
        for i, (dl, dn, fb) in enumerate(zip(self._comb_d, self._comb_n, self._comb_fb)):
            delayed = dl.process(pre, dn)
            # one-pole on delayed for damping (block zi)
            filt = self._comb_filter[i]
            if _HAS_SCIPY:
                # y = damp*x + (1-damp)*y_prev  → lfilter
                alpha = 1.0 - damp  # careful: we want y = damp*x + (1-damp)*y
                # rewrite: y[n] = (1-damp)*y[n-1] + damp*x[n]
                b = [damp]
                a_c = [1.0, -(1.0 - damp)]
                filtered, zf = sp_signal.lfilter(b, a_c, delayed, zi=[filt])
                self._comb_filter[i] = float(zf[0])
            else:
                filtered = np.empty(n, dtype=np.float64)
                f = filt
                for k, v in enumerate(delayed):
                    f = damp * v + (1.0 - damp) * f
                    filtered[k] = f
                self._comb_filter[i] = f
            # inject feedback into delay by writing pre + fb*filtered back next call
            # Approximate: mix feedback into next input via delay hist pad
            # Store feedback by adding into hist tail
            fb_sig = pre + fb * filtered
            # re-seed hist end with fb signal so next process sees feedback
            # (simple: mix into delay line hist)
            m = min(len(dl.hist), len(fb_sig))
            dl.hist[-m:] = 0.65 * dl.hist[-m:] + 0.35 * fb_sig[-m:]
            comb_sum += filtered
        comb_sum *= 0.25

        late = comb_sum
        g_ap = self._ap_g
        for dl, dn in zip(self._ap_d, self._ap_n):
            buf_out = dl.process(late, dn)
            # y = -x + delayed; write x + g*delayed into hist
            y = -late + buf_out
            m = min(len(dl.hist), n)
            # approximate allpass write
            dl.hist[-m:] = late[-m:] + g_ap * buf_out[-m:]
            late = y

        wet = early * 0.55 + late * 0.85
        pk = float(np.max(np.abs(wet)) + 1e-12)
        if pk > 0.9:
            wet *= 0.9 / pk
        w = self.wet
        return (1.0 - 0.35 * w) * x + w * wet


class StereoRoomReverb:
    """Two mono units with slightly different sizes for L/R decorrelation."""

    def __init__(self, sr: int = 48000, room_size: float = 0.45, damping: float = 0.45, wet: float = 0.20):
        self.sr = int(sr)
        self.L = MonoRoomReverb(sr, room_size=room_size, damping=damping, wet=wet)
        self.R = MonoRoomReverb(sr, room_size=room_size * 1.07, damping=damping * 0.95, wet=wet)

    def reset(self):
        self.L.reset()
        self.R.reset()

    def set_params(self, room_size: Optional[float] = None, damping: Optional[float] = None, wet: Optional[float] = None):
        self.L.set_params(room_size, damping, wet)
        rs = None if room_size is None else float(room_size) * 1.07
        dm = None if damping is None else float(damping) * 0.95
        self.R.set_params(rs, dm, wet)

    def process(self, dry_stereo: np.ndarray) -> np.ndarray:
        x = np.asarray(dry_stereo, dtype=np.float64)
        if x.ndim != 2 or x.shape[1] != 2 or x.shape[0] == 0:
            return x
        mid = 0.5 * (x[:, 0] + x[:, 1])
        l_in = 0.82 * x[:, 0] + 0.18 * mid
        r_in = 0.82 * x[:, 1] + 0.18 * mid
        left = self.L.process(l_in)
        right = self.R.process(r_in)
        return np.column_stack([left, right])


def room_reverb_from_layout(
    width: float,
    depth: float,
    height: float = 2.6,
    open_avg: float = 0.5,
    sr: int = 48000,
) -> Tuple[float, float, float]:
    """Map house size + open windows → (room_size, damping, wet)."""
    vol = max(8.0, float(width) * float(depth) * float(height))
    size = max(0.2, min(0.95, math.log10(vol) / 2.2))
    open_avg = max(0.0, min(1.0, float(open_avg)))
    # Keep reverb light so drop impacts stay clear (open windows dump energy out)
    wet = 0.06 + 0.12 * size * (1.0 - 0.65 * open_avg)
    damping = 0.38 + 0.35 * open_avg + 0.08 * (1.0 - size)
    return size, damping, wet
