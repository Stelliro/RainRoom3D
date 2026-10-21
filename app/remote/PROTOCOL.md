# RainRoom phone protocol (version 1)

How the phone web app (`app/remote/web/`) talks to the RainRoom desktop app.
Everything here describes the code exactly as implemented in
`app/remote/server.py` and `app/remote/zones.py`.

## The idea in one paragraph

The whole building shares **one weather** (rain amount, crispness, wind, sound
mix, drop character). Anyone can change it, on the PC or on any phone, and
everyone sees the change. Each phone has **its own room** ("zone"): its own
size, materials, wall and roof thickness, windows and listening spot, saved
separately. The same rain sounds different in each room. A phone can play
its room's rain **on the phone** (the PC renders it and streams it) or on
**one of the PC's sound outputs** (the PC renders that phone's room to that
device, so a speaker in another room plays it). Several phones can be active
at once; each active zone costs one more rain render on the PC.

---

## 1. Connecting

### The URL

```
http://<pc-lan-ip>:<port>/?k=<pairing key>
```

* `port`: 8765 by default. If that port is busy, the server tries the next
  19 ports (8766, 8767, …) and uses the first free one.
* `key`: a random 10-character `[A-Za-z0-9]` pairing key. The **desktop app
  keeps it between runs** (in `configs/remote.json`, together with the on/off
  choice and the last port), so a bookmarked phone link keeps working after a
  restart. **New link** in the desktop Phones dialog makes a new key
  (`RemoteServer.rotate_key()`): every connected phone gets
  `error{code:"bad_key"}` and a 1008 close, and must scan the new code. Zones
  keep their outputs (a PC output keeps playing). A bare `RemoteServer`
  (tests, the dev runner without `--key`) still makes a new key per run.
* `RemoteServer.lan_urls()` returns one URL per LAN IPv4 address, best first:
  `192.168.x` (but `192.168.56.x`, VirtualBox, last), then `10.x`, then
  `172.16–31.x`, then other addresses. Within one range the primary
  interface comes first. That is the address used to reach the default
  route; it is found with a UDP `connect()`, and no packet is sent. The
  range wins over "primary" because a VPN (often `10.x`) or a WSL / Hyper-V
  switch (`172.x`) can own the default route while the phone is on the home
  Wi-Fi. Loopback and link-local (`169.254.x`) addresses are never listed;
  if nothing else exists the list is `["http://127.0.0.1:…"]`.
* The desktop shows the first URL as a QR code (`app/remote/qr.py`, ECC M)
  plus the full list as text.

### HTTP (static files)

* Only `GET` is supported. Other methods get `405`.
* `/` serves `index.html`. Paths ending in `/` serve that folder's
  `index.html`.
* **HTML pages need the key.** A `*.html` request (including `/`) must carry
  `?k=<key>`. Otherwise the server returns `403` with a small page in the
  app's look (light or dark like the phone): without a key "Open the link
  from the PC", with a wrong or old key "This link no longer works" (someone
  made a New link; scan the code again).
* Other files (`.js`, `.css`, images, fonts, `.json`, `.webmanifest`, …)
  are served **without** the key; they hold no data. Use relative URLs
  (`app.js`, `style.css`). The page itself reads the key from
  `location.search` and sends it in `hello`.
* Every response carries `Cache-Control: no-cache`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` and
  `Connection: close` (one request per TCP connection).
* Content types: html, js/mjs (`text/javascript`), css, json, webmanifest,
  svg, png, jpg, gif, webp, ico, woff/woff2/ttf, txt, wav, mp3, map.
* Rejected with `404`: `..` or `.` segments (also when percent-encoded),
  backslashes, NUL bytes, `:` in a segment, dot-files, and anything that
  resolves outside the web root.
* Nothing is fetched from the internet. Put every asset in `app/remote/web/`;
  it is bundled into the Windows build (`scripts/build_release.ps1` passes
  `--add-data app/remote/web;app/remote/web` and fails the build if
  `_internal/app/remote/web/index.html` is missing; the local, gitignored
  `RainRoom3D.spec` lists it in `datas` too) and found both from source and
  from a frozen build (`sys._MEIPASS`).

### WebSocket

`ws://<same host:port>/ws`. Build it from the page location:

