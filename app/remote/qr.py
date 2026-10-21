"""A small, dependency-free QR Code encoder (ISO/IEC 18004, model 2).

Byte mode only (the phone link is plain ASCII), every error-correction level,
versions 1-40, all eight masks with the standard penalty scoring.

    >>> m = encode("http://192.168.1.20:8765/?k=abc123")
    >>> len(m) == len(m[0])        # square module matrix, True = dark
    True

``encode`` returns a list of rows (``m[y][x]``) WITHOUT the quiet zone; draw
at least 4 light modules around it. ``to_text`` / ``to_svg`` are tiny
renderers for the terminal and for files; the desktop turns the matrix into a
QImage itself.

The tables and the layout follow the specification; the output was checked
against an independent decoder (jsQR) for many lengths, levels and versions.
"""

from __future__ import annotations

from typing import List, Optional, Sequence

Matrix = List[List[bool]]

# Error-correction level -> (format bits, index into the tables)
_ECL = {"L": (1, 0), "M": (0, 1), "Q": (3, 2), "H": (2, 3)}

# Index [ecl][version]; version 0 unused.
_ECC_PER_BLOCK = (
    (-1, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28,
     28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    (-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26,
     26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28),
    (-1, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30,
     28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    (-1, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28,
     30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
)
_NUM_BLOCKS = (
    (-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8,
     8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25),
    (-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16,
     17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49),
    (-1, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20,
     23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68),
    (-1, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25,
     25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81),
)

MIN_VERSION, MAX_VERSION = 1, 40


# ----- Reed-Solomon over GF(256), polynomial 0x11D -----

def _gf_mul(x: int, y: int) -> int:
    z = 0
    for i in range(7, -1, -1):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z & 0xFF


def _rs_divisor(degree: int) -> List[int]:
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 0x02)
    return result


def _rs_remainder(data: Sequence[int], divisor: Sequence[int]) -> List[int]:
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _gf_mul(coef, factor)
    return result


# ----- capacity -----

def _raw_data_modules(ver: int) -> int:
    result = (16 * ver + 128) * ver + 64
    if ver >= 2:
        num_align = ver // 7 + 2
        result -= (25 * num_align - 10) * num_align - 55
        if ver >= 7:
            result -= 36
    return result


def _data_codewords(ver: int, ecl_idx: int) -> int:
    return (_raw_data_modules(ver) // 8
            - _ECC_PER_BLOCK[ecl_idx][ver] * _NUM_BLOCKS[ecl_idx][ver])


def _alignment_positions(ver: int) -> List[int]:
    if ver == 1:
        return []
    num_align = ver // 7 + 2
    size = ver * 4 + 17
    step = (ver * 8 + num_align * 3 + 5) // (num_align * 4 - 4) * 2
    result = [size - 7 - i * step for i in range(num_align - 1)] + [6]
    return list(reversed(result))


def capacity_bytes(ver: int, ecc: str = "M") -> int:
    """How many bytes of byte-mode data fit in ``ver`` at level ``ecc``."""
    _fmt, idx = _ECL[ecc]
    cc_bits = 8 if ver <= 9 else 16
    return (_data_codewords(ver, idx) * 8 - 4 - cc_bits) // 8


# ----- the symbol -----

class _Symbol:
    def __init__(self, ver: int):
        self.ver = ver
        self.size = ver * 4 + 17
        self.mod = [[False] * self.size for _ in range(self.size)]
        self.fn = [[False] * self.size for _ in range(self.size)]

    def set_fn(self, x: int, y: int, dark: bool) -> None:
        self.mod[y][x] = bool(dark)
        self.fn[y][x] = True

    def draw_function_patterns(self) -> None:
        n = self.size
        for i in range(n):
            self.set_fn(6, i, i % 2 == 0)
            self.set_fn(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < n and 0 <= y < n:
                        self.set_fn(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        pos = _alignment_positions(self.ver)
        last = len(pos) - 1
        for i, ax in enumerate(pos):
            for j, ay in enumerate(pos):
                if (i == 0 and j == 0) or (i == 0 and j == last) or (i == last and j == 0):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.set_fn(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
        self.draw_format(0, 0)  # reserve; real bits drawn after masking
        self.draw_version()

    def draw_format(self, fmt_bits: int, mask: int) -> None:
        data = fmt_bits << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412
        n = self.size

        def bit(i: int) -> bool:
            return ((bits >> i) & 1) != 0

        for i in range(6):
            self.set_fn(8, i, bit(i))
        self.set_fn(8, 7, bit(6))
        self.set_fn(8, 8, bit(7))
        self.set_fn(7, 8, bit(8))
        for i in range(9, 15):
            self.set_fn(14 - i, 8, bit(i))
        for i in range(8):
            self.set_fn(n - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self.set_fn(8, n - 15 + i, bit(i))
        self.set_fn(8, n - 8, True)  # the always-dark module

    def draw_version(self) -> None:
        if self.ver < 7:
            return
        rem = self.ver
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        bits = self.ver << 12 | rem
        n = self.size
        for i in range(18):
            dark = ((bits >> i) & 1) != 0
            a, b = n - 11 + i % 3, i // 3
            self.set_fn(a, b, dark)
            self.set_fn(b, a, dark)

    def draw_codewords(self, data: Sequence[int]) -> None:
        n = self.size
        i = 0
        total = len(data) * 8
        right = n - 1
        while right >= 1:
            if right == 6:
                right = 5
            upward = ((right + 1) & 2) == 0
            for vert in range(n):
                y = n - 1 - vert if upward else vert
                for j in range(2):
                    x = right - j
                    if not self.fn[y][x] and i < total:
                        self.mod[y][x] = ((data[i >> 3] >> (7 - (i & 7))) & 1) != 0
                        i += 1
            right -= 2

    def apply_mask(self, mask: int) -> None:
        n = self.size
        for y in range(n):
            row, fnrow = self.mod[y], self.fn[y]
            for x in range(n):
                if fnrow[x]:
                    continue
                if mask == 0:
                    inv = (x + y) % 2 == 0
                elif mask == 1:
                    inv = y % 2 == 0
                elif mask == 2:
                    inv = x % 3 == 0
                elif mask == 3:
                    inv = (x + y) % 3 == 0
                elif mask == 4:
                    inv = (x // 3 + y // 2) % 2 == 0
                elif mask == 5:
                    inv = x * y % 2 + x * y % 3 == 0
                elif mask == 6:
                    inv = (x * y % 2 + x * y % 3) % 2 == 0
                else:
                    inv = ((x + y) % 2 + x * y % 3) % 2 == 0
                if inv:
                    row[x] = not row[x]


_FINDER_A = (True, False, True, True, True, False, True, False, False, False, False)
_FINDER_B = tuple(reversed(_FINDER_A))


def penalty(mod: Matrix) -> int:
    """Standard mask penalty (rules N1..N4)."""
    n = len(mod)
    score = 0
    lines = [list(r) for r in mod] + [[mod[y][x] for y in range(n)] for x in range(n)]
    for line in lines:
        run_color, run = line[0], 1
        for v in line[1:]:
            if v == run_color:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run_color, run = v, 1
        if run >= 5:
            score += 3 + (run - 5)
        # 1:1:3:1:1 finder look-alike with 4 light modules on one side;
        # outside the symbol counts as light (the quiet zone).
        padded = [False] * 4 + line + [False] * 4
        for i in range(len(padded) - 10):
            w = tuple(padded[i:i + 11])
            if w == _FINDER_A or w == _FINDER_B:
                score += 40
    for y in range(n - 1):
        for x in range(n - 1):
            c = mod[y][x]
            if c == mod[y][x + 1] == mod[y + 1][x] == mod[y + 1][x + 1]:
                score += 3
    dark = sum(sum(1 for v in r if v) for r in mod)
    total = n * n
    k = (abs(dark * 20 - total * 10) + total - 1) // total - 1
    score += max(0, k) * 10
    return score


def _encode_bits(data: bytes, ver: int, ecl_idx: int) -> List[int]:
    bits: List[int] = []

    def put(val: int, length: int) -> None:
        for i in range(length - 1, -1, -1):
            bits.append((val >> i) & 1)

    put(0b0100, 4)
    put(len(data), 8 if ver <= 9 else 16)
    for b in data:
        put(b, 8)
    cap = _data_codewords(ver, ecl_idx) * 8
    put(0, min(4, cap - len(bits)))
    put(0, (8 - len(bits) % 8) % 8)
    pad = 0xEC
    while len(bits) < cap:
        put(pad, 8)
        pad ^= 0xEC ^ 0x11
    out = []
    for i in range(0, len(bits), 8):
        v = 0
        for b in bits[i:i + 8]:
            v = (v << 1) | b
        out.append(v)
    return out


def _add_ecc_and_interleave(data: List[int], ver: int, ecl_idx: int) -> List[int]:
    num_blocks = _NUM_BLOCKS[ecl_idx][ver]
    ecc_len = _ECC_PER_BLOCK[ecl_idx][ver]
    raw = _raw_data_modules(ver) // 8
    num_short = num_blocks - raw % num_blocks
    short_len = raw // num_blocks  # data + ecc of a short block
    divisor = _rs_divisor(ecc_len)
    blocks = []
    k = 0
    for i in range(num_blocks):
        dlen = short_len - ecc_len + (0 if i < num_short else 1)
        dat = data[k:k + dlen]
        k += dlen
        blocks.append((dat, _rs_remainder(dat, divisor)))
    out: List[int] = []
    for i in range(max(len(b[0]) for b in blocks)):
        for dat, _ecc in blocks:
            if i < len(dat):
                out.append(dat[i])
    for i in range(ecc_len):
        for _dat, ecc in blocks:
            out.append(ecc[i])
    assert len(out) == raw
    return out


def encode(text, ecc: str = "M", min_version: int = 1, max_version: int = MAX_VERSION,
           mask: Optional[int] = None) -> Matrix:
    """QR matrix for ``text`` (str is UTF-8 encoded) at level ``ecc``.

    Picks the smallest version that fits and the mask with the lowest
    penalty (or ``mask`` 0-7 when given). Raises ValueError if it can't fit.
    """
    data = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    ecc = str(ecc).upper()
    if ecc not in _ECL:
        raise ValueError(f"unknown error-correction level {ecc!r}")
    fmt_bits, idx = _ECL[ecc]
    lo = max(MIN_VERSION, int(min_version))
    hi = min(MAX_VERSION, int(max_version))
    ver = next((v for v in range(lo, hi + 1) if capacity_bytes(v, ecc) >= len(data)), None)
    if ver is None:
        raise ValueError(f"{len(data)} bytes do not fit a version {lo}-{hi} QR code at level {ecc}")
    codewords = _add_ecc_and_interleave(_encode_bits(data, ver, idx), ver, idx)
    sym = _Symbol(ver)
    sym.draw_function_patterns()
    sym.draw_codewords(codewords)
    masks = range(8) if mask is None else [int(mask) % 8]
    best = None
    for m in masks:
        sym.apply_mask(m)
        sym.draw_format(fmt_bits, m)
        score = penalty(sym.mod)
        if best is None or score < best[0]:
            best = (score, m)
        sym.apply_mask(m)  # XOR again = undo
    sym.apply_mask(best[1])
    sym.draw_format(fmt_bits, best[1])
    return [list(r) for r in sym.mod]


def version_of(matrix: Matrix) -> int:
    return (len(matrix) - 17) // 4


def to_text(matrix: Matrix, border: int = 2, invert: bool = False) -> str:
    """Two rows per text line with half-block characters (for a terminal)."""
    n = len(matrix)

    def dark(x: int, y: int) -> bool:
        v = 0 <= x < n and 0 <= y < n and matrix[y][x]
        return (not v) if invert else v

    lines = []
    for y in range(-border, n + border, 2):
        row = []
        for x in range(-border, n + border):
            top, bot = dark(x, y), dark(x, y + 1)
            row.append("█" if top and bot else "▀" if top else "▄" if bot else " ")
        lines.append("".join(row))
    return "\n".join(lines)


def to_svg(matrix: Matrix, border: int = 4, dark: str = "#000", light: str = "#fff") -> str:
    n = len(matrix)
    parts = [f"M{x + border},{y + border}h1v1h-1z"
             for y in range(n) for x in range(n) if matrix[y][x]]
    s = n + 2 * border
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {s} {s}" '
            f'shape-rendering="crispEdges"><rect width="100%" height="100%" fill="{light}"/>'
            f'<path d="{"".join(parts)}" fill="{dark}"/></svg>')


__all__ = ["capacity_bytes", "encode", "penalty", "to_svg", "to_text", "version_of"]
