# Changelog

All notable changes to RainRoom3D. Versions follow [Semantic Versioning](https://semver.org/):
from 1.0 on, point releases (1.0.x, 1.x) bring fixes, sound tuning and small features.

## [1.0.0] — 2026-10-22

The first full release. It brings a new app, a physical rain model, wind,
thunder and lightning, phone listening and room presets. RainRoom3D is still
evolving, and from here changes come as point releases.

### Added
- **A new app window.**
  - **Design** and **Listen** modes.
  - Plan and 3D views, with floating tools, an objects list and an inspector for whatever is selected.
  - A **Rain & sound** panel, and a transport bar with Play, output, headphones and volume.
- **Rain that is real rain.**
  - The amount is a real rain rate, from 0.2 to 100 mm/h, with real drop sizes. Bigger drops are louder and lower.
  - The biggest drops each get their own spatial voice. The rest blend into a dense texture of the same drops, so heavy rain gets louder without turning into hiss.
- **A house model that drives the sound.**
  - Rain lands on the roof, the walls, the windows, the sills and the ground around the house.
  - Each material sounds different, rings for a different time and lets a different amount of sound through. Roof and wall thickness matter too.
  - The yard all around the house is raining as well, and you hear it through each opening from where you sit.
- **Wind.**
  - Strength and direction, with natural gusts and an optional shifting direction.
  - It blows rain onto the walls facing it, whistles at the eaves and roars in storms.
  - It reaches you through the house: through open windows (by how far they're open and which way they face), through drafts between windows, and as buffeting and rattling of shut panes. Thick walls keep it out.
- **Thunder and lightning.**
  - Strikes from storms around the house, from distant rumbles to close cracks.
  - The thunder follows the strike's distance and direction, and reaches you through the house the way the wind does.
  - Matching lightning bolts and flashes light up the 3D view.
- **Drips and gutters.** Eave drips and gutter trickle keep going after the rain stops, with their own fader.
- **Weather presets**, from *Drizzle* to *Thunderstorm*, plus your own weather and sound-mix presets.
- **Phone listening.**
  - Scan a QR code to play the rain on a phone over your Wi-Fi.
  - Each phone gets its own room under the same weather, and can play on the phone or on one of this PC's outputs.
  - Phone rooms render in their own process, so they don't stutter the desktop's sound.
- **Example houses:** a tin shed, a tile cottage, a brick house and a glass studio.
- **House presets.**
  - **Save as preset…** keeps a whole house (shape, materials, windows, speakers and every sound setting).
  - **Your presets** reopens one exactly as you saved it.
- **Autosave.**
  - Your room saves itself a moment after each change and when you close the app.
  - It reopens next time, so changes like switching to wood walls stick.
- **File → Back to your previous room.** It undoes opening another house, an example or a preset, or starting a new house.
- **Roof and wall thickness** settings. Leave them empty to use the usual build for the material.
- The version number is shown in **About**.

### Changed
- **Roofs and walls now offer only their own materials.**
  - Roofs: tin, metal, tile, shingle, wood and glass.
  - Walls: brick, wood, glass and tin.
  - Windows are always glass panes.
  - Houses saved with older names, such as "Glass Window" or "Wood" walls, open with the matching new material.
- Drop size now shapes each drop's pitch and length.
- The rain wash follows the rain's energy.
- Window openness is shown everywhere as a percentage and an angle.
- The release zip is named after the version (`RainRoom3D-1.0.0-windows-portable.zip`). It never includes the builder's own room, presets or phone pairing settings.
- `run.bat` now sets up a `.venv` the first time it runs, and no longer patches source files.

### Fixed
- A "bubbling" or "boiling" sound in heavy rain. The low end of a dense rain sheet now blends smoothly.
- Wind at full strength sounding as if it blew straight through the building, even with the windows shut.
- A hollow-bucket ring in heavy rain, caused by identical drop sounds stacking up.
- A 23 Hz drum-roll buzz from drops all landing at the same moment in every audio block.
- Drizzle being silent, and the Amount slider barely changing the loudness.
- Room changes being lost on restart. The desktop app never saved them by itself.

### Removed
- Old engines, launchers and notes from before the current sound engine: the single-drop and weather-demo scripts, the `tools/` folder, the overlay README and the per-version notes.
- An unused `python-osc` dependency.
- `configs/my_house.json` is no longer tracked in git. It is your own room, and now stays on your PC.

## [0.2.0-wip] — 2026-09-06

The tin roof sounds like rain now. This release is a checkpoint.

### Added
- 3D droplets drive stacked wet ticks from a drop sample bank.

### Changed
- **Tin roof:** wet impacts and water spreading on the sheet, instead of jewelry dings, plastic clacks or a brown noise bed.
- Glass panes sit above puddle water and below tin in the mix.
- A downpour is a sheet of ticks on the house (the roof and a thin ring at the eaves), not a 40 m field.

### Fixed
- Quantity 0 is now truly off.

## [0.1.2-wip] — 2026-07-14

### Added
- **Sound mix:** wash, droplets, room echo and wind air, saved with the house.
- A lean Windows portable build. It leaves out CUDA and machine-learning packages, which shrank the zip from about 2.5 GB to about 100 MB.

### Changed
- **Wind rewritten:** a layered body, whoosh and gusts instead of pink static.
- **Headphones (You):** the mix is led by the droplets, with no constant air hiss when the wind is at 0.

### Fixed
- Window sashes now pivot on their real hinge edge and no longer come loose.

## [0.1.1-wip] — 2026-07-13

### Changed
- Refreshed the dark theme: sidebar, workflow navigation, buttons, forms and sliders.
- The inspector scrolls, so fields no longer overlap.
- Tweaked how the floor plan and 3D view look.
- The README uses real app screenshots.

## [0.1.0-wip] — 2026-07-13

The first public work-in-progress release.

- Design a house, place speakers and simulate rain.
- An outdoor rain wash with soft wet impacts.
- Binaural listening as **You**, and routing to several output devices.
- A default Living Room layout.
- Licensed under the PolyForm Noncommercial 1.0.0 license.

[1.0.0]: https://github.com/Stelliro/RainRoom3D/releases/tag/v1.0.0
[0.2.0-wip]: https://github.com/Stelliro/RainRoom3D/releases/tag/v0.2.0-wip
[0.1.2-wip]: https://github.com/Stelliro/RainRoom3D/releases/tag/v0.1.2-wip
[0.1.1-wip]: https://github.com/Stelliro/RainRoom3D/releases/tag/v0.1.1-wip
[0.1.0-wip]: https://github.com/Stelliro/RainRoom3D/releases/tag/v0.1.0-wip