```js
const ws = new WebSocket(`ws://${location.host}/ws`);
ws.binaryType = "arraybuffer";
```

* RFC 6455, version 13. A plain `GET /ws` without upgrade headers gets `426`.
* Text messages are JSON objects `{"t": "<type>", ...}`. The server sends
  binary messages only for audio (section 4). Binary messages from the client
  are ignored.
* The largest message accepted is 256 KiB; anything bigger closes the socket
  with code 1009. Fragmented messages are fine, and pings may come between
  fragments. Text that isn't JSON (including absurdly deep nesting) gets
  `error{bad_json}`; before `hello` it gets `no_hello` and a 1008 close.
* A client that doesn't read what the server sends is cut off: once 4 MiB
  are waiting in the server's send buffer for that socket, it is aborted.
* The server sends a WebSocket ping every 20 s (browsers answer on their
  own). It closes a socket that has sent nothing for 75 s, so send
  `{"t":"ping"}` every 15–20 s when nothing else is going on (acks count as
  traffic too).
* Close codes from the server: 1000 normal, 1001 RainRoom is closing or the
  network dropped, 1002 protocol error, 1008 rejected (bad key, no hello,
  replaced by another tab), 1009 message too big, 1011 server error.

### hello (must be the first message, within 15 s)

```json
{"t": "hello", "client_id": "k3Jd9-x81_aa", "key": "Ab3dE6gH9k", "device_name": "Anna's phone"}
```

* `client_id`: generate it once and keep it in `localStorage`. It must match
  `[A-Za-z0-9_-]{1,40}`. The server **lower-cases** it (Windows file names
  ignore case, so `PhoneA` and `phonea` are one room) and `state.client_id`
  gives the form it uses. It names this phone's room file, so a phone gets
  its own room back on its next visit.
* `localStorage` belongs to one address and port. Opening the PC by another
  address (the dialog can list several), a changed DHCP address, or a
  different port gives the browser a new id and so a new, empty room. That's
  why `state` lists the saved rooms (`rooms`, `zone_new`) and a phone can
  switch to one (section 3.5). The desktop keeps its port between runs.
* `key`: the `k` value from the page URL.
* `device_name` (optional, at most 60 characters): shown on the desktop.

Failures send an `error` and then close with 1008:

| code | when |
|---|---|
| `no_hello` | the first message was not `hello`, or nothing came for 15 s |
| `bad_key` | the key is wrong, or someone made a **New link** on the computer (also sent to already-connected phones then, before the close). Ask the user to scan the code again; don't auto-reconnect with the old key |
| `bad_client_id` | the id does not match the pattern above |
| `busy` | 32 phone rooms are already loaded and none is idle (see section 5). Try again later |

On success the server replies with `state` (section 2).

**One socket per room.** If the same `client_id` connects again (a second
tab, or a reload before the old socket closed), the **old** socket gets
`error{code:"replaced"}` and is closed with 1008. A phone stream from the old
socket stops. Don't auto-reconnect after `replaced`; show "Opened somewhere
else" with a button to take it back.

**Reconnecting.** After any other close, reconnect with backoff (1 s, 2 s,
5 s, then every 10 s) and send `hello` again. The room comes back from the
server. Output starts as it was on the server, so a PC output keeps playing,
but a phone stream is always `off` after a reconnect (see 3.4).

---

## 2. Server → client messages

### state (reply to hello and to get_state)

```json
{
  "t": "state",
  "protocol": 1,
  "client_id": "k3Jd9-x81_aa",
  "zone": { …room JSON, see below… },
  "thickness": {"roof_cm": 0.0, "wall_cm": 0.0, "typical_roof_cm": 0.8, "typical_wall_cm": 23.0},
  "weather": {"values": {…}, "preset": "Steady rain", "display": {…}},
  "weather_schema": [ {…}, … ],
  "presets": ["Drizzle", "Light rain", "Steady rain", "Heavy rain", "Downpour", "Storm"],
  "preset_info": [{"name": "Drizzle", "description": "A fine, barely-there mist of drops.", "builtin": true}, …],
  "materials": {…},
  "devices": [{"index": 5, "name": "Speakers (Realtek)", "default": true}, …],
  "output": {"mode": "off", "device": null, "playing": false, "starting": false},
  "volume": 0.75,
  "server_name": "LIVINGROOM-PC",
  "zone_new": true,
  "rooms": [{"id": "k3jd9-x81_aa", "name": "Upstairs bedroom", "width": 4.2, "depth": 3.6,
             "windows": 2, "updated": 1760970000.1, "in_use": false}, …],
  "max_active": 4,
  "audio": {"sample_rate": 48000, "channels": 2, "frames": 2048, "format": "s16le",
            "header_bytes": 12, "magic": 1381122353, "max_lead_ms": 600.0, "start_lead_ms": 250.0}
}
```

* `zone_new`: this id has no saved room yet (a new phone, or the same phone
  on a new address). The phone app then offers the `rooms` it could mean
  ("Welcome back?") before the user starts over.
* `rooms`: saved phone rooms on this PC, newest first (`updated` = UNIX time
  of the last save; `in_use` = a phone has it open now). Includes this
  phone's own room once it is saved.
* `max_active`: how many rooms may play at once (section 3.4).

#### `zone`: the phone's room

This is the same JSON shape `app/utils/persistence.save_room` writes (and it
is what is saved in `configs/zones/<client_id>.json`):

```json
{
  "name": "Bedroom",
  "room": {"width": 3.5, "height": 2.5, "depth": 3.2, "terrain_size": 36.0,
           "roof_material": "Metal Roof", "wall_material": "Brick Wall",
           "wall_thickness": 0.0, "roof_thickness": 0.0, "name": "Bedroom"},
  "listener": {"x": 1.75, "y": 1.2, "z": 1.6, "yaw": 0.0, "pitch": 0.0, "roll": 0.0, "audio_device": null},
  "windows": [
    {"name": "Window", "wall": "north", "offset": 1.25, "width": 1.0, "height": 1.2, "sill": 0.9,
     "open": 0.5, "open_style": "casement", "max_angle_deg": 70.0, "hinge_side": "left",
     "material": "Glass Window", "custom_hinge": "left", "custom_motion": "swing",
     "custom_outward": true, "custom_notes": "", "free_place": false,
     "free_x": 0.0, "free_y": 1.4, "free_z": 0.0, "x": 1.25, "z": 3.2}
  ],
  "speakers": [], "headphones_items": [], "headphones_mode": false,
  "rain": {…the shared values again…, "master_volume": 0.75},
  "master_volume": 0.75, "mix": {…}, "tone": {…}
}
```

What the web app needs from it:

* **Coordinates** are meters. The origin is the south-west floor corner, `x`
  goes east and `z` goes north. `room.width` is the east–west size,
  `room.depth` is north–south, and `room.height` is the floor-to-ceiling
  height.
* **Windows** sit on a wall. `offset` is measured from the **west** end for
  the north/south walls and from the **south** end for east/west walls.
  `width` runs along the wall, `sill` is the bottom of the glass above the
  floor, `height` is the glass height, and `open` goes from 0 (closed) to
  1 (wide open). Phone windows are always `free_place: false`.
* **Listener** ("You"): `x`, `z` on the floor plan and `y` = ear height.
  `listener.yaw` here is in **radians** (the file format). `set_listener`
  takes **degrees**. A yaw of 0 faces north (+z), and the angle grows
  clockwise as seen from above (90° = facing east).
* `roof_thickness` / `wall_thickness` are in **meters** here (0 = the
  material's usual build). The sibling `thickness` object gives the same
  values in **cm**, plus each material's usual thickness for a "default 23 cm"
  placeholder. `set_room` takes **cm** (section 3.3).
* `rain`, `mix` and `tone` repeat the shared weather (and `master_volume`)
  because the file format has them. **The `weather` message is the one to
  trust for weather**. Use top-level `volume`, or the `volume` message, for
  this zone's volume.
* Phone rooms never have speakers: phones listen as "You" (binaural).

#### `weather`: the shared weather

* `values`: every shared key → its value. The shared keys are
  `settings_schema.SETTINGS`, which is exactly `presets.WEATHER_KEYS` +
  `presets.MIX_KEYS` + `tone_pitch, tone_weight, tone_ring, tone_wet,
  tone_soft` + `wind_direction_deg`. Toggles are JSON booleans; the rest are
  numbers.
* `preset`: the name of the weather preset these values match, or `null`
  (show "Custom").
* `display`: key → a short plain-words text for the current value
  (`"moderate rain · 6.1 mm/h"`, `"90° · toward E · hits west side"`,
  `"Off"`). Show it next to the slider.

#### `weather_schema`: how to draw the weather controls

The settings of the `rain` and `wind` groups, in display order:

```json
{"key": "droplet_density", "label": "Amount", "group": "rain", "lo": 0.0, "hi": 1.0, "step": 0.01,
 "kind": "slider", "left": "Drizzle", "right": "Downpour",
 "help": "How much rain is falling, from a light drizzle to a downpour.",
 "advanced": false, "default": 0.55, "enabled_by": null}
