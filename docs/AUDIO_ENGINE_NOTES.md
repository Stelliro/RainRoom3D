# RainRoom3D Audio Engine Rewrite — Design Notes

**Rule carved in stone:** No white noise, pink noise, or any continuous noise beds are used anywhere. Buzz/static must not be able to creep in by accident.

## Synthesis model (event‑only)
Each droplet is an **impact event**. Its sound is the sum of 2‑4 **damped sinusoidal modes** chosen by the impacted material (glass/wood/tile/etc.).
- Amplitude scales with impact energy (size × speed).
- Per‑event phase offsets come from a deterministic hash of the event id. Variation without randomness.
- Distance attenuation is 1/r (clamped) with simple stereo panning + small interaural delay.

## Propagation
- **Walls** act as barriers: if the listener is not line‑of‑sight through a defined window, we apply ~‑18 dB and a gentle low‑pass (~1.4 kHz) to mimic transmission loss.
- **Windows** act as apertures: a light band‑pass gives that “air/edge” coloration when the path goes through an opening.

## Testing
- `python -m app.audio.engine --single-drop` renders `out/single_drop.wav` for deterministic QA.
- `python -m app.audio.engine --render 64` renders a short mix of 64 impacts.

## Integration
- If your `app.models.room` exists, we’ll use its `Room`, `Window`, and `Listener`. If not, the engine ships with tiny fallbacks so tests still run.

## Next steps (optional)
- Replace VBAP‑lite with HRTF convolution.
- Per‑surface impulse responses (no noise; recorded IRs).
- Air absorption above ~10 kHz by distance.
