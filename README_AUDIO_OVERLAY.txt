RainRoom3D — Audio Engine Overlay (Umbra, 2025‑09‑13)
====================================================
What this is
------------
A clean drop-in replacement for **app/audio/engine.py** that:
- Eliminates buzzing/static by using *event-only* droplet synthesis (no noise beds).
- Adds a **single-droplet** test that renders to `RainRoom3D/out/single_drop.wav`.
- Enforces **wall occlusion** and **window transmission**.
- Uses deterministic modal resonators (no random white/pink noise, by design).

How to install
--------------
1) Close your app.
2) Extract this zip **on top of** your project root so files land under `RainRoom3D/...`.
   There are **no junk files** (no __pycache__, no compiled artifacts).
3) Run one of:
   - Windows (PowerShell): `py -3 -m app.audio.engine --single-drop`
   - Generic Python:       `python -m app.audio.engine --single-drop`

Files included
--------------
- `RainRoom3D/app/audio/engine.py`  ← rewritten engine (no white noise)
- `RainRoom3D/configs/example_room.json`  ← minimal room with a window
- `RainRoom3D/docs/AUDIO_ENGINE_NOTES.md` ← design notes + no-noise policy

Notes
-----
- If `app.models.room` exists, the engine will use it; otherwise it falls back to
  lightweight dataclasses inside `engine.py` so the single-drop test still works.
- The engine writes WAVs directly (no external encoder). Stereo 16‑bit PCM @ 48kHz.

Umbra pledge
------------
No blind moves. No features orphaned. No noise beds. If you need multi‑device or HRTF,
this engine exposes a clean event pipeline so we can extend without regressions.
