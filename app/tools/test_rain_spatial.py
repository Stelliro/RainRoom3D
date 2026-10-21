"""
Checks for equal outdoor rain, couple-once spatialization, and surround map.

Run:  python -m app.tools.test_rain_spatial
"""

from __future__ import annotations

import math
import sys
import time

import numpy as np


def _ok(name: str, cond: bool, detail: str = "") -> bool:
    mark = "PASS" if cond else "FAIL"
    extra = f" — {detail}" if detail else ""
    print(f"  [{mark}] {name}{extra}")
    return bool(cond)


def test_ring_equal() -> bool:
    from app.models.room import default_house
    from app.audio.spatial_engine import SpatialRainEngine

    room = default_house()
    room.roof_material = "Shingle Roof"
    eng = SpatialRainEngine(room, samplerate=48000, blocksize=512)
    rw, rd = float(room.width), float(room.depth)
    counts = {"north": 0, "south": 0, "east": 0, "west": 0, "roof": 0, "other": 0}
    n = 4000
    for _ in range(n):
        layer, x, y, z, depth = eng._pick_source_3d()
        if layer == "roof":
            counts["roof"] += 1
            continue
        if layer == "near":
            counts["other"] += 1
            continue
        # Classify eaves/wall ring by which facade is nearest
        dn = abs(z - rd)
        ds = abs(z - 0.0)
        de = abs(x - rw)
        dw = abs(x - 0.0)
        side = min(
            (("north", dn), ("south", ds), ("east", de), ("west", dw)),
            key=lambda t: t[1],
        )[0]
        # Inside footprint with no roof tag → still count nearest wall
        if z > rd * 0.5 and dn <= de and dn <= dw:
            side = "north"
        counts[side] += 1

    yard = sum(counts[k] for k in ("north", "south", "east", "west"))
    if yard < 100:
        return _ok("ring spawn sample size", False, str(counts))
    fracs = {k: counts[k] / yard for k in ("north", "south", "east", "west")}
    mx, mn = max(fracs.values()), min(fracs.values())
    # Perimeter-uniform: 5×4 house → N/S slightly more than E/W, not 3× one side
    ok = (mx / max(1e-9, mn)) < 2.2 and mn > 0.12
    return _ok(
        "outdoor rain wraps all four facades",
        ok,
        f"fracs={ {k: round(v, 3) for k, v in fracs.items()} } roof={counts['roof']}/{n}",
    )


def test_rain_stays_on_the_house() -> bool:
    """Hits land on the house or a thin eaves ring — not a 40 m field — and
    the roof gets its share by area (house_model catchments)."""
    from app.models.room import default_house
    from app.audio.spatial_engine import SpatialRainEngine

    room = default_house()
    room.roof_material = "Tin Roof"
    room.droplet_density = 1.0
    eng = SpatialRainEngine(room, samplerate=48000, blocksize=512)
    rw, rd = float(room.width), float(room.depth)
    n = 800
    roof = 0
    far = 0
    eaves = 0
    for _ in range(n):
        layer, x, y, z, depth = eng._pick_source_3d()
        if layer == "roof":
            roof += 1
            continue
        # Outside the footprint
        outside = x < -0.05 or x > rw + 0.05 or z < -0.05 or z > rd + 0.05
        if outside and depth > 1.2:
            far += 1
        else:
            eaves += 1
    from app.audio import house_model as hm

    want = hm.layer_shares(hm.catchments(room))["roof"]
    ok = abs(roof / n - want) < 0.06 and far / n < 0.04
    return _ok(
        "rain stays on the house plus a thin eaves ring",
        ok,
        f"roof={roof}/{n} (area share {want:.2f}) eaves={eaves} far={far}",
    )


def test_wrap_leakage() -> bool:
    from app.models.room import default_house
    from app.audio.spatial import render_drop_to_receiver_mono

    room = default_house()  # windows on north + east only
    rng = np.random.RandomState(3)
    mono = rng.randn(2048).astype(np.float64) * 0.05
    src_south = (2.5, 0.05, -2.2)
    recv_north = (2.5, 1.1, 3.5)
    sig = render_drop_to_receiver_mono(room, mono, src_south, recv_north, 48000)
    peak = float(np.max(np.abs(sig)))
    return _ok(
        "south-side rain still reaches a north speaker (corner wrap)",
        peak > 1e-6,
        f"peak={peak:.3e}",
    )


def test_couple_once_all_speakers() -> bool:
    from app.models.room import default_house
    from app.audio.spatial import render_drop_to_receivers

    room = default_house()
    rng = np.random.RandomState(9)
    mono = rng.randn(1024).astype(np.float64) * 0.04
    src = (2.5, 0.02, 5.5)  # north yard
    recvs = [
        (i, (float(s.x), float(s.y), float(s.z)), 1.0)
        for i, s in enumerate(room.speakers)
    ]
    L = room.listener
    t0 = time.perf_counter()
    bufs, stereo, portals = render_drop_to_receivers(
        room,
        mono,
        src,
        recvs,
        48000,
        listener=((float(L.x), float(L.y), float(L.z)), 0.0),
        fast=True,
        binaural_quality="fast",
    )
    dt_fast = time.perf_counter() - t0
    t0 = time.perf_counter()
    bufs2, stereo2, portals2 = render_drop_to_receivers(
        room,
        mono,
        src,
        recvs,
        48000,
        listener=((float(L.x), float(L.y), float(L.z)), 0.0),
        fast=False,
        binaural_quality="full",
    )
    dt_full = time.perf_counter() - t0
    peaks = [float(np.max(np.abs(bufs[i]))) for i in bufs]
    ok_all = all(p > 1e-7 for p in peaks) and stereo is not None
    # Fast path should not be dramatically slower; we only assert it runs and
    # that every speaker got energy from one coupling.
    return _ok(
        "one coupling feeds every speaker + binaural",
        ok_all and len(portals) >= 1,
        f"peaks={[round(p, 5) for p in peaks]} portals={len(portals)} "
        f"fast={dt_fast*1e3:.1f}ms full={dt_full*1e3:.1f}ms",
    )


def test_surround_vbap() -> bool:
    from app.models.room import Speaker
    from app.audio.surround import mix_to_surround, vbap_gains, wall_channel_gains

    g = vbap_gains(0.0, 6)  # front
    # FL FR FC LFE BL BR — energy on FL/FR/FC, not rears
    ok_front = (g[0] + g[1] + g[2]) > 0.8 and (g[4] + g[5]) < 0.35
    g_r = vbap_gains(90.0, 8)
    ok_east = g_r[7] > 0.4  # SR

    walls = wall_channel_gains(6)
    ok_walls = all(k in walls for k in ("north", "east", "south", "west", "roof"))

    frames = 256
    n_spk = [
        Speaker(name="N", x=2.5, y=1.1, z=3.5),
        Speaker(name="E", x=4.2, y=1.1, z=2.0),
        Speaker(name="SW", x=1.0, y=1.1, z=0.9),
    ]
    bufs = {
        0: np.ones(frames) * 0.1,
        1: np.ones(frames) * 0.1,
        2: np.ones(frames) * 0.1,
    }
    wall = {
        "north": np.ones(frames) * 0.05,
        "south": np.ones(frames) * 0.05,
        "east": np.ones(frames) * 0.05,
        "west": np.ones(frames) * 0.05,
        "roof": np.ones(frames) * 0.02,
    }
    block = mix_to_surround(
        frames=frames,
        n_ch=6,
        speaker_bufs=bufs,
        speakers=n_spk,
        wall_bufs=wall,
        room_width=5.0,
        room_depth=4.0,
    )
    ok_shape = block.shape == (frames, 6)
    ch_rms = np.sqrt(np.mean(block * block, axis=0))
    # Every seat except maybe LFE has some rain
    seated = [0, 1, 2, 4, 5]
    ok_wrap = all(ch_rms[i] > 1e-4 for i in seated)
    return _ok(
        "5.1 mix wraps FL/FR/C/RL/RR",
        ok_front and ok_east and ok_walls and ok_shape and ok_wrap,
        f"front_ok={ok_front} east_sr={g_r[7]:.2f} rms={np.round(ch_rms, 4).tolist()}",
    )


