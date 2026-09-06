"""HIM probe: quantity → audible level (drizzle silence)."""
from __future__ import annotations

import numpy as np

from app.audio.spatial_engine import SpatialRainEngine
from app.models.room import default_house


def measure(e, room, q: float, seconds: float = 2.5, seed: int = 7) -> dict:
    room.droplet_density = float(q)
    e._reset_sim(seed)
    frames = int(e.blocksize)
    nblocks = max(1, int(seconds * e.samplerate / frames))
    chunks = []
    for _ in range(nblocks):
        mix = e._advance(frames)
        chunks.append(np.asarray(mix.get(0, np.zeros(frames)), dtype=np.float64))
    x = np.concatenate(chunks) if chunks else np.zeros(1)
    pk = float(np.max(np.abs(x)) + 1e-12)
    rms = float(np.sqrt(np.mean(x * x)) + 1e-12)
    aud = float(np.mean(np.abs(x) > 0.003))
    q = max(0.0, min(1.0, float(q)))
    field = (0.008 + 0.14 * (q ** 0.90)) * float(e._master) * 0.05
    return {
        "q": q,
        "ips": e._ips(),
        "vips": e._voice_ips(),
        "chain": e._chain_hits(),
        "pk": pk,
        "rms": rms,
        "aud": aud,
        "field": field,
    }


def main() -> None:
    room = default_house()
    room.mix_wash = 0.05
    room.mix_droplets = 2.0
    room.rain_intensity = 0.6
    room.master_volume = 0.49
    eng = SpatialRainEngine(room, samplerate=48000, blocksize=2048)
    eng.use_drop_bank = True
    eng._drop_bank.ensure_built()
    def dump(title, eng, room, qs):
        print(title)
        print("q    ips    vips   ch    rms      pk      aud%    field   bi_rms   bi_pk")
        for q in qs:
            s = measure(eng, room, q)
            room.droplet_density = q
            eng._reset_sim(7)
            frames = int(eng.blocksize)
            nblocks = int(2.5 * eng.samplerate / frames)
            bi_chunks = []
            s0 = []
            for _ in range(nblocks):
                mix = eng._advance(frames)
                s0.append(np.asarray(mix.get(0, np.zeros(frames)), dtype=np.float64))
                if eng._last_binaural is not None and eng._last_binaural.shape[0] == frames:
                    bi_chunks.append(eng._last_binaural)
            x = np.concatenate(s0)
            pk = float(np.max(np.abs(x)) + 1e-12)
            rms = float(np.sqrt(np.mean(x * x)) + 1e-12)
            aud = float(np.mean(np.abs(x) > 0.003))
            if bi_chunks:
                b = np.concatenate(bi_chunks, axis=0)
                bi_rms = float(np.sqrt(np.mean(b * b)) + 1e-12)
                bi_pk = float(np.max(np.abs(b)) + 1e-12)
            else:
                bi_rms = 0.0
                bi_pk = 0.0
            field = (0.008 + 0.14 * (q ** 0.90)) * float(eng._master) * float(getattr(room, "mix_wash", 0.05))
            print(
                f"{q:.2f} {eng._ips():6.1f} {eng._voice_ips():6.1f} {eng._chain_hits():3d}  "
                f"{rms:.5f} {pk:.4f} {100.0 * aud:6.1f}  {field:.5f}  {bi_rms:.5f}  {bi_pk:.4f}"
            )

    qs = (0.00, 0.02, 0.04, 0.08, 0.12, 0.20, 0.35, 0.55, 1.0)
    dump("default_house speakers+You", eng, room, qs)

    from app.utils.persistence import load_room
    from pathlib import Path
    p = Path("configs/my_house.json")
    if p.is_file():
        mine = load_room(str(p))
        e2 = SpatialRainEngine(mine, samplerate=48000, blocksize=2048)
        e2.use_drop_bank = True
        e2._drop_bank = eng._drop_bank
        dump("my_house.json speakers+You", e2, mine, qs)


if __name__ == "__main__":
    main()