```

* `kind`: `"slider"` (lo..hi in `step` steps), `"toggle"` (boolean) or
  `"dial"` (`wind_direction_deg`, 0–359°, wraps around: 0° = rain blown
  toward north, 90° = toward east).
* `left` / `right`: words for the two ends of a slider. `help`: one plain
  sentence (tooltip / info text).
* `advanced: true`: put it behind a "More" disclosure.
* `enabled_by`: the key of a toggle that must be on for this control to
  matter; grey it out otherwise.
* The mix and drop-character settings are shared as well, and `set_weather`
  accepts them, but the phone UI is only meant to show rain and wind.

#### `materials`

```json
{
  "roof": ["Tin Roof", "Tile Roof", "Metal Roof", "Shingle Roof", "Glass Window", "Brick Wall", "Wood"],
  "wall": [same list],
  "roof_hints": {"Tin Roof": "Tin roof — loud and bright; the roof is your ceiling", …},
  "wall_hints": {"Brick Wall": "Brick walls — block almost all outside rain; very quiet and deep", …},
  "window_styles": [{"key": "slider", "label": "Slider (side gap)"}, {"key": "casement", "label": "Casement (side hinge)"}, …],
  "walls": ["north", "south", "east", "west"],
  "hinge_sides": ["left", "right"],
  "limits": {"size": [1.5, 30.0], "height": [2.0, 6.0], "terrain_size": [12.0, 120.0],
             "thickness_cm": [0.0, 150.0], "window_width_min": 0.3, "window_height": [0.3, 4.0],
             "sill_max": 3.5, "max_angle_deg": [10.0, 90.0], "max_windows": 8}
}
```

The window style keys are `slider, casement, awning, hopper, sash, pivot,
tilt_turn, custom`. `hinge_side` only matters for `casement` and `tilt_turn`.
`max_angle_deg` only matters for the styles that swing or tilt (`casement,
awning, hopper, pivot, tilt_turn`).

#### `devices`

This PC's sound outputs, for the "play on the computer" picker:
`[{"index": int, "name": str, "default": bool}]`. Names are cleaned the way
the desktop cleans them (" Windows WASAPI" etc. removed). The list is cached
for 30 s; `refresh_devices` re-reads it. It is empty if the PC has no audio
backend.

#### `output`

`{"mode": "off"|"phone"|"pc", "device": int|null, "playing": bool, "starting": bool}`.

### weather

```json
{"t": "weather", "values": {…}, "preset": "Storm", "display": {…}}
```

This is sent to **every** connected phone whenever the shared weather changes,
whoever changed it (any phone or the desktop), and also to the phone that
changed it. It is **not** sent when a change leaves every value as it was
(for example setting a value it already has, or a value that clamps to the
current one). Update sliders optimistically while dragging, then take
whatever arrives. Throttle slider sends to about 10 per second.

### zone

```json
{"t": "zone", "room": {…room JSON…}, "thickness": {"roof_cm": …, "wall_cm": …, "typical_roof_cm": …, "typical_wall_cm": …}}
```

This is the reply to every accepted room edit (`set_room`, `add_window`,
`set_window`, `remove_window`, `set_listener`). It carries the whole room
**after** the server clamped and validated it, so redraw from it. The server
may have moved or shrunk things.

### output

```json
{"t": "output", "mode": "pc", "device": 7, "playing": true, "starting": false}
{"t": "output", "mode": "off", "device": null, "playing": false, "starting": false,
 "error": "Error opening OutputStream: Invalid device"}
