# RainRoom3D codebase index

A map of the source: what each part does, and where to look when you change
something. Every module also starts with a docstring explaining itself.

## Entry points

| Command | What it starts |
|---------|----------------|
| `python -m app.main` (or `run.bat`) | The desktop app: `app/main.py` → `StudioWindow` in `app/ui/studio/window.py` |
| `RainRoom3D.exe --rainroom-zone-worker` | A phone room's sound worker. `app/main.py` hands it to `app/remote/worker.py` before Qt loads |
| `python -m app.remote.devserver` | The phone server without the desktop app, used while working on the web app |
| `python -m app.tools.test_*` | The test suites (see [Tests](#tests)) |

## How it fits together

```text
Room (models/room.py)  ← loaded / saved by utils/persistence.py
   │      edited through ui/studio/context.py (StudioContext)
   ▼
SpatialRainEngine (audio/spatial_engine.py)
   ├─ rain_dsd        rain rate → drop sizes and counts
   ├─ house_model     where the rain lands, and how much gets inside, per material
   ├─ drop_bank       pre-rendered drop sounds (synth_drop in engine.py)
   ├─ spatial         each drop → speakers / ears through the window portals
   ├─ rain_texture    the drops too many to place one by one (dense sheet)
   ├─ yard            rain on the ground outside, heard through each opening
   ├─ field_bed/_router  soft outdoor wash, routed through the windows
   ├─ runoff          eave drips and gutters after the rain
   ├─ wind + gusts    the wind outside
   ├─ house_wind      what the wind does to the house: drafts, buffeting, rattles
   ├─ thunder         strikes from storms around the house
   ├─ outdoor_router  wind and thunder → openings and panels → speakers / ears
   ├─ reverb          the room's echo
   └─ hrtf / surround binaural headphones, or multichannel speaker layouts
   ▼
sounddevice output (one stream per device), or PCM for a phone
```

The views (`graphics/`) draw the same `Room`. Their rain comes from the same
catchment areas in `house_model`, and their lightning from the strikes in
`thunder`.

## `app/audio/` — the sound

| Module | Role |
|--------|------|
| `spatial_engine.py` | The engine: simulation clock, mixing stages (`_mix_dense`, `_mix_outdoor_field`, `_mix_weather`…), output streams and offline rendering |
| `engine.py` | Single-drop synthesis (`synth_drop`) and its helpers |
| `drop_bank.py` | Pre-rendered bank of drop hits by size and sharpness |
| `rain_dsd.py` | The physical rain model behind Amount: rain rate (mm/h) → drop size distribution |
| `house_model.py` | Materials, catchment areas, transmission, roof and wall thickness |
| `spatial.py` | Room acoustics with the window-portal model |
| `rain_texture.py` | The dense rain sheet, including the low-band fusing that prevents "boiling" |
| `yard.py` | Rain on the yard, heard through each opening |
| `field_bed.py`, `field_router.py` | Outdoor wash layer from the loops in `assets/audio/rain/` |
| `runoff.py` | Roof runoff: eave drips and gutter trickle |
| `wind.py`, `gusts.py` | Wind sound and natural gust speed |
| `house_wind.py` | Wind pressure on each wall, ventilation through openings, panel buffeting and sash rattles |
| `outdoor_router.py` | Carries sound arriving through the air (wind, thunder) in through openings and panels |
| `thunder.py` | Lightning strikes and their thunder (`ThunderStorm`) |
| `reverb.py` | Mild indoor room reverb |
| `hrtf.py` | Parametric binaural rendering for "You" |
| `surround.py` | Speaker mics and wall buses → surround devices |

## `app/models/` — data

| Module | Role |
|--------|------|
| `room.py` | `Room`, `Window`, `Speaker`, `Listener`, `default_house()` |
| `materials.py` | Roof and wall material lists, and `role_material()` to migrate old names |
| `settings_schema.py` | One registry of every rain, wind, mix and drop setting. The dock, the phone app and presets are all built from it |
| `presets.py` | Weather and mix presets, example houses, house presets, and the autosave and previous-room paths |

## `app/ui/` — the desktop app

`app/ui/studio/` is the app window:

| Module | Role |
|--------|------|
| `window.py` | `StudioWindow`: builds the panels, menus, files, autosave and presets |
| `context.py` | `StudioContext`: shared room, engine, selection and edit signals |
| `app_bar.py`, `transport.py` | Top bar, and the Play / output / volume bar |
| `canvas.py`, `palette.py`, `objects_panel.py` | Plan and 3D views, the tool palette and the objects list |
| `inspector.py`, `page_*.py` | Inspector card, with pages for the house, windows, speakers and You |
| `weather_dock.py` | The Rain & sound panel, built from `settings_schema` |
| `devices_dialog.py`, `phone_dialog.py`, `remote_bridge.py` | Output setup, and the phone feature's desktop side |
| `controls.py`, `icons.py` | Shared controls and drawn icons |

Also in `app/ui/`: `floorplan.py` (the plan editor), `theme.py` (design tokens
and stylesheet) and `widgets.py` (compass wheels, chips, sections).

## `app/graphics/` — 3D

| Module | Role |
|--------|------|
| `glview.py` | OpenGL 3D view |
| `room3d_view.py` | Software (QPainter) 3D view, the fallback that always works |
| `lightning.py` | Bolt shapes, flash brightness and how much they light the house |

## `app/remote/` — phones

| Module | Role |
|--------|------|
| `server.py` | LAN HTTP and WebSocket server (standard library only) |
| `zones.py` | `ZoneHub`: the shared weather and one room per phone |
| `worker.py` | Renders each phone room in its own process |
| `qr.py` | QR code encoder for the pairing link |
| `web/` | The phone web app: static files, no build step |
| `PROTOCOL.md` | The WebSocket protocol |

## Files on disk

| Path | What it holds | In git? |
|------|---------------|---------|
| `configs/default_house.json` | The house a fresh copy starts with | yes |
| `configs/houses/*.json` | Example houses (written by `app/tools/make_example_houses.py`) | yes |
| `configs/my_house.json` | Your autosaved room | **no** |
| `configs/my_house.previous.json` | The room you had before opening another one | **no** |
| `configs/presets/<kind>/` | Your house, weather and mix presets | **no** |
| `configs/remote.json`, `configs/zones/` | Phone pairing settings and phone rooms | **no** |
| `logs/app.log` | The app log | **no** |

In the built app, these live in the `configs` folder beside `RainRoom3D.exe`.

## Tests

All tests run headless and write any presets and autosaves to temporary
folders, never to your own files.

| Suite | Covers |
|-------|--------|
| `python -m app.tools.test_rain_spatial` | Rain levels, spatial placement, materials, wind, thunder, surround |
| `python -m app.tools.test_settings` | The settings registry, presets and example houses |
| `python -m app.tools.test_remote` | QR encoder, LAN server, shared weather, phone rooms, audio stream |
| `python -m app.tools.test_ui_smoke` | The app window: panels, inspector, dock, presets, files, modes, keys |

`python -m app.tools.quantity_probe` prints how loud each rain Amount is.

## Releasing

1. Set the version in `app/version.py`.
2. Add an entry to `CHANGELOG.md`.
3. Build with `scripts/build_release.ps1`. It refuses to ship personal files.
4. Attach `dist/RainRoom3D-<version>-windows-portable.zip` to the GitHub release.