def test_speaker_test_pan() -> bool:
    from app.models.room import default_house
    from app.audio.surround import peer_pan_lr, speaker_test_channel_gains

    room = default_house()
    # Three speakers on ONE shared device, west → east
    room.speakers[0].x, room.speakers[0].z = 0.4, 2.0
    room.speakers[1].x, room.speakers[1].z = 2.5, 2.0
    room.speakers[2].x, room.speakers[2].z = 4.6, 2.0
    for s in room.speakers:
        s.audio_device = 7
        s.enabled = True
    left = speaker_test_channel_gains(room.speakers[0], room, n_ch=2)
    mid = speaker_test_channel_gains(room.speakers[1], room, n_ch=2)
    right = speaker_test_channel_gains(room.speakers[2], room, n_ch=2)
    pan_l = peer_pan_lr(room.speakers[0], room.speakers, room)
    pan_r = peer_pan_lr(room.speakers[2], room.speakers, room)
    ok = (
        pan_l < -0.45
        and pan_r > 0.45
        and float(left[0]) > float(left[1]) * 1.4
        and float(right[1]) > float(right[0]) * 1.4
        and abs(float(mid[0]) - float(mid[1])) < 0.35
    )
    return _ok(
        "shared-device test tone pans by speaker location",
        ok,
        f"pan_L={pan_l:.2f} pan_R={pan_r:.2f} "
        f"gL={np.round(left, 3).tolist()} gR={np.round(right, 3).tolist()}",
    )


def test_drizzle_is_audible() -> bool:
    """q=0 silent; drizzle has a bed; level rises toward downpour (my_house)."""
    from pathlib import Path

    from app.audio.spatial_engine import SpatialRainEngine
    from app.utils.persistence import load_room

    p = Path("configs/my_house.json")
    if not p.is_file():
        from app.models.room import default_house
        room = default_house()
    else:
        room = load_room(str(p))
    room.mix_wash = 0.05
    room.mix_droplets = 2.0
    room.rain_intensity = 0.6
    eng = SpatialRainEngine(room, samplerate=48000, blocksize=2048)
    eng.use_drop_bank = True
    eng._drop_bank.ensure_built()

    def rms_at(q: float, seed: int = 7, seconds: float = 2.0) -> float:
        room.droplet_density = q
        eng._reset_sim(seed)
        frames = int(eng.blocksize)
        n = max(1, int(seconds * eng.samplerate / frames))
        parts = []
        for _ in range(n):
            mix = eng._advance(frames)
            parts.append(np.asarray(mix.get(0, np.zeros(frames)), dtype=np.float64))
        x = np.concatenate(parts)
        return float(np.sqrt(np.mean(x * x)) + 1e-12)

    r0 = rms_at(0.0)
    r_driz = rms_at(0.08)
    r_mid = rms_at(0.40)
    r_full = rms_at(1.0)
    ok_off = r0 < 1e-6
    ok_driz = r_driz > 0.0024
    ok_up = r_full > r_driz * 1.35 and r_mid > r_driz * 0.95
    return _ok(
        "drizzle is audible; 0% is off; downpour louder",
        ok_off and ok_driz and ok_up,
        f"rms0={r0:.5f} drizzle={r_driz:.5f} mid={r_mid:.5f} full={r_full:.5f}",
    )


def test_tin_roof_is_brighter_than_wood() -> bool:
    from app.audio.engine import synth_drop
    from app.audio.spatial_engine import SpatialRainEngine, _material_surface
    from app.models.room import default_house

    def centroid(x, sr=48000):
        x = np.asarray(x, dtype=np.float64).reshape(-1)
        w = np.hanning(len(x))
        spec = np.abs(np.fft.rfft(x * w))
        f = np.fft.rfftfreq(len(x), 1.0 / sr)
        return float(np.sum(f * spec) / (np.sum(spec) + 1e-12))

    wood = synth_drop(sr=48000, surface="wood", size_mm=3.0, seed=3, sharpness=0.6)
    shingle = synth_drop(sr=48000, surface="shingle", size_mm=3.0, seed=3, sharpness=0.6)
    metal = synth_drop(sr=48000, surface="metal", size_mm=3.0, seed=3, sharpness=0.6)
    cw, cs, cm = centroid(wood), centroid(shingle), centroid(metal)

    room = default_house()
    room.roof_material = "Tin Roof"
    eng = SpatialRainEngine(room)
    mapped = eng._surface_for_layer("roof")
    mat = _material_surface("Tin Roof")
    shingle_roof = _material_surface("Shingle Roof")

    ok = (
        mapped == "metal"
        and mat == "metal"
        and shingle_roof == "shingle"
        and cm > cw * 1.20
        and cm > cs * 1.15
        and cm < 4800.0
    )
    return _ok(
        "tin roof maps to metal and is spectrally sharper than wood/shingle",
        ok,
        f"cent_wood={cw:.0f} shingle={cs:.0f} metal={cm:.0f} map={mapped}/{mat}/{shingle_roof}",
    )


def test_tin_is_wet_metal_not_plastic_or_jewelry() -> bool:
    """Tin = wet water on a sheet. Not a 2–4 kHz ding, not a 9 kHz plastic clack."""
    from app.audio.engine import synth_drop

    sr = 48000

    def _spec(x):
        x = np.asarray(x, dtype=np.float64).reshape(-1)
        n = max(256, len(x))
        if len(x) < n:
            x = np.pad(x, (0, n - len(x)))
        spec = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
        f = np.fft.rfftfreq(len(x), 1.0 / sr)
        return f, spec

    def peakiness(x, lo=1800.0, hi=5200.0):
        f, spec = _spec(x)
        band = spec[(f >= lo) & (f <= hi)]
        if band.size < 8:
            return 999.0
        return float(np.max(band) / (np.median(band) + 1e-20))

    def band_frac(x, lo=None, hi=None):
        f, spec = _spec(x)
        tot = float(np.sum(spec) + 1e-20)
        m = np.ones(spec.shape, dtype=bool)
        if lo is not None:
            m &= f >= lo
        if hi is not None:
            m &= f < hi
        return float(np.sum(spec[m]) / tot)

    def centroid(x):
        f, spec = _spec(x)
        mag = np.sqrt(spec)
        return float(np.sum(f * mag) / (np.sum(mag) + 1e-12))

    metal = synth_drop(sr=sr, surface="metal", size_mm=3.0, seed=3, sharpness=0.6)
    metal_hi = synth_drop(sr=sr, surface="metal", size_mm=3.0, seed=3, sharpness=0.9)
    wood = synth_drop(sr=sr, surface="wood", size_mm=3.0, seed=3, sharpness=0.6)
    cm = centroid(metal)
    cw = centroid(wood)
    pk_m = peakiness(metal)
    body = band_frac(metal, hi=800.0)
    hf2 = band_frac(metal, lo=2000.0)
    hf4 = band_frac(metal, lo=4000.0)
    hf4_hi = band_frac(metal_hi, lo=4000.0)
    dur_ms = 1000.0 * len(metal) / sr

    n12 = int(0.012 * sr)
    n18 = int(0.018 * sr)
    early = metal[:n12]
    late = metal[n18:]
    e_rms = float(np.sqrt(np.mean(early * early)) + 1e-12)
    l_rms = float(np.sqrt(np.mean(late * late)) + 1e-12)
    late_cent = centroid(late) if len(late) > 64 else 0.0

    ok = (
        pk_m < 80.0
        and cm > cw * 1.35
        and cm < 4800.0
        and 0.15 < body < 0.70
        and hf2 > 0.10
        and hf2 < 0.62
        and hf4 < 0.28
        and hf4_hi < 0.40
        and 28.0 < dur_ms < 95.0
        and (l_rms / e_rms) > 0.12
        and late_cent > 700.0
    )
    return _ok(
        "tin is wet metal (not jewelry, not plastic clack)",
        ok,
        f"cent={cm:.0f}/{cw:.0f} peakiness={pk_m:.1f} body<800={body:.2f} "
        f">=2k={hf2:.2f} >=4k={hf4:.2f}/{hf4_hi:.2f} dur={dur_ms:.1f}ms "
        f"tail={l_rms/e_rms:.2f} late_cent={late_cent:.0f}",
    )