```

This is sent after `set_output` (usually twice: first `starting: true` at
once, then the result when the switch is done), after a stream starts
playing, after a failure, and when the desktop stops this zone. `error` is
present only when something went wrong; show it in plain words, as it is
already worded for people.

### stream (start of phone audio)

```json
{"t": "stream", "state": "start", "stream": 3, "sample_rate": 48000, "channels": 2, "frames": 2048,
 "format": "s16le", "header_bytes": 12, "start_lead_ms": 250.0, "max_lead_ms": 600}
```

This is sent once per stream, right **before** its first audio frame (text
and binary messages share one ordered channel). `stream` is a new id for each
stream; reset the playback counters when it changes. See section 4.

### volume

`{"t": "volume", "value": 0.4}` is the reply to `set_volume`, with the stored
value.

### devices

`{"t": "devices", "devices": [...]}` is the reply to `refresh_devices`.

### error

```json
{"t": "error", "code": "rejected", "message": "That window doesn't exist any more", "for": "set_window"}
```

| code | meaning |
|---|---|
| `rejected` | a valid request that couldn't be done (bad index, unknown preset, a volume that isn't a number, an unknown device, "Connect first …", a non-shared weather key) |
| `busy` | `set_output phone/pc` while `max_active` rooms already play (the message says so in plain words); at `hello`, too many rooms loaded |
| `bad_json` | the text message was not a JSON object |
| `unknown_type` | `t` is not a known type |
| `server` | an unexpected server error (it is logged on the PC) |
| `no_hello`, `bad_key`, `bad_client_id`, `replaced` | see section 1. The socket closes |

`for` is the `t` of the request when known. `message` is plain words for the
user. Apart from the hello errors and `replaced`, **the connection stays
open**.

### pong

`{"t": "pong", "time": 1760970000.12}` is the reply to `ping` (server UNIX
time).

---

## 3. Client → server messages

Unknown keys inside a message are ignored. A field with the wrong type is
ignored, so the rest of the patch still applies. Values out of range are
clamped. Nothing a client sends can crash the server.

### 3.1 Weather (shared by everyone)

```json
{"t": "set_weather", "key": "droplet_density", "value": 0.8}
{"t": "set_weather", "key": "wind_vary_speed", "value": true}
{"t": "apply_preset", "name": "Storm"}
```

* `set_weather` accepts every shared key (see `weather.values`). Numbers are
  clamped to the setting's `lo..hi`; a dial wraps modulo 360; a toggle takes
  `true`/`false` (numbers are read as truthy). `master_volume` and unknown
  keys give `error{rejected}`.
* `apply_preset` takes a name from `presets` (case-insensitive). It sets that
  preset's weather keys; the presets never change the wind direction.
* The result comes back as a `weather` broadcast. On the desktop, the hub
  calls `on_weather_from_phone` callbacks with the changes so the desktop
  Room (the source of truth) and its controls update live. The desktop's own
  edits come back to the phones through `hub.set_shared`.

### 3.2 Volume (this zone only)

`{"t": "set_volume", "value": 0.6}` takes 0..1 and is saved with the zone.
It is applied from the next audio block, for phone streams and PC outputs
alike.

### 3.3 This phone's room

All edits reply with `zone` and are saved to disk (see section 5).

```json
{"t": "set_room", "patch": {"name": "Upstairs bedroom", "width": 4.2, "depth": 3.6, "height": 2.4,
                            "terrain_size": 30, "roof_material": "Tile Roof", "wall_material": "Wood",
                            "roof_thickness": 25, "wall_thickness": ""}}
