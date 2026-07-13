# Rain field samples

WAV loops here are the **main outdoor rain body**. Discrete drops are soft accents only.

| Bundled | Role |
|---------|------|
| `rain_soft_loop.wav` | Light / soft quantity |
| `rain_med_loop.wav` | Medium quantity |
| `rain_heavy_loop.wav` | Heavy quantity |
| `rain_roof_dark.wav` | Darker roof-adjacent wash |

Regenerate procedural bundles with:

```text
python scripts/gen_rain_samples.py
```

| | |
|---|---|
| Formats | `.wav` (16/24/32-bit PCM) |
| Rate | Any (resampled to the engine sample rate, usually 48 kHz) |
| Length | ≥ 0.25 s; longer loops (10–60 s) work best |
| Content | Outdoor rain wash; avoid music or speech |

**Tip:** Drop real field recordings here (Freesound CC0, your own takes). Real recordings beat pure synthesis for realism — the engine blends them through window portals automatically.

If this folder is empty, the engine falls back to a procedural multi-band wash.