def test_glass_is_lighter_than_water() -> bool:
    """Window-pane splatters sit above puddles and below tin — not a heavy thud."""
    from app.audio.engine import synth_drop
    from app.audio.spatial_engine import SpatialRainEngine
    from app.models.room import default_house

    def centroid(x, sr=48000):
        x = np.asarray(x, dtype=np.float64).reshape(-1)
        w = np.hanning(len(x))
        spec = np.abs(np.fft.rfft(x * w))
        f = np.fft.rfftfreq(len(x), 1.0 / sr)
        return float(np.sum(f * spec) / (np.sum(spec) + 1e-12))

    water = synth_drop(sr=48000, surface="water", size_mm=3.0, seed=3, sharpness=0.6)
    glass = synth_drop(sr=48000, surface="glass", size_mm=3.0, seed=3, sharpness=0.6)
    metal = synth_drop(sr=48000, surface="metal", size_mm=3.0, seed=3, sharpness=0.6)
    cw, cg, cm = centroid(water), centroid(glass), centroid(metal)

    room = default_house()
    eng = SpatialRainEngine(room)
    near = eng._surface_for_layer("near")

    ok = (
        cg > cw * 1.35
        and cg < cm * 0.95
        and cg < 4200.0
        and near == "glass"
    )
    return _ok(
        "glass pane is higher than water, lower than tin",
        ok,
        f"cent_water={cw:.0f} glass={cg:.0f} metal={cm:.0f} near={near}",
    )


def test_visual_hit_own_sound() -> bool:
    from app.models.room import default_house
    from app.audio.spatial_engine import SpatialRainEngine

    room = default_house()
    eng = SpatialRainEngine(room, samplerate=48000, blocksize=2048)
    eng.use_drop_bank = True
    eng._drop_bank.ensure_built()
    eng._reset_sim(2)
    n = 8
    for i in range(n):
        eng._spawn_visual_hit((0.4 + 0.4 * i, 0.02, 3.7, 1))
    ok = len(eng._voices) == n
    mix = eng._mix_speakers(2048)
    pk = max(float(np.max(np.abs(mix[i]))) for i in mix if i >= 0)
    return _ok(
        "each visual landing is its own stacked voice",
        ok and pk > 1e-4,
        f"voices={len(eng._voices)} pk={pk:.4f}",
    )


def test_voice_rate_cap() -> bool:
    from app.models.room import default_house
    from app.audio.spatial_engine import SpatialRainEngine, _MAX_VOICE_EVENTS_PER_SEC

    room = default_house()
    room.droplet_density = 1.0
    eng = SpatialRainEngine(room, samplerate=48000, blocksize=512)
    perc = eng._ips()
    voices = eng._voice_ips()
    chain = eng._chain_hits()
    ok = perc >= 100.0 and voices <= _MAX_VOICE_EVENTS_PER_SEC + 1e-6 and chain in (1, 2)
    return _ok(
        "downpour: many separate ticks (not a noise bed)",
        ok,
        f"perceived={perc:.1f}/s voices={voices:.1f}/s chain={chain}",
    )