```

| field | rule |
|---|---|
| `name` | string; whitespace is collapsed and it is cut to 40 characters; empty is ignored |
| `width`, `depth` | meters, clamped to 1.5–30 |
| `height` | ceiling in meters, clamped to 2–6 |
| `terrain_size` | yard size in meters, clamped to 12–120 |
| `roof_material`, `wall_material` | must be one of `materials.roof` / `materials.wall`, otherwise ignored |
| `roof_thickness`, `wall_thickness` | **centimeters**, the same rule as the desktop House page: `null`, `""` or `0` = the material's usual build; a number or numeric string (a comma decimal works) from 0 to 150; **above 150 is ignored** (the last good value stays); negative counts as 0 |

After any size change the server keeps things inside: windows stay inside
their wall and under the ceiling, and the listener stays inside the walls
(15 cm margin) and below the ceiling.

```json
{"t": "add_window", "wall": "east"}
```

This adds a 0.4–1.2 m wide window, 50% open casement, in the middle of the
longest free stretch of the given wall. Without `wall` (or with an invalid
one) it uses the longest free stretch of any wall. A room holds up to 8
windows (more gives `error{rejected}`). The new window is the last one in
`room.windows`.

```json
{"t": "set_window", "index": 0, "patch": {"wall": "west", "offset": 0.8, "width": 1.2, "height": 1.3,
                                           "sill": 0.8, "open": 0.75, "open_style": "awning",
                                           "hinge_side": "right", "max_angle_deg": 60, "name": "Bay"}}
{"t": "remove_window", "index": 1}
```

* `index` is the window's position in `room.windows` (an integer). A missing
  index gives `error{rejected}`.
* `wall`: one of north/south/east/west. `open`: 0–1. `open_style`: one of
  the style keys. `hinge_side`: `left` or `right`. `max_angle_deg`: 10–90.
  `name`: same rule as the room name.
* Fitting on the wall: `width` ≥ 0.3 and ≤ wall length − 0.1; `offset` ≥ 0
  and `offset + width` ≤ wall length; `height` 0.3–4 and ≤ ceiling − 0.1;
  `sill` 0–3.5 and `sill + height` ≤ ceiling − 0.05. Changing `wall`
  re-fits the window to the new wall.

```json
{"t": "set_listener", "x": 2.0, "z": 1.4, "yaw": 90}
```

Every field is optional. `x`, `z` are meters on the floor plan and are
clamped inside (15 cm margin). `y` is the ear height in meters, clamped to
0.4 … ceiling − 0.1. `yaw` is in **degrees** (0 = facing north, 90 = east);
it is stored in radians. While dragging, send at most about 10 per second.

### 3.4 Output

```json
{"t": "set_output", "mode": "phone"}
{"t": "set_output", "mode": "pc", "device": 7}
{"t": "set_output", "mode": "pc"}
{"t": "set_output", "mode": "off"}
```

* `"off"`: stops this zone's rendering, whether a phone stream or a PC
  device.
* `"phone"`: the PC renders this room's rain and streams it to this socket
  (section 4). Call it from a user gesture after creating/resuming the
  `AudioContext` (browser autoplay rules). When the socket closes, the stream
  stops and the mode becomes `off`. After a reconnect, ask again.
* `"pc"`: the PC plays this room's rain binaurally ("You" = headphone mix)
  on PC output `device` (an index from `devices`), or on the system default
  output when `device` is missing or `null`. An index not in `devices` gives
  `error{rejected}`. **This keeps playing after the phone disconnects**, so a
  phone can start the rain on a speaker in another room and walk away. It
  stops with `set_output off` from any later connection of the same phone, or
  from the desktop.
* Switching modes stops the previous output first. Switches run one after
  another on a worker thread, so the reply is an `output` message
  (`starting: true` at once, then the result). A start takes about 3 s: the
  room's sound worker process starts (about 2 s) and builds its drop sound
  bank. Switching between "phone" and "pc" keeps the worker; "off" ends it.
* At most `max_active` (4) rooms play at once, phone streams and PC outputs
  together. One more `set_output phone/pc` gets `error{code:"busy"}`;
  "off" and switching a room that already plays always work.
* An output that fails to open replies `output{mode:"off", error:"…"}`.

### 3.5 Other

| message | reply |
|---|---|
| `{"t": "ack", "played": 123904, "stream": 3}` | none (section 4) |
| `{"t": "ping"}` | `pong` |
| `{"t": "refresh_devices"}` | `devices` (re-reads the PC's outputs) |
| `{"t": "get_state"}` | `state` (a full resync, for example after the tab was hidden) |
| `{"t": "list_rooms"}` | `{"t": "rooms", "rooms": [...], "current": "<this id>"}`, same entries as `state.rooms` |
| `{"t": "forget_room", "room_id": "…"}` | deletes that saved room (not this phone's own, not one in use: `error{rejected}`), then `rooms` |

**Switching rooms.** To use a saved room, the phone stores that room's `id`
as its `client_id` and reconnects with `hello`. If another phone has that
room open, that phone gets `replaced` (the app asks for a second tap
first).

---

## 4. Phone audio (output "phone")

### Frames

Each audio block is one **binary** WebSocket message:

```
offset  size  type        meaning
0       4     uint32 LE   magic 0x52524131 ('RRA1'; bytes 31 41 52 52)
4       4     uint32 LE   seq: 0, 1, 2, … within this stream (wraps at 2^32)
8       4     uint32 LE   start_frame: index of the first sample frame = seq * 2048
12      8192  int16 LE    2048 frames × 2 channels interleaved (L, R, L, R, …)
```

* Every message is exactly **8204 bytes**: 48 000 Hz stereo, 2048 frames,
  about 42.7 ms. The audio is the zone's binaural "You" mix, after this
  zone's volume and the soft limiter (the same signal the PC would play on
  headphones).
* Convert with `new Int16Array(buf, 12, 4096)`, then `/ 32768`. The browser's
  `AudioContext` may run at 44 100 Hz; resample (for example by creating
  `AudioBuffer`s at 48 000 Hz, which the browser resamples, or by a resampler
  in an AudioWorklet).
* A gap in `seq` can't happen on one stream (TCP); if it does, treat it as a
  restart.

### Flow control (the client sets the pace)

The server never sends ahead by more than `max_lead_ms` (600 ms = 28 800
frames, that is 14 blocks) of what the client says it has **played**:

1. The server sends `stream{state:"start"}` and then blocks as fast as it can
   render them until `sent − played + 2048 > max_lead` (at the start
   `played = 0`, so the first burst is 14 blocks ≈ 597 ms).
2. The client queues the blocks and **starts playback once it has at least
   `start_lead_ms` (250 ms) buffered**.
3. About 5 times a second (every ~200 ms) the client sends
   `{"t":"ack","played":<total frames of this stream it has played so far>,"stream":<id>}`.
   `played` counts the stream's frames that have actually been handed to
   the audio output, starting at 0 for each new `stream` id. It must not
   go down. The server clamps it to what it has sent. With `stream` set,
   acks for an old stream are ignored.
4. After each ack the server renders and sends again until the lead is full.
   In steady state the lead stays between about 400 and 600 ms
   (600 ms − one ack interval − one block). That lead absorbs a slow render
   (a setting just changed, the PC is busy), like the desktop's own
   `_LEAD_BLOCKS`.
   Two more limits hold whatever the acks say: the server never sends more
   than real time (+3 %) plus the lead since the stream's first block, and
   it waits while more than 256 KiB are still queued on the socket. So a
   client that acks frames it never got can't make the PC render faster
   than real time or pile audio up in memory.
5. **A client that stops acking stops getting audio.** The server waits; it
   never runs ahead without limit. When acks resume, sending resumes. If the
   phone fell behind (tab in the background), the simplest fix is
   `set_output phone` again: that starts a fresh stream (new id, seq 0) with
   a fresh lead.
6. Underrun on the client (queue empty): play silence, keep counting
   `played` only for real frames played, and keep acking. The server refills
   up to the lead at once.

The 600 / 250 ms values are configurable on the PC (`ZoneHub(max_lead_ms=…,
start_lead_ms=…)`); always read them from `stream` / `state.audio`.

### What a phone stream costs the PC

Every playing room (phone stream or PC output) renders in **its own worker
process** (`app/remote/worker.py`) at below-normal priority, so it neither
shares the desktop's Python GIL nor slows the desktop's own playback, and a
room's start-up (drop bank, kernels, calibration) can't stall the desktop's
speakers. Measured on the development PC (16 threads): about 7 ms CPU per
2048-frame block (42.7 ms of audio) at "Steady rain" and about 9–10 ms at
maximum Amount with storm wind, per room. Three rooms at maximum rain and
storm wind next to a desktop-like render ran with zero underruns, and the
desktop render stayed on time. Each worker is a separate Python process
(numpy + the engine), and it ends when its room stops. Settings changes
never stall the render: the engine rebuilds sound kernels and tone banks on
worker threads.

The worker talks to the app over a localhost TCP socket with a one-time
random token (passed in its environment); messages are a JSON header plus
raw PCM bytes, no pickle. From source it runs as `python -m
app.remote.worker`; from a frozen build as `RainRoom3D.exe
--rainroom-zone-worker` (app/main.py hands that flag over before Qt
starts). If a worker can't start, the room renders inside the app instead
(with a log warning), and `RAINROOM_ZONE_INPROCESS=1` forces that (the UI
tests use it so their stubbed device start applies).

---

## 5. Zones and saving

* A zone is keyed by `client_id`. Its room is saved with
  `persistence.save_room` to `<zones dir>/<client_id>.json`, in the same file
  format as a house. The zones dir is `configs/zones/` beside the app: the
  repo's `configs/zones` when run from source, and `configs/zones` next to
  `RainRoom3D.exe` when frozen. The `RAINROOM_ZONES_DIR` env var or
  `ZoneHub(zones_dir=…)` overrides it (the tests and the dev runner use a temp
  folder).
* Saves are debounced: about 1 s after the last edit (room, window, listener,
  volume). The write goes to `<id>.json.tmp` first and is then renamed over
  the old file. Everything still unsaved is written when the server stops.
* A new phone gets: name "Bedroom", 3.5 × 3.2 m, 2.5 m ceiling, 36 m yard,
  the desktop house's roof and wall materials (usual thickness), one
  1 m casement window on the north wall at 50% open, the listener in the
  middle at 1.2 m, volume 75%, no speakers. It is **saved once it is first
  edited** (room, window, listener or volume), so phones that only look
  leave no files behind.
* Limits: at most 32 rooms in memory (idle ones, i.e. not connected and not
  playing, are saved and dropped first; if none is idle a new `hello` gets
  `busy`), and at most 64 saved files (when a new file would go over, the
  oldest ones nobody has loaded are deleted). `ZoneHub(max_active=…,
  max_loaded=…, max_files=…)` changes them.
* On load, anything out of range is clamped by the same rules as edits, and
  speakers are removed.
* The output mode is **not** saved: after a PC restart every zone starts
  `off`.
* The shared weather is not owned by zones. Before every rendered block the
  zone copies the shared snapshot into its room (PC-output zones get it
  pushed on every change), so the zone files' `rain` / `mix` / `tone`
  values just hold whatever the weather was at the last save.

---

## 6. Desktop / Python side (for the integration)

```python
from app.remote.zones import ZoneHub
from app.remote.server import RemoteServer
from app.remote import qr

