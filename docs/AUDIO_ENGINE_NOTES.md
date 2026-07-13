# RainRoom3D Audio Engine v3 — Design Notes

## What went wrong (v1–v2 “bleeps and bloops”)

Earlier engines treated each drop as **2–4 pure damped sinusoids** (modal /
Minnaert partials) and made that the loudest layer. High‑Q tones with little
broadband body read as electronic **bleeps**, especially when:

1. Only **one drop** could play at a time in the streamer (voices overwrote).
2. Impact density was too low for ticks to fuse into rain texture.
3. A “no noise beds” rule removed the continuous under-layer real rain has.

## v3.2 synthesis model (current)

**Problem:** Multi-layer drops (slap + splat + splash + spray) + bed ticks +
~200 impacts/s stacked into muddy, broken rain.

**Fix:**

| Layer | Role |
|-------|------|
| Outdoor field (WAV loops preferred) | **Main** continuous wet wash |
| Discrete drops | Sparse single-body brown accents only |

- No multi-tone / multi-layer stack per drop.
- Impact rate capped ~90/s (typically ~30–50 at mid quantity).
- Bundled loops in `assets/audio/rain/` (regenerate with `scripts/gen_rain_samples.py`).
- Drop **real** field recordings into that folder for best realism.

## Mixer

`RainEngine` keeps a deque of concurrent stereo voices (equal-power pan),
Poisson-schedules impacts from `room.rain_intensity` × `droplet_density`,
and exposes `_emit_block(frames)` for `MultiDeviceEngine`.

## Testing

```text
python -m app.audio.engine --single-drop --surface water --size-mm 3.5
python -m app.audio.engine --render 200 --seconds 4 --intensity 0.65 --surface water --out out/rain.wav
python -m app.audio.engine --render 200 --seconds 4 --intensity 0.65 --surface metal --out out/rain_metal.wav
```

## Integration

- Uses `app.models.room.Room` / `Listener` when available.
- Materials map from names (`Tin Roof`, `Glass Window`, …) via substring
  match onto surface profiles: water, glass, metal, wood, tile, shingle, brick.

## Spatial realism layer (v3.2+)

| Module | Role |
|--------|------|
| `hrtf.py` | Parametric HRTF: Woodworth ITD, tilted ILD, pinna notch, rear shadow (not measured SOFA) |
| `reverb.py` | Mild indoor early reflections + short FDN; wetness from room volume & open windows |
| `field_bed.py` | Continuous outdoor field (procedural multi-band) + optional WAV loops in `assets/audio/rain/` |

Outdoor field and discrete drops both couple through **window portals** before
reverb and device routing. Headphones use HRTF at the “You” marker; mapped
speakers get mono mics + shared-device stereo pan when devices coincide.