def test_downpour_visual_stacks_ticks() -> bool:
    """Full quantity: many roof landings stay as stacked ticks, not a fused bed."""
    from app.models.room import default_house
    from app.audio.spatial_engine import SpatialRainEngine

    room = default_house()
    room.roof_material = "Tin Roof"
    room.droplet_density = 1.0
    eng = SpatialRainEngine(room, samplerate=48000, blocksize=2048)
    eng.use_drop_bank = True
    eng._drop_bank.ensure_built()
    eng._reset_sim(4)
    n = 120
    for i in range(n):
        eng._spawn_visual_hit((0.4 + (i % 10) * 0.4, 2.62, 0.4 + (i // 10) * 0.3, 3))
    voices = len(eng._voices)
    mix = eng._mix_speakers(2048)
    x = np.asarray(mix.get(0, np.zeros(2048)), dtype=np.float64)
    pk = float(np.max(np.abs(x)) + 1e-12)
    rms = float(np.sqrt(np.mean(x * x)) + 1e-12)
    crest = 20.0 * math.log10(pk / rms)
    ok = voices >= 100 and crest > 8.0
    return _ok(
        "downpour visual hits stack as ticks (high crest, not hiss)",
        ok,
        f"voices={voices} crest={crest:.1f}dB pk={pk:.3f} rms={rms:.4f}",
    )


def _binaural_block(eng, room, q: float, seconds: float = 3.0, seed: int = 42) -> np.ndarray:
    room.droplet_density = q
    eng._reset_sim(seed)
    frames = int(eng.blocksize)
    parts = []
    for _ in range(max(1, int(seconds * eng.samplerate / frames))):
        eng._advance(frames)
        parts.append(eng._last_binaural.mean(axis=1))
    return np.concatenate(parts)[eng.samplerate // 2 :]


def test_quantity_keeps_rising() -> bool:
    """Every slider step gets louder — no plateau past ~35% (old AGC bug)."""
    from pathlib import Path

    from app.audio.spatial_engine import SpatialRainEngine
    from app.models.room import default_house
    from app.utils.persistence import load_room

    p = Path("configs/my_house.json")
    room = load_room(str(p)) if p.is_file() else default_house()
    eng = SpatialRainEngine(room, samplerate=48000, blocksize=2048)
    eng._drop_bank.ensure_built()
    qs = (0.05, 0.35, 0.6, 0.8, 1.0)
    lv = []
    for q in qs:
        x = _binaural_block(eng, room, q)
        lv.append(20.0 * math.log10(float(np.sqrt(np.mean(x * x))) + 1e-12))
    steps = [b - a for a, b in zip(lv, lv[1:])]
    ok = all(s >= 1.5 for s in steps) and (lv[-1] - lv[0]) >= 15.0
    return _ok(
        "quantity keeps getting louder to 100% (no plateau)",
        ok,
        "dB=" + "/".join(f"{v:.1f}" for v in lv) + " steps=" + "/".join(f"{s:.1f}" for s in steps),
    )


def test_downpour_sheet_not_hiss() -> bool:
    """100%: a roar of the same grains with the big drops still on top —
    the envelope moves and peaks stand out, it isn't flat noise."""
    from app.audio.spatial_engine import SpatialRainEngine
    from app.models.room import default_house

    room = default_house()
    room.roof_material = "Tin Roof"
    eng = SpatialRainEngine(room, samplerate=48000, blocksize=2048)
    eng._drop_bank.ensure_built()
    x = _binaural_block(eng, room, 1.0)
    win = 480
    env = np.sqrt(np.mean(x[: len(x) // win * win].reshape(-1, win) ** 2, axis=1)) + 1e-12
    spread = 20.0 * math.log10(float(np.percentile(env, 99) / np.percentile(env, 50)))
    crest = 20.0 * math.log10(float(np.max(np.abs(x)) / np.sqrt(np.mean(x * x))))
    _n, _dist, split = eng._rain_state()
    ok = spread >= 2.0 and crest >= 10.0 and split > 3.0
    return _ok(
        "downpour roars with big drops on top (not flat hiss)",
        ok,
        f"env p99/p50={spread:.1f}dB crest={crest:.1f}dB split={split:.2f}mm",
    )


def _peak_prominence_db(x: np.ndarray, sr: int, lo: float = 150.0, hi: float = 5000.0) -> float:
    """Tallest 1/12-octave band above its ±1/4-octave neighbourhood."""
    n = 8192
    seg = x[: len(x) // n * n].reshape(-1, n) * np.hanning(n)
    P = np.mean(np.abs(np.fft.rfft(seg, axis=1)) ** 2, axis=0)
    f = np.fft.rfftfreq(n, 1.0 / sr)
    cents = lo * 2.0 ** np.arange(0.0, math.log2(hi / lo), 1.0 / 12.0)
    band = np.array([P[(f >= c / 2 ** (1 / 24)) & (f < c * 2 ** (1 / 24))].mean() for c in cents])
    band = 10.0 * np.log10(band + 1e-30)
    prom = band - np.convolve(band, np.ones(7) / 7.0, mode="same")
    return float(np.max(prom[3:-3]))


def test_downpour_no_fixed_resonance() -> bool:
    """A downpour sheet must not ring at fixed pitches (the "hollow bucket").

    Thousands of copies/s of one grain reproduce that grain's noise peaks as
    a steady resonance; grain variants + per-hit pitch spread average them out.
    """
    from app.audio.spatial_engine import SpatialRainEngine
    from app.models.room import default_house

    room = default_house()
    room.roof_material = "Tin Roof"
    eng = SpatialRainEngine(room, samplerate=48000, blocksize=2048)
    eng._drop_bank.ensure_built()
    x = _binaural_block(eng, room, 1.0, seconds=6.0)
    prom = _peak_prominence_db(x, eng.samplerate)
    return _ok(
        "downpour has no fixed resonance (no hollow-bucket ring)",
        prom <= 3.2,
        f"tallest 1/12-oct peak={prom:.1f}dB (single-grain sheet ≈ 4–7 dB)",
    )


def test_visual_hits_not_on_block_grid() -> bool:
    """Landings handed over per audio block must not all start on sample 0.

    That put the loudest drops on a 23 Hz grid — a drum roll on a bucket.
    """
    from app.audio.spatial_engine import SpatialRainEngine
    from app.models.room import default_house

    room = default_house()
    room.roof_material = "Tin Roof"
    room.droplet_density = 0.15
    eng = SpatialRainEngine(room, samplerate=48000, blocksize=2048)
    eng._drop_bank.ensure_built()
    eng.use_dense_texture = False
    eng._reset_sim(7)
    rng = np.random.RandomState(3)
    frames = int(eng.blocksize)
    parts = []
    for _ in range(int(6 * eng.samplerate / frames)):
        hits = [(rng.uniform(0, room.width), room.height + 0.02, rng.uniform(0, room.depth), 3)
                for _ in range(rng.poisson(6))]
        eng.submit_visual_impacts(hits)
        eng._advance(frames)
        parts.append(eng._last_binaural.mean(axis=1))
    m = np.stack(parts[12:])
    prof = np.mean(m.reshape(m.shape[0], 16, frames // 16) ** 2, axis=(0, 2))
    swing = 10.0 * math.log10(float(prof.max() / prof.min()))
    return _ok(
        "visual landings spread across the block (no 23 Hz drum roll)",
        swing <= 3.0,
        f"energy swing across block phase={swing:.1f}dB",
    )


def test_drips_outlast_the_rain() -> bool:
    """Stop the rain: eaves keep dripping, slower and slower; Drips 0 = silent."""
    from app.audio.spatial_engine import SpatialRainEngine
    from app.models.room import default_house

    room = default_house()
    room.roof_material = "Tin Roof"
    eng = SpatialRainEngine(room, samplerate=48000, blocksize=2048)
    eng._drop_bank.ensure_built()
    frames = int(eng.blocksize)
    per_s = eng.samplerate / frames

    def run(mix_drips: float):
        room.mix_drips = mix_drips
        eng._reset_sim(5)
        drips = []
        orig = eng._spawn_drip
        eng._spawn_drip = lambda *a, **k: (drips.append(eng._time), orig(*a, **k))
        parts = []
        try:
            for b in range(int(70 * per_s)):
                room.droplet_density = 0.5 if b < 20 * per_s else 0.0
                eng._advance(frames)
                parts.append(eng._last_binaural.mean(axis=1))
        finally:
            del eng._spawn_drip
        return np.array(drips), np.concatenate(parts)

    t, x = run(1.0)
    sr = eng.samplerate
    early = int(np.sum((t >= 22) & (t < 32)))
    late = int(np.sum((t >= 50) & (t < 60)))
    after = x[22 * sr : 60 * sr]
    lvl = 10.0 * math.log10(float(np.mean(after * after)) + 1e-30)
    rain = x[10 * sr : 20 * sr]
    rain_lvl = 10.0 * math.log10(float(np.mean(rain * rain)) + 1e-30)
    _t0, x0 = run(0.0)
    off = x0[22 * sr : 60 * sr]
    off_lvl = 10.0 * math.log10(float(np.mean(off * off)) + 1e-30)
    ok = early > late > 0 and -90.0 < lvl < rain_lvl - 10.0 and off_lvl < -150.0
    return _ok(
        "drips outlast the rain and fade; Drips 0 = silent",
        ok,
        f"drips 2–12 s after={early} · 30–40 s after={late} · after {lvl:.1f}dB "
        f"(rain {rain_lvl:.1f}dB) · fader 0 {off_lvl:.0f}dB",
    )


def test_drip_spots_on_the_eaves() -> bool:
    """Drip spots ring the house just outside the walls; over a window they hit the sill."""
    from app.audio.runoff import EAVE_OUT_M, RoofRunoff

    ro = RoofRunoff()
    ro.layout(5.0, 4.0, 2.6, (("north", 1.0, 2.2, 0.9),))
    outside = all(
        s.x < 0 or s.x > 5.0 or s.z < 0 or s.z > 4.0 for s in ro.spots
    )
    near = all(
        min(abs(s.x + EAVE_OUT_M), abs(s.x - 5.0 - EAVE_OUT_M), abs(s.z + EAVE_OUT_M), abs(s.z - 4.0 - EAVE_OUT_M)) < 1e-6
        for s in ro.spots
    )
    sill = [s for s in ro.spots if s.surface == "metal"]
    sill_ok = bool(sill) and all(s.wall == "north" and 1.0 <= s.x <= 2.2 and s.land_y == 0.9 for s in sill)
    mean_share = sum(s.share for s in ro.spots) / len(ro.spots)
    ok = outside and near and sill_ok and abs(mean_share - 1.0) < 1e-9 and len(ro.spots) >= 10
    return _ok(
        "drip spots ring the eaves; drips over a window land on its sill",
        ok,
        f"{len(ro.spots)} spots · {len(sill)} on the sill",
    )


def _layer_level(room, layer: str, recv: object = "L") -> float:
    """Full-path gain (dB) from drops on ``layer`` to one receiver."""
    from app.audio.spatial_engine import SpatialRainEngine

    eng = SpatialRainEngine(room, samplerate=48000, blocksize=2048)
    eng._drop_bank.ensure_built()
    room.droplet_density = 0.5
    eng._reset_sim(3)
    cal = eng._measure_layer(layer)
    g = cal.get("full", {}).get(recv, 0.0)
    return 20.0 * math.log10(g + 1e-12)


def test_design_changes_the_sound() -> bool:
    """The house design reaches the sound through physics, in the right direction."""
    from app.models.room import default_house

    def house(roof="Tin Roof", wall="Brick Wall", open_amt=None):
        r = default_house()
        r.roof_material, r.wall_material = roof, wall
        if open_amt is not None:
            for w in r.windows:
                w.open = open_amt
        return r

    tin = _layer_level(house("Tin Roof"), "roof")
    tile = _layer_level(house("Tile Roof"), "roof")
    ground_brick = _layer_level(house(wall="Brick Wall", open_amt=0.0), "ground")
    ground_glass = _layer_level(house(wall="Glass Window", open_amt=0.0), "ground")
    ground_open = _layer_level(house(wall="Brick Wall", open_amt=0.9), "ground")
    ok = (
        tin - tile >= 10.0
        and ground_glass - ground_brick >= 10.0
        and ground_open - ground_brick >= 6.0
    )
    return _ok(
        "design → sound: tin ≫ tile roof; glass walls and open windows let the yard in",
        ok,
        f"roof tin {tin:.1f} / tile {tile:.1f} dB · ground brick {ground_brick:.1f} / "
        f"glass {ground_glass:.1f} / open {ground_open:.1f} dB",
    )


def test_wind_wets_the_windward_wall() -> bool:
    from app.audio import house_model as hm
    from app.models.room import default_house

    room = default_house()
    calm = {p.wall: p.rate_area for p in hm.catchments(room, 0.0, 0.0) if p.layer == "wall"}
    # 0° pushes rain toward +Z: it lands on the south face (outward −Z)
    windy = {p.wall: p.rate_area for p in hm.catchments(room, 0.7, 0.0) if p.layer == "wall"}
    ok = windy["south"] > 10.0 * windy["north"] and windy["south"] > 10.0 * calm["south"]
    return _ok(
        "wind drives rain onto the windward wall only",
        ok,
        f"south calm {calm['south']:.2f} → windy {windy['south']:.2f} m² · north windy {windy['north']:.2f} m²",
    )


def test_view_and_engine_agree() -> bool:
    """The 3D view spawns rain from the same catchments the audio uses, and
    the view-driven and scheduler paths sound equally loud."""
    import random
    import types

    from app.audio import house_model as hm
    from app.audio.spatial_engine import SpatialRainEngine
    from app.graphics import glview as gv
    from app.models.room import default_house

    room = default_house()
    room.roof_material = "Tin Roof"
    room.wall_material = "Glass Window"
    room.droplet_density = 0.5
    np.random.seed(2)
    random.seed(2)
    view = types.SimpleNamespace(room=room)
    spawn = types.MethodType(gv.GLRoomView._spawn_world_rain, view)
    kinds = spawn(6000)[:, 4]
    want = hm.kind_shares(hm.catchments(room))
    worst = max(abs(float(np.mean(kinds == k)) - v) for k, v in want.items())

    eng = SpatialRainEngine(room, samplerate=48000, blocksize=2048)
    eng._drop_bank.ensure_built()
    eng.use_drips = False

    def level(visual: bool) -> float:
        eng._reset_sim(11)
        rng = np.random.RandomState(4)
        frames = int(eng.blocksize)
        parts = []
        for _ in range(int(5 * eng.samplerate / frames)):
            if visual:
                pts = spawn(rng.poisson(1200 * frames / eng.samplerate))
                hits = [(float(x) + room.width / 2, float(ly), float(z) + room.depth / 2, int(k))
                        for x, _y, z, _vy, k, ly in pts]
                eng.submit_visual_impacts(hits)
            mix = eng._advance(frames)
            parts.append(sum(mix.get(i, np.zeros(frames)) ** 2 for i in range(len(room.speakers))))
        x = np.concatenate(parts)[eng.samplerate:]
        return 10.0 * math.log10(float(np.mean(x)) + 1e-30)

    lv, ln = level(True), level(False)
    ok = worst < 0.03 and abs(lv - ln) < 2.0
    return _ok(
        "3D view and audio share one rain model (spawn shares, equal loudness)",
        ok,
        f"worst kind-share error={worst:.3f} · view {lv:.1f} dB vs scheduler {ln:.1f} dB",
    )


_TONE_BANKS: dict = {}


def _tone_bank(tone):
    """Built drop bank per (ring, wet, soft); shared by the tone tests (~1 s each)."""
    from app.audio.drop_bank import DropSampleBank, quantize_tone

    key = quantize_tone(*tone)
    if key not in _TONE_BANKS:
        b = DropSampleBank(samplerate=48000, seed=2026, tone=key)
        b.ensure_built()
        _TONE_BANKS[key] = b
    return _TONE_BANKS[key]


def _bank_singles(bank, surface: str) -> list:
    from app.audio.drop_bank import SHARP_BINS, SIZE_BINS_MM

    return [g for sz in SIZE_BINS_MM for sh in SHARP_BINS for g in bank._grains[(surface, sz, sh, 1)]]


def test_tone_default_bank_is_unchanged() -> bool:
    """Default Ring/Wetness/Softness must bake exactly the bank we always had."""
    from app.audio.drop_bank import TONE_DEFAULT, DropSampleBank
    from app.audio.spatial_engine import SpatialRainEngine
    from app.models.room import default_house

    class _LegacyBank(DropSampleBank):
        # The fixed per-surface params the bank used before the sliders were wired
        def _surface_kw(self, surface, sharpness):
            if surface == "metal":
                return {"tone_ring": 0.0, "tone_soft": 0.28, "tone_wet": 0.62}
            if surface == "glass":
                return {"tone_ring": 0.06, "tone_soft": 0.22, "tone_wet": 0.52}
            return {}

    legacy = _LegacyBank(samplerate=48000, seed=2026)
    legacy.ensure_built()
    new = _tone_bank(TONE_DEFAULT)
    same_keys = set(legacy._grains) == set(new._grains)
    diff = [k for k in legacy._grains
            if not all(np.array_equal(a, b) for a, b in zip(legacy._grains[k], new._grains.get(k, [])))]
    eng = SpatialRainEngine(default_house())
    ok = same_keys and not diff and eng._drop_bank.tone == TONE_DEFAULT
    return _ok(
        "default tone bakes the same grains as the old fixed bank",
        ok,
        f"keys={len(new._grains)} differing={len(diff)} engine tone={eng._drop_bank.tone} "
        f"build={new.build_s:.2f}s",
    )


def test_tone_sliders_change_the_grains() -> bool:
    """Ring / Wetness / Softness move glass and metal grains the way they say."""
    from app.audio.drop_bank import TONE_DEFAULT
    from app.audio.engine import synth_drop

    sr = 48000
    r0, w0, s0 = TONE_DEFAULT

    def tail_ms(g):
        # Energy time-centroid: a wetter hit keeps sounding longer
        e = np.asarray(g) ** 2
        return 1000.0 * float(np.sum(np.arange(len(e)) * e) / (np.sum(e) * sr))

    def rise_ms(g):
        env = np.sqrt(np.convolve(g * g, np.ones(24) / 24.0, mode="same"))
        pk = float(env.max())
        return 1000.0 * (int(np.argmax(env > 0.9 * pk)) - int(np.argmax(env > 0.1 * pk))) / sr

    def centroid(g):
        x = np.pad(g, (0, max(0, 4096 - len(g))))[:4096]
        mag = np.abs(np.fft.rfft(x))
        return float(np.sum(np.fft.rfftfreq(4096, 1.0 / sr) * mag) / np.sum(mag))

    def peakiness(g, lo=250.0, hi=4000.0):
        # Narrow (±3%) over wide (±25%) band energy on a 1/48-oct grid, geo-mean'd:
        # resonances lift it, the broad spectral tilt doesn't.
        n = 16384
        x = np.pad(g, (0, max(0, n - len(g))))[:n]
        P = np.abs(np.fft.rfft(x)) ** 2
        f = np.fft.rfftfreq(n, 1.0 / sr)
        cs = np.concatenate([[0.0], np.cumsum(P)])
        c = lo * 2.0 ** np.arange(0.0, math.log2(hi / lo), 1.0 / 48.0)

        def band(a, b):
            i0 = np.searchsorted(f, a)
            i1 = np.maximum(np.searchsorted(f, b), i0 + 1)
            return (cs[i1] - cs[i0]) / (i1 - i0)

        r = band(c * 0.97, c * 1.03) / band(c * 0.78, c * 1.28)
        return float(1.0 / np.exp(np.mean(np.log(r + 1e-30))))

    def avg(fn, tone, surf):
        return float(np.mean([fn(g) for g in _bank_singles(_tone_bank(tone), surf)]))

    rows, ok = [], True
    for surf in ("glass", "metal"):
        wet_lo, wet_hi = avg(tail_ms, (r0, 0.3, s0), surf), avg(tail_ms, (r0, 1.0, s0), surf)
        rise_lo, rise_hi = avg(rise_ms, (r0, w0, 0.0), surf), avg(rise_ms, (r0, w0, 1.0), surf)
        c_lo, c_hi = avg(centroid, (r0, w0, 0.0), surf), avg(centroid, (r0, w0, 1.0), surf)
        pk_d, pk_hi = avg(peakiness, TONE_DEFAULT, surf), avg(peakiness, (1.0, w0, s0), surf)
        ok &= wet_hi > wet_lo * 1.08
        ok &= rise_hi > rise_lo * 1.08 and c_hi <= c_lo
        ok &= pk_hi > pk_d * 1.03
        rows.append(
            f"{surf}: tail {wet_lo:.1f}->{wet_hi:.1f}ms rise {rise_lo:.2f}->{rise_hi:.2f}ms "
            f"cent {c_lo:.0f}->{c_hi:.0f} peaky {pk_d:.3f}->{pk_hi:.3f}"
        )
    # Softness also darkens tin (the sheet tick), not only slows it
    m_lo = avg(centroid, (r0, w0, 0.0), "metal")
    m_hi = avg(centroid, (r0, w0, 1.0), "metal")
    ok &= m_hi < m_lo * 0.98

    # Ring on tin stays a low-mid bloom: no 2–4 kHz jewelry ping even at full Ring
    from app.audio.drop_bank import surface_tone_kw

    kw = surface_tone_kw("metal", 0.6, (1.0, w0, s0))
    x = synth_drop(sr=sr, surface="metal", size_mm=3.0, seed=3, sharpness=0.6, **kw)
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    f = np.fft.rfftfreq(len(x), 1.0 / sr)
    band = spec[(f >= 1800.0) & (f <= 5200.0)]
    jewel = float(np.max(band) / (np.median(band) + 1e-20))
    ok &= jewel < 80.0 and kw["tone_ring"] <= 0.08
    rows.append(f"tin full-ring 1.8–5.2k peakiness={jewel:.1f} ring={kw['tone_ring']:.3f}")
    return _ok("tone sliders change the grains (wet tail, soft attack, ring)", bool(ok), " · ".join(rows))


def test_tone_change_rebuilds_off_the_audio_thread() -> bool:
    """A slider move mid-play: no block stalls on the rebuild, swap lands in ~3 s."""
    from app.audio.drop_bank import quantize_tone
    from app.audio.spatial_engine import SpatialRainEngine
    from app.models.room import default_house

    room = default_house()
    room.roof_material = "Tin Roof"
    room.droplet_density = 0.5
    eng = SpatialRainEngine(room, samplerate=48000, blocksize=2048)
    eng._drop_bank.ensure_built()
    eng._reset_sim(5)
    frames = int(eng.blocksize)
    period = frames / float(eng.samplerate)
    gen0 = eng._drop_bank.generation

    def run(n_blocks, on_block=None):
        times = []
        t_next = time.perf_counter()
        for i in range(n_blocks):
            if on_block:
                on_block(i)
            t = time.perf_counter()
            eng._advance(frames)
            times.append(time.perf_counter() - t)
            # Pace like the real mixer so the worker thread gets wall time
            t_next += period
            time.sleep(max(0.0, t_next - time.perf_counter()))
        return times

    run(12)  # warm-up: kernels, path calibration
    base = run(24)
    target = quantize_tone(0.75, 1.0, 0.2)
    t_change = None
    t_swap = None

    def drag(i):
        nonlocal t_change, t_swap
        # A short drag across a few blocks, ending on the target
        steps = [(0.45, 0.9, 0.5), (0.6, 0.95, 0.35), target]
        if i < len(steps):
            room.tone_ring, room.tone_wet, room.tone_soft = steps[i]
            if i == len(steps) - 1:
                t_change = eng._time
        if t_swap is None and eng._drop_bank.tone == target:
            t_swap = eng._time

    during = run(int(4.0 / period), drag)
    eng._bank_switch.join(5.0)
    swapped = eng._drop_bank.tone == target and eng._drop_bank.generation != gen0
    kernels_new = eng._dense_kernel_id is not None and eng._dense_kernel_id[-1] == eng._drop_bank.generation
    # One stray OS hiccup is tolerated (the mixer keeps ~170 ms queued), but
    # never a long one: a rebuild ON the audio thread stalls a block ~2 s.
    worst2 = sorted(during)[-2]
    extra_ms = 1000.0 * (worst2 - max(base))
    delay = (t_swap - t_change) if (t_swap is not None and t_change is not None) else float("inf")
    ok = swapped and kernels_new and extra_ms <= 20.0 and max(during) <= 0.150 and delay <= 3.0
    return _ok(
        "tone change rebuilds the bank off the audio thread",
        ok,
        f"block max {1000 * max(base):.1f} -> {1000 * worst2:.1f} ms (+{extra_ms:.1f}, worst {1000 * max(during):.1f}) "
        f"swap after {delay:.2f}s sim · build {eng._bank_switch.last_build_s:.2f}s · "
        f"kernels rebuilt={kernels_new}",
    )


def test_tone_follows_room_before_play() -> bool:
    """Sliders moved while stopped (or a loaded house) must pick the right bank
    at Play, and a build that throws must not restart every block."""
    from app.audio import drop_bank as DB
    from app.audio.spatial_engine import SpatialRainEngine
    from app.models.room import default_house

    eng = SpatialRainEngine(default_house())
    r2 = default_house()
    r2.tone_wet = 0.2
    eng.room = r2
    eng._bank_for_start()
    want = DB.quantize_tone(r2.tone_ring, r2.tone_wet, r2.tone_soft)
    follows = eng._drop_bank.tone == want

    calls = [0]
    orig = DB.DropSampleBank.ensure_built

    def boom(self, progress_cb=None):
        calls[0] += 1
        raise RuntimeError("synthetic build failure")

    DB.DropSampleBank.ensure_built = boom
    try:
        sw = DB.ToneBankSwitcher(samplerate=48000, seed=2026)
        cur = DB.DropSampleBank(samplerate=48000, seed=2026)
        tone = (0.9, 0.9, 0.9)
        logger = DB.log
        was = logger.disabled
        logger.disabled = True  # the expected traceback is noise here
        try:
            sw.poll(tone, 0.0, cur)
            sw.poll(tone, 1.0, cur)
            sw.join(5.0)
            for t in (1.1, 1.2, 5.0, 20.0):
                sw.poll(tone, t, cur)
                sw.join(5.0)
            held = calls[0] == 1
            sw.poll(tone, 1.0 + sw.RETRY_S + 1.0, cur)
            sw.join(5.0)
            retried = calls[0] == 2
        finally:
            logger.disabled = was
    finally:
        DB.DropSampleBank.ensure_built = orig
    ok = follows and held and retried
    return _ok(
        "tone bank follows the room at Play; failed builds back off",
        ok,
        f"bank tone {eng._drop_bank.tone} want {want} · build attempts {calls[0]} "
        f"(held={held}, retried={retried})",
    )


def _speaker_ear_db(room, secs: float = 6.0):
    """Per-speaker and per-ear level (dB) of a short render, first second dropped
    (5 s measured: 2 s of sparse grains swings the ear level by ±1 dB)."""
    from app.audio.spatial_engine import SpatialRainEngine

    eng = SpatialRainEngine(room, samplerate=48000, blocksize=2048)
    eng._drop_bank.ensure_built()
    eng._reset_sim(3)
    frames = int(eng.blocksize)
    spk, ears = [], []
    for _ in range(int(secs * eng.samplerate / frames)):
        mix = eng._advance(frames)
        spk.append(np.stack([mix.get(i, np.zeros(frames)) for i in range(len(room.speakers))]))
        b = eng._last_binaural
        ears.append(b if b is not None and len(b) == frames else np.zeros((frames, 2)))
    s_ = np.concatenate(spk, axis=1)[:, eng.samplerate:]
    e_ = np.concatenate(ears)[eng.samplerate:]
    db = lambda x: 10.0 * math.log10(float(np.mean(x * x)) + 1e-30)
    return [db(s_[i]) for i in range(len(s_))], (db(e_[:, 0]), db(e_[:, 1]))


def test_windows_let_the_yard_in() -> bool:
    """Opening a window brings the yard in at the speakers/ears near it, and
    moving the window moves where it comes in — even under a tin roof."""
    from app.models.room import default_house

    def house(open_amt, offset=None):
        r = default_house()
        r.roof_material, r.wall_material = "Tin Roof", "Brick Wall"
        r.droplet_density = 0.5
        for w in r.windows:
            w.open = 0.0
        w = next(w for w in r.windows if str(w.wall).lower() in ("north", "south"))
        w.open = open_amt
        if offset is not None:
            w.offset = w.x = offset
        return r, w

    shut, w = house(0.0)
    near = min(range(len(shut.speakers)), key=lambda i: math.dist(
        (shut.speakers[i].x, shut.speakers[i].z), shut.window_center(w)[::2]))
    s0, e0 = _speaker_ear_db(shut)
    s1, e1 = _speaker_ear_db(house(1.0)[0])
    lift = s1[near] - s0[near]
    ear_lift = max(e1) - max(e0)
    # Slide the open window to the far end of its wall: the nearest speaker changes
    far_r, far_w = house(1.0, max(0.0, float(shut.width) - float(w.width) - 0.05))
    s2, _e2 = _speaker_ear_db(far_r)
    moved = (s1[near] - s2[near]) >= 2.0
    ok = lift >= 4.0 and ear_lift >= 2.0 and moved
    return _ok(
        "open windows let the yard in where they are (tin roof)",
        ok,
        f"speaker {near + 1}: {s0[near]:.1f} → {s1[near]:.1f} dB open, {s2[near]:.1f} dB window moved away · "
        f"ears {max(e0):.1f} → {max(e1):.1f} dB",
    )


def _view_driven_level(room, visual: bool, secs: float = 4.0, seed: int = 11):
    """Rain heard with the GL view's real particle sim landing drops (visual)
    or with the scheduler alone. Returns (speakers dB, ears dB, buses dB)."""
    import random
    import types

    from app.audio.spatial_engine import SpatialRainEngine
    from app.graphics import glview as gv

    eng = SpatialRainEngine(room, samplerate=48000, blocksize=2048)
    eng._drop_bank.ensure_built()
    eng.use_weather_sounds = False
    eng._reset_sim(seed)
    np.random.seed(3)
    random.seed(3)
    view = None
    if visual:
        view = types.SimpleNamespace(room=room, _max_particles=2400, _splashes=[],
                                     _rain=np.zeros((0, 6), dtype=np.float32),
                                     _rain_verts=np.zeros((0, 3), dtype=np.float32),
                                     _on_impacts=eng.submit_visual_impacts,
                                     isVisible=lambda: True, update=lambda: None)
        for name in ("_visual_target", "_spawn_world_rain", "_init_particles", "_tick", "_wind_push"):
            setattr(view, name, types.MethodType(getattr(gv.GLRoomView, name), view))
    frames = int(eng.blocksize)
    spk, ears, bus = [], [], []
    tick_t = 0.0
    for b in range(int(secs * eng.samplerate / frames)):
        if view is not None:
            t_end = (b + 1) * frames / eng.samplerate
            while tick_t < t_end:  # the view ticks every 33 ms
                view._tick()
                tick_t += 0.033
            eng._visual_seen_t = time.perf_counter()
        mix = eng._advance(frames)
        spk.append(sum(mix.get(i, np.zeros(frames)) ** 2 for i in range(len(room.speakers))))
        bus.append(sum(v ** 2 for v in eng._last_wall.values()) if eng._last_wall else np.zeros(frames))
        ears.append((eng._last_binaural ** 2).sum(1) / 2)
    sk = int(1.5 * eng.samplerate / frames)
    db = lambda parts: 10.0 * math.log10(float(np.mean(np.concatenate(parts[sk:]))) + 1e-30)
    return db(spk), db(ears), db(bus)


def test_view_and_scheduler_sound_the_same() -> bool:
    """With the 3D view landing the drops or without it, the rain is equally
    loud on the speakers, the ears and the wall/roof buses — light and heavy,
    calm and windy (the view's own particle sim, at its own tick)."""
    from app.models.room import default_house

    worst = 0.0
    rows = []
    for q, wind in ((0.15, 0.0), (0.9, 0.0), (0.55, 0.6)):
        room = default_house()
        room.roof_material = "Tin Roof"
        room.droplet_density = q
        room.wind_speed = wind
        v = _view_driven_level(room, True)
        n = _view_driven_level(room, False)
        d = [a - b for a, b in zip(v, n)]
        worst = max(worst, max(abs(x) for x in d))
        rows.append(f"q{q} w{wind}: spk {d[0]:+.1f} ears {d[1]:+.1f} bus {d[2]:+.1f}")
    return _ok("3D view and scheduler sound the same (amount, wind, ears, buses)", worst < 1.5,
               " · ".join(rows) + f" (worst {worst:.1f} dB)")


def _a_weight(x: np.ndarray, sr: int = 48000) -> np.ndarray:
    """A-weighting (how loud it sounds, not how much energy): deep rumble
    counts far less than the hiss and whistle of wind."""
    from scipy import signal as sps

    f1, f2, f3, f4 = 20.598997, 107.65265, 737.86223, 12194.217
    z = [0, 0, 0, 0]
    p = [-2 * math.pi * f4, -2 * math.pi * f4, -2 * math.pi * f1, -2 * math.pi * f1,
         -2 * math.pi * f2, -2 * math.pi * f3]
    k = (2 * math.pi * f4) ** 2 * 10 ** (1.9997 / 20)
    zd, pd, kd = sps.bilinear_zpk(z, p, k, sr)
    return sps.sosfilt(sps.zpk2sos(zd, pd, kd), x)


def test_wind_through_the_house() -> bool:
    """Wind is heard through the openings: louder with the windward window
    open than shut, louder as it blows harder, quieter behind thick walls,
    and a window open a crack whistles (a narrow peak) where a wide one rushes."""
    import copy

    from app.audio.outdoor_router import apertures
    from app.audio.spatial_engine import SpatialRainEngine
    from app.audio.wind import WindSound
    from app.models.room import Window, default_house

    def level(room, secs=5.0):
        eng = SpatialRainEngine(room, samplerate=48000, blocksize=2048)
        eng._reset_sim(5)
        frames = 2048
        acc = []
        for _ in range(int(secs * 48000 / frames)):
            eng._advance(frames)
            acc.append(eng._last_binaural.copy())
        x = _a_weight(np.concatenate(acc)[48000:].mean(1))
        return 10.0 * math.log10(float(np.mean(x * x)) + 1e-30), x

    base = default_house()
    base.droplet_density = 0.0
    base.wall_material, base.roof_material = "Wood", "Tile Roof"
    base.windows = [Window("W", wall="west", offset=1.5, width=1.2, height=1.2, sill=0.9, open=0.0,
                           open_style="slider")]
    base.wind_direction_deg = 90.0  # blowing east: comes from the west, onto the west window
    base.wind_speed = 0.6
    shut, _ = level(base)
    op = copy.deepcopy(base)
    op.windows[0].open = 0.8
    opened, wide = level(op)
    soft = copy.deepcopy(op)
    soft.wind_speed = 0.25
    breeze, _ = level(soft)
    thick = copy.deepcopy(base)
    thick.wall_thickness = 0.6
    thick.roof_thickness = 0.4
    muffled, _ = level(thick)
    # A crack sings: compare peakiness of the whistle vs the rush
    crack = copy.deepcopy(base)
    crack.windows[0].open = 0.04
    crack.wind_speed = 0.8
    aps = [a for a in apertures(crack) if a.key[0] == "win"]
    ws = WindSound(48000)
    _r, _o, sing = ws.render(np.full(48000, 0.8), [(aps[0].key, aps[0].gap_m, aps[0].open_amt, 1.0)])
    sig = sing.get(aps[0].key, np.zeros(48000))
    spec = np.abs(np.fft.rfft(sig * np.hanning(len(sig))))
    peaky = float(np.max(spec) / (np.median(spec) + 1e-12))
    ok = (opened - shut > 6.0 and opened - breeze > 8.0 and shut - muffled > 6.0 and peaky > 30.0)
    return _ok("wind: open window louder, gale louder, thick walls muffle, a crack whistles", ok,
               f"shut {shut:.1f} / open {opened:.1f} / breeze {breeze:.1f} / thick shut {muffled:.1f} dB · "
               f"whistle peak/median {peaky:.0f}")


def test_house_wind() -> bool:
    """Drafts need a way out (one window alone barely breathes, opposite
    windows draw); the windward window is the inlet; light panels buffet
    more than heavy ones; and it all reaches the ears."""
    import copy

    from app.audio import house_wind as hw
    from app.audio import outdoor_router as orr
    from app.models.room import Window, default_house

    base = default_house()
    base.wall_material, base.roof_material = "Wood", "Tin Roof"
    base.windows = [
        Window("W", wall="west", offset=1.5, width=1.2, height=1.2, sill=0.9, open=1.0, open_style="slider"),
        Window("E", wall="east", offset=1.5, width=1.2, height=1.2, sill=0.9, open=0.0, open_style="slider"),
    ]
    alone = {v.key: v for v in hw.ventilation(base, 270.0)[0]}   # wind from the west
    cross = copy.deepcopy(base)
    cross.windows[1].open = 1.0
    through = {v.key: v for v in hw.ventilation(cross, 270.0)[0]}
    w_alone, w_cross = alone[("win", 0)].speed, through[("win", 0)].speed
    inlet = through[("win", 0)].inflow and not through[("win", 1)].inflow
    brick = copy.deepcopy(base)
    brick.wall_material, brick.roof_material = "Brick Wall", "Tile Roof"

    def roof_gain(r):
        return next(hw.panel_buffet_gain(r, a) for a in orr.apertures(r) if a.key == ("roof",))

    g_tin, g_tile = roof_gain(base), roof_gain(brick)
    eng_ok = True
    try:
        from app.audio.spatial_engine import SpatialRainEngine

        r = copy.deepcopy(cross)
        r.droplet_density, r.wind_speed, r.wind_direction_deg = 0.0, 0.9, 90.0
        eng = SpatialRainEngine(r, samplerate=48000, blocksize=2048)
        eng._reset_sim(5)
        for _ in range(12):
            eng._advance(2048)
        house = eng._house_wind.render(r, orr.apertures(r), None, np.full(2048, 0.9), 0.9, 270.0)
        eng_ok = ("win", 0) in house and ("roof",) in house and float(np.std(house[("win", 0)])) > 0.0
    except Exception as e:  # pragma: no cover
        print("    house wind render failed:", e)
        eng_ok = False
    ok = w_cross > 3.0 * w_alone and inlet and g_tin > 10.0 * g_tile and eng_ok
    return _ok("house wind: drafts need a way out, windward inlet, light panels buffet", ok,
               f"west window air speed alone {w_alone:.2f}·U / with east open {w_cross:.2f}·U · inlet={inlet} · "
               f"roof buffet tin {g_tin:.2f} vs tile {g_tile:.3f} · render={eng_ok}")


def test_thunder() -> bool:
    """Strikes: sound arrives distance/343 s after the flash; near ones are
    louder and brighter than far ones; shut windows take the crack off (A-
    weighted level drops) while the deep rumble still comes through walls and
    roof; How often sets the rate."""
    import copy

    from app.audio import thunder as th
    from app.audio.spatial_engine import SpatialRainEngine
    from app.models.room import default_house

    def strike_level(km, shut=False):
        room = default_house()
        room.droplet_density = 0.0
        if shut:
            for w in room.windows:
                w.open = 0.0
        eng = SpatialRainEngine(room, samplerate=48000, blocksize=2048)
        eng._reset_sim(3)
        s = th.Strike(0.2, km * 1000.0, 45.0, 10.0, 5)
        eng._storm._render(s)
        s.sig = th.tame(s.sig, 48000)
        eng._storm.strikes.append(s)
        frames = 2048
        acc = []
        for _ in range(int((km * 1000.0 / 343.0 + 7.0) * 48000 / frames)):
            eng._advance(frames)
            acc.append(eng._last_binaural.mean(1))
        x = np.concatenate(acc)
        env = np.sqrt(np.convolve(x * x, np.ones(4800) / 4800, "same"))
        onset = int(np.argmax(env > env.max() * 0.05)) / 48000.0
        spec = np.abs(np.fft.rfft(x)) ** 2
        f = np.fft.rfftfreq(len(x), 1 / 48000)
        cent = float((spec * f).sum() / (spec.sum() + 1e-30))
        a = _a_weight(x)
        env_a = np.sqrt(np.convolve(a * a, np.ones(4800) / 4800, "same"))
        return 20 * math.log10(env.max() + 1e-12), onset - 0.2, cent, 20 * math.log10(env_a.max() + 1e-12)

    near, t_near, c_near, near_a = strike_level(0.5)
    far, t_far, c_far, _fa = strike_level(6.0)
    near_shut, _t, c_shut, shut_a = strike_level(0.5, shut=True)
    eng = SpatialRainEngine(default_house(), samplerate=48000, blocksize=2048)
    st = eng._storm
    st.render_sync = False
    count = 0
    for k in range(int(600 / 0.5)):
        st._schedule(k * 0.5, 0.5, 0.45, None)
    count = len(st.strikes)
    per_min = count / 10.0
    want = th.rate_per_min(0.5)
    ok = (abs(t_near - 500 / 343.0) < 0.35 and abs(t_far - 6000 / 343.0) < 0.6
          and near - far > 8.0 and c_near > 1.5 * c_far
          and 3.0 < near_a - shut_a < 25.0 and c_shut < c_near
          and 0.5 * want < per_min < 1.6 * want)
    return _ok("thunder: delay by distance, near louder/brighter, walls let the rumble in, rate", ok,
               f"0.5 km at {t_near:.2f}s {near:.1f} dB {c_near:.0f} Hz · 6 km at {t_far:.1f}s {far:.1f} dB {c_far:.0f} Hz · "
               f"shut {near_shut:.1f} dB {c_shut:.0f} Hz (A-weighted {near_a:.1f} → {shut_a:.1f}) · "
               f"{per_min:.1f}/min (want {want:.1f})")


def test_facing_turns_the_sound() -> bool:
    """Yaw is the way You face (0 = north, 90 = east): facing east, a source
    to the north is on the LEFT and one to the east is straight ahead."""
    import math
    from app.audio.spatial import azimuth_elevation

    o, y = (0.0, 0.0, 0.0), math.radians(90)
    north = math.degrees(azimuth_elevation(o, (0.0, 0.0, 1.0), y)[0])
    east = math.degrees(azimuth_elevation(o, (1.0, 0.0, 0.0), y)[0])
    front0 = math.degrees(azimuth_elevation(o, (1.0, 0.0, 0.0), 0.0)[0])
    return _ok("facing east puts north on the left", abs(north + 90) < 1e-6
               and abs(east) < 1e-6 and abs(front0 - 90) < 1e-6,
               f"north {north:.0f}° east {east:.0f}° (yaw 0: east {front0:.0f}°)")


def test_wall_thickness() -> bool:
    """Thicker walls / roof block more and sound duller, enough to mute the
    rain when really thick; empty (0) keeps each material's usual build."""
    from app.audio import house_model as hm
    from app.audio import yard
    from app.models.room import default_house

    r = default_house()
    r.wall_material, r.roof_material = "Wood", "Tin Roof"
    same = (abs(hm.wall_material(r).airborne - hm.material("wood").airborne) < 1e-12
            and abs(hm.roof_material(r).impact - hm.material("tin").impact) < 1e-12)
    usual_w, usual_r = hm.wall_material(r), hm.roof_material(r)
    r.wall_thickness = 2.0 * hm.typical_wall_thickness(r)
    double_db = 20.0 * math.log10(hm.wall_material(r).airborne / usual_w.airborne)
    duller = hm.wall_material(r).lp_hz < usual_w.lp_hz
    r.wall_thickness, r.roof_thickness = 1.0, 0.30
    mute_db = 20.0 * math.log10(hm.roof_material(r).impact / usual_r.impact)
    wall_mute_db = 20.0 * math.log10(hm.wall_material(r).airborne / usual_w.airborne)
    shallow = yard.yard_view(1.2, (0.05, 1.0, 1.2))
    deep = yard.yard_view(1.2, (0.45, 1.0, 1.2))
    ok = (same and abs(double_db + 9.03) < 0.1 and duller and mute_db < -45.0
          and wall_mute_db < -25.0 and deep < 0.8 * shallow)
    return _ok(
        "wall / roof thickness: blocks and dulls, mutes when really thick",
        ok,
        f"double wall {double_db:.1f} dB · 1 m wood wall {wall_mute_db:.0f} dB · 30 cm tin roof {mute_db:.0f} dB · "
        f"yard seen 45 cm vs 5 cm reveal {deep / shallow:.2f}×",
    )


def main() -> int:
    print("RainRoom3D spatial / rain distribution checks")
    results = [
        test_ring_equal(),
        test_rain_stays_on_the_house(),
        test_wrap_leakage(),
        test_couple_once_all_speakers(),
        test_surround_vbap(),
        test_voice_rate_cap(),
        test_downpour_visual_stacks_ticks(),
        test_visual_hit_own_sound(),
        test_tin_roof_is_brighter_than_wood(),
        test_tin_is_wet_metal_not_plastic_or_jewelry(),
        test_glass_is_lighter_than_water(),
        test_drizzle_is_audible(),
        test_speaker_test_pan(),
        test_quantity_keeps_rising(),
        test_downpour_sheet_not_hiss(),
        test_downpour_no_fixed_resonance(),
        test_visual_hits_not_on_block_grid(),
        test_drips_outlast_the_rain(),
        test_drip_spots_on_the_eaves(),
        test_design_changes_the_sound(),
        test_wind_wets_the_windward_wall(),
        test_view_and_engine_agree(),
        test_windows_let_the_yard_in(),
        test_wall_thickness(),
        test_facing_turns_the_sound(),
        test_view_and_scheduler_sound_the_same(),
        test_wind_through_the_house(),
        test_house_wind(),
        test_thunder(),
        test_tone_default_bank_is_unchanged(),
        test_tone_sliders_change_the_grains(),
        test_tone_change_rebuilds_off_the_audio_thread(),
        test_tone_follows_room_before_play(),
    ]
    passed = sum(1 for r in results if r)
    print(f"\n{passed}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