hub = ZoneHub()                               # zones_dir=…, engine_factory=…, device_lister=…
hub.sync_from_room(ctx.room)                  # shared values + default materials for new zones
hub.on_weather_from_phone(cb)                 # cb(changes: dict, preset_name: str|None), from the server thread
hub.add_listener(cb)                          # cb(event, data): "weather" | "zones" | "output", any thread
hub.set_shared(values, preset_name=None)      # desktop edits -> phones (no-op if nothing changed)
hub.list_zones()                              # [{client_id, name, device_name, connected, output, last_seen}]
hub.stop_zone_output(client_id)
hub.saved_rooms(); hub.forget_room(room_id)   # the phone's room picker
srv = RemoteServer(hub, port=8765, key=None)  # key=None -> random per run
srv.start(); srv.lan_urls(); srv.key; srv.port; srv.connections()
srv.rotate_key()                              # new key; phones on the old link get bad_key + close
srv.stop()                                    # closes sockets, stops all zone renders/devices, saves
matrix = qr.encode(srv.lan_urls()[0], "M")    # list of rows, True = dark, no quiet zone
```

* `on_weather_from_phone` callbacks run on the server's asyncio thread. Hand
  them to the Qt main thread through a queued signal, then
  `settings_schema.set(desktop_room, key, value)` + `ctx.edited("sound", …)`.
  The hub snapshot is already updated and broadcast, and the desktop's
  following `set_shared` echo is dropped when nothing changed.
* Engine API used per zone (`SpatialRainEngine`, inside the worker):
  `prepare_for_stream()` builds the drop bank, then resets the sim and the
  limiter, without a device. `render_listener_block(frames)` returns
  float32 `(frames, 2)`, the same as the headphone stream. For PC output,
  the zone uses `start_headphones(headphones_device=idx)` and `stop()`.
  In the app the zone holds a `worker.ProcessEngine` with the same calls
  (plus `render_pcm16`, `sync_room` for PC-output edits, and `release`);
  `ZoneHub(engine_factory=…)` swaps in anything with that API (tests).
* Desktop integration (`app/ui/studio/remote_bridge.py`, `phone_dialog.py`):
  `RemoteBridge` owns one hub + one server for the studio window. Start, stop
  and New link run on a worker thread; desktop weather edits ("sound" /
  "rain" areas, a loaded house) are pushed with `set_shared` at most ~10 times
  a second, with the dock's preset name; phone edits come back through a
  queued Qt signal and are written with `settings_schema.set` +
  `ctx.edited(area, source=bridge)`. Edits from the bridge are not pushed
  back: the hub already has the phone's (possibly newer) values, so only the
  dock's preset name follows (`set_shared({}, preset)`); sending the whole
  Room back from a busy UI thread used to send sliders backwards. Phones'
  PC-output device list is the desktop's own `ctx.devices`. App close calls
  `bridge.shutdown()`, which stops the server and every zone on a non-daemon
  worker without blocking the UI. The on/off choice, key and port live in
  `configs/remote.json` (`RAINROOM_REMOTE_CONFIG` overrides the path), never
  in the house file. That file carries a fingerprint of the PC and user; a
  copy on another machine is ignored (sharing stays off, a new key is made).
  The release scripts leave `configs/remote.json` and `configs/zones/` out.
* Dev runner: `python -m app.remote.devserver [--port 8765] [--key KEY]
  [--house configs/my_house.json] [--zones-dir DIR] [--fake-pc] [--no-qr]`.
  It prints the URLs and a terminal QR. The shared weather comes from the
  house file, read only (the file is never written). Phone rooms go to
  `%TEMP%/rainroom_dev_zones` unless `--zones-dir` is given.
