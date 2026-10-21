"""
Headless smoke test of the studio main window: app bar, canvas chrome,
inspector pages, the registry-built rain & sound dock, presets, example
houses, transport, Design / Listen modes and keyboard handling.

Never opens an audio stream (engine start/stop are stubbed) and never writes
into configs/ (user presets go to a temp dir).

Run:  python -m app.tools.test_ui_smoke
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

# Must be set before Qt / presets are imported.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["RAINROOM_NOGPU"] = "1"
_PRESET_TMP = tempfile.mkdtemp(prefix="rainroom_presets_")
os.environ["RAINROOM_PRESET_DIR"] = _PRESET_TMP
# The app autosaves the room: never into your own configs/my_house.json
os.environ["RAINROOM_AUTOSAVE"] = os.path.join(_PRESET_TMP, "autosave.json")
# Phone rooms + phone settings never touch configs/
os.environ["RAINROOM_ZONES_DIR"] = os.path.join(_PRESET_TMP, "zones")
os.environ["RAINROOM_REMOTE_CONFIG"] = os.path.join(_PRESET_TMP, "remote.json")
# Phone rooms render in this process, so the stubbed engine start below applies
# (a worker process would open a real sound device)
os.environ["RAINROOM_ZONE_INPROCESS"] = "1"

from PySide6 import QtCore, QtGui, QtTest, QtWidgets  # noqa: E402


def _ok(name: str, cond: bool, detail: str = "") -> bool:
    mark = "PASS" if cond else "FAIL"
    extra = f" — {detail}" if detail else ""
    print(f"  [{mark}] {name}{extra}")
    return bool(cond)


# ----- harness -----

_DIALOGS = []
_CALLS = {"set_dim": 0, "set_terrain": 0, "start": 0, "start_headphones": 0, "stop_all": 0}


def _mute_dialogs() -> None:
    """A modal box would hang an offscreen run — record it instead."""
    MB = QtWidgets.QMessageBox

    def rec(kind, ret=None):
        def f(*a, **k):
            _DIALOGS.append((kind, a[1:3] if len(a) > 2 else a))
            return ret
        return staticmethod(f)

    MB.critical = rec("critical", MB.Ok)
    MB.warning = rec("warning", MB.Ok)
    MB.information = rec("information", MB.Ok)
    MB.question = rec("question", MB.No)


def _make_main():
    import app.main as M
    from app.audio.spatial_engine import SpatialRainEngine
    from app.models.room import default_house
    from app.ui.studio.page_house import HousePage

    # Deterministic start (not the user's own configs/my_house.json)
    M.Main._load_startup_preset = staticmethod(default_house)

    # Count footprint handler calls; the page wires them through late-bound lambdas
    orig_dim, orig_terrain = HousePage._set_dim, HousePage._set_terrain

    def set_dim(self, attr, val):
        _CALLS["set_dim"] += 1
        return orig_dim(self, attr, val)

    def set_terrain(self, v):
        _CALLS["set_terrain"] += 1
        return orig_terrain(self, v)

    HousePage._set_dim = set_dim
    HousePage._set_terrain = set_terrain

    # No real audio streams, ever
    def stub(name):
        def f(self, *a, **k):
            _CALLS[name] = _CALLS.get(name, 0) + 1
        return f

    SpatialRainEngine.start = stub("start")
    SpatialRainEngine.start_headphones = stub("start_headphones")
    SpatialRainEngine.stop_all = stub("stop_all")
    SpatialRainEngine.play_test_tone = stub("play_test_tone")
    SpatialRainEngine.play_speaker_test = stub("play_speaker_test")
    return M, M.Main()


def _settle(n: int = 3) -> None:
    app = QtWidgets.QApplication.instance()
    for _ in range(n):
        app.processEvents()
        QtCore.QThread.msleep(50)


def _key(widget, key, text: str = "") -> None:
    for t in (QtCore.QEvent.KeyPress, QtCore.QEvent.KeyRelease):
        QtWidgets.QApplication.sendEvent(widget, QtGui.QKeyEvent(t, key, QtCore.Qt.NoModifier, text))


# ----- tests -----

def test_layout_and_transport(M, w) -> bool:
    """(was: 3 sidebar steps + play bar) The studio has its four regions, four
    inspector pages and one transport with Play / Output / Volume / status."""
    from app.ui.studio.inspector import PAGE_HOUSE
    parts = all(hasattr(w, n) for n in ("app_bar", "canvas", "palette", "objects", "inspector", "dock", "transport"))
    ok = _ok("app bar, canvas chrome, dock and transport exist", parts)
    ok &= _ok("inspector has House / Window / Speaker / You pages",
              w.inspector.stack.count() == 4 and w.inspector.current_page() == PAGE_HOUSE)
    t = w.transport
    modes = [t.cmb_output.itemData(i) for i in range(t.cmb_output.count())]
    ok &= _ok("transport has Play, Output, Volume, status",
              all(hasattr(t, n) for n in ("btn_play", "cmb_output", "sld_volume", "lbl_volume", "lbl_live"))
              and modes == ["headphones", "multi", "all"], str(modes))
    plays = [b.text() for b in w.findChildren(QtWidgets.QPushButton) if "Play" in b.text()]
    ok &= _ok("Play exists only in the transport", plays == [t.btn_play.text()], str(plays))
    saves = [b.text() for b in w.findChildren(QtWidgets.QPushButton) if b.text().startswith("Save house")]
    ok &= _ok("no duplicate Save house buttons", not saves, str(saves))
    keys = {a.shortcut().toString() for a in (w.act_new, w.act_open, w.act_save, w.act_save_as)}
    ok &= _ok("Ctrl+N / O / S / Shift+S shortcuts", keys == {"Ctrl+N", "Ctrl+O", "Ctrl+S", "Ctrl+Shift+S"}, str(keys))
    return ok


def test_mode_switch(M, w) -> bool:
    """Listen hides the editing chrome, goes 3D and enlarges the dock; Design
    brings everything back (and each mode remembers its view)."""
    w.set_mode("design")
    w.canvas.set_view("plan")
    w.canvas.set_tool("window")
    w.set_mode("listen")
    hidden = not any(x.isVisibleTo(w) for x in (w.palette, w.objects, w.inspector))
    ok = _ok("Listen hides palette, objects list and inspector", hidden)
    ok &= _ok("Listen switches the canvas to 3D", w.canvas.current_view() in ("3d", "gl"), w.canvas.current_view())
    ok &= _ok("Listen cancels a placement tool", w.canvas.tool == "select")
    ok &= _ok("Listen enlarges the dock", w.dock.property("large") is True and w.dock.width() == w.dock.WIDTH_LARGE)
    ok &= _ok("mode switch reflects in the app bar", w.app_bar.seg_mode.current() == "listen")
    w.set_mode("design")
    shown = all(x.isVisibleTo(w) for x in (w.palette, w.objects, w.inspector))
    ok &= _ok("Design brings the chrome back on the plan", shown and w.canvas.current_view() == "plan")
    w.set_mode("listen")
    ok &= _ok("each mode remembers its view", w.canvas.current_view() == "3d")
    w.set_mode("design")
    return ok


def test_volume(M, w) -> bool:
    w.transport.sld_volume.setValue(49)
    ok = abs(w.room.master_volume - 0.49) < 1e-9 and abs(w.engine.room.master_volume - 0.49) < 1e-9
    return _ok("volume slider → room.master_volume via engine", ok and w.transport.lbl_volume.text() == "49%",
               w.transport.lbl_volume.text())


def test_every_setting_has_one_widget(M, w) -> bool:
    from app.models import settings_schema as S
    from app.ui.studio.weather_dock import _display
    d = w.dock
    keys = [s.key for s in S.SETTINGS]
    ok = _ok("one widget per Setting", sorted(d._setting_inputs) == sorted(keys)
             and len({id(x) for x in d._setting_inputs.values()}) == len(keys))
    bad = []
    for s in S.SETTINGS:
        wd = d._setting_inputs[s.key]
        before = S.get(w.room, s.key)
        if s.kind == "toggle":
            wd.setChecked(not bool(before))
            want = 0.0 if before else 1.0
        elif s.kind == "dial":
            want = float((int(before) + 37) % 360)
            wd.setValue(int(want))
        else:
            t = M._ticks(s, before)
            t2 = t - 3 if t >= 3 else t + 3
            wd.setValue(t2)
            want = M._from_ticks(s, t2)
        got = S.get(w.room, s.key)
        if abs(got - want) > 1e-6:
            bad.append((s.key, want, got))
        lbl = d._setting_values.get(s.key)
        if lbl is not None and lbl.text() != _display(s, got):
            bad.append((s.key, "label", lbl.text()))
    ok &= _ok("moving each control changes the Room attribute", not bad, str(bad))
    S.set(w.room, "wind_speed", 0.5)
    S.set(w.room, "wind_vary_direction", False)
    d.sync_settings_ui()
    off = not d._setting_rows["wind_dir_range_deg"].isEnabled()
    d._setting_inputs["wind_vary_direction"].setChecked(True)
    on = d._setting_rows["wind_dir_range_deg"].isEnabled()
    ok &= _ok("advanced wind knobs follow their toggle", off and on)
    d._setting_inputs["wind_speed"].setValue(0)
    calm = (not d._setting_rows["wind_vary_direction"].isEnabled()
            and not d._setting_rows["wind_dir_range_deg"].isEnabled()
            and d._setting_rows["wind_vary_speed"].isEnabled())
    d._setting_inputs["wind_speed"].setValue(50)
    windy = d._setting_rows["wind_vary_direction"].isEnabled()
    ok &= _ok("Shifting direction greys out while the wind is calm", calm and windy)
    ok &= _ok("advanced wind options are nested + collapsed",
              "wind_more" in d._sections and not d._sections["wind_more"].is_expanded())
    ok &= _ok("sound mix + drop character start folded",
              not d._sections["mix"].is_expanded() and not d._sections["drop"].is_expanded())
    return ok


def test_weather_presets(M, w) -> bool:
    from app.models import presets as P, settings_schema as S
    d = w.dock
    bad = []
    for p in P.builtin("weather"):
        d.on_weather_chip(p.name)
        if d.weather_chips.current() != p.name or P.matching(w.room, "weather") != p.name:
            bad.append((p.name, d.weather_chips.current()))
        sld = d._setting_inputs["droplet_density"]
        if sld.value() != M._ticks(S.by_key("droplet_density"), p.values["droplet_density"]):
            bad.append((p.name, "slider", sld.value()))
        if d._setting_inputs["wind_vary_speed"].isChecked() != bool(p.values["wind_vary_speed"]):
            bad.append((p.name, "gusty"))
    ok = _ok("each weather preset updates widgets + chip", not bad, str(bad))
    d._setting_inputs["droplet_density"].setValue(d._setting_inputs["droplet_density"].value() + 7)
    ok &= _ok("moving a slider off-preset shows Custom",
              d.weather_chips.current() is None and d.weather_chips._custom.isChecked())
    return ok


def test_weather_quick_pick(M, w) -> bool:
    """The app-bar weather button names the preset (or Custom) and the rain rate,
    and its popup chips apply presets like the dock does."""
    from app.ui.studio.weather_dock import rain_rate_text
    d, bar = w.dock, w.app_bar
    d.on_weather_chip("Storm")
    t1 = bar.weather_text()
    ok = _ok("button shows the preset and mm/h", t1.startswith("Storm · ") and "mm/h" in t1, t1)
    d._setting_inputs["droplet_density"].setValue(d._setting_inputs["droplet_density"].value() - 9)
    t2 = bar.weather_text()
    ok &= _ok("button says Custom off-preset", t2.startswith("Custom · ") and t2.endswith(rain_rate_text(w.room)), t2)
    w._sync_quick_weather()
    names = set(bar.weather_popup.chips.buttons())
    ok &= _ok("popup lists every weather preset", names == set(d._presets["weather"]), str(sorted(names)))
    bar.weather_popup.chips.buttons()["Drizzle"].click()
    ok &= _ok("popup chip applies the preset", d.current_weather() == "Drizzle"
              and bar.weather_text().startswith("Drizzle"), bar.weather_text())
    return ok


def test_amount_caption_uses_preset_words(M, w) -> bool:
    """The Amount caption speaks the chips' language ("Steady rain · 6.1 mm/h")."""
    from app.models import presets as P
    d = w.dock
    bad = []
    for p in P.builtin("weather"):
        if p.name == "Storm":  # a wind preset: its amount reads as the rain band it sits in
            continue
        d.on_weather_chip(p.name)
        cap = d._setting_values["droplet_density"].text()
        if not cap.startswith(p.name + " · ") or "mm/h" not in cap:
            bad.append((p.name, cap))
    ok = _ok("Amount caption names the same band as the preset", not bad, str(bad))
    d.on_weather_chip("Steady rain")
    return ok


def test_mix_presets_and_user_presets(M, w) -> bool:
    from app.models import presets as P
    d = w.dock
    bad = []
    for i in range(1, d.cmb_mix_preset.count()):
        name = d.cmb_mix_preset.itemData(i)
        d.on_mix_preset(i)
        if P.matching(w.room, "mix") != name or d.cmb_mix_preset.currentIndex() != i:
            bad.append(name)
    ok = _ok("each mix preset applies + selects", not bad and d.cmb_mix_preset.count() > 5, str(bad))

    d._setting_inputs["mix_wash"].setValue(d._setting_inputs["mix_wash"].value() + 11)
    ok &= _ok("mix shows Custom off-preset", d.cmb_mix_preset.currentIndex() == 0)
    d.ask_preset_name = lambda kind: "My test mix"
    d.confirm = lambda *a: True
    d.save_preset("mix")
    idx = d.cmb_mix_preset.findData("My test mix")
    ok &= _ok("Save as preset adds a user mix preset",
              idx > 0 and d.cmb_mix_preset.currentIndex() == idx and d.btn_mix_del_preset.isEnabled())
    d.delete_preset("mix")
    ok &= _ok("Delete removes the user mix preset",
              d.cmb_mix_preset.findData("My test mix") < 0 and not P.user("mix"))

    d.ask_preset_name = lambda kind: "Test squall"
    d.save_preset("weather")
    ok &= _ok("Save as preset adds a weather chip",
              "Test squall" in d.weather_chips.buttons() and d.weather_chips.current() == "Test squall"
              and d.btn_weather_del.isEnabled())
    d.delete_preset("weather")
    ok &= _ok("Delete removes the weather chip", "Test squall" not in d.weather_chips.buttons())
    ok &= _ok("user presets went to the temp dir", str(P.PRESET_DIR) == _PRESET_TMP, str(P.PRESET_DIR))
    return ok


def test_reset_buttons(M, w) -> bool:
    from app.models import settings_schema as S
    d = w.dock
    ok = True
    for gid in ("mix", "drop"):
        for s in S.in_group(gid):
            d._setting_inputs[s.key].setValue(0)
        d._reset_buttons[gid].click()
        bad = [s.key for s in S.in_group(gid) if abs(S.get(w.room, s.key) - s.default) > 1e-9]
        bad += [s.key for s in S.in_group(gid)
                if d._setting_inputs[s.key].value() != M._ticks(s, s.default)]
        ok &= _ok(f"Reset restores {gid} defaults + widgets", not bad, str(bad))
    ok &= _ok("mix reset lands on Recommended", d.cmb_mix_preset.currentData() == "Recommended")
    return ok


def test_apply_room_blocks_footprint(M, w) -> bool:
    from app.models.room import default_house
    house = w.inspector.house
    before = dict(_CALLS)
    room = default_house()
    room.width, room.depth, room.terrain_size = 7.5, 6.25, 50.0
    w.room = room
    w.engine.room = room
    w._apply_room_to_ui()
    ok = _ok("_apply_room_to_ui never calls _set_dim / _set_terrain",
             _CALLS["set_dim"] == before["set_dim"] == 0 and _CALLS["set_terrain"] == 0,
             f"dim={_CALLS['set_dim']} terrain={_CALLS['set_terrain']}")
    ok &= _ok("footprint spinboxes still show the room",
              abs(house.spin_w.value() - 7.5) < 1e-9 and abs(house.spin_terrain.value() - 50.0) < 1e-9)
    house.spin_w.setValue(8.0)
    ok &= _ok("user edits still reach _set_dim", _CALLS["set_dim"] == 1 and abs(w.room.width - 8.0) < 1e-9)
    # lowering the ceiling keeps speakers and You under it
    w.room.speakers[0].y = 3.4
    w.room.listener.y = 3.0
    house.spin_h.setValue(2.2)
    ok &= _ok("a lower ceiling clamps speaker / You heights",
              w.room.speakers[0].y <= 2.2 and w.room.listener.y <= 2.2,
              f"{w.room.speakers[0].y:.2f} / {w.room.listener.y:.2f}")
    return ok


def test_thickness_placeholders(M, w) -> bool:
    from app.audio import house_model as hm
    house = w.inspector.house
    w._on_selection(("none", -1))
    house.cmb_roof.setCurrentText("Tin Roof")
    house.cmb_wall.setCurrentText("Brick Wall")
    roof_ph = f"default {hm.typical_roof_thickness(w.room) * 100.0:g} cm"
    wall_ph = f"default {hm.typical_wall_thickness(w.room) * 100.0:g} cm"
    ok = _ok("thickness boxes show the material's usual build greyed",
             house.ed_roof_t.placeholderText() == roof_ph and house.ed_wall_t.placeholderText() == wall_ph,
             f"{house.ed_roof_t.placeholderText()} / {house.ed_wall_t.placeholderText()}")
    house.ed_wall_t.setText("30")
    ok &= _ok("typing cm stores metres", abs(w.room.wall_thickness - 0.30) < 1e-9)
    house.ed_wall_t.setText("")
    ok &= _ok("empty means the usual build", w.room.wall_thickness == 0.0)
    return ok


def test_example_houses(M, w) -> bool:
    from app.models import presets as P
    cmb = w.inspector.house.cmb_example
    names = [n for n, _, _ in P.example_houses()]
    ok = _ok("example picker lists configs/houses", cmb.count() == len(names) + 1 and len(names) >= 4, str(names))
    n0 = len(_DIALOGS)
    w._load_example_house(1)
    ok &= _ok("loading an example replaces the room",
              w.room.name == names[0] and w.engine.room is w.room and len(_DIALOGS) == n0, w.room.name)
    ok &= _ok("material hint follows the loaded house",
              w.inspector.house.lbl_roof_hint.text() != ""
              and w.inspector.house.cmb_roof.currentText() == w.room.roof_material)
    ok &= _ok("an example never becomes the Save target", w.ctx.path is None and not w.ctx.dirty)
    ok &= _ok("app bar shows the loaded house name", w.app_bar.ed_name.text() == w.room.name)
    w._new_house()
    ok &= _ok("New house resets the room and example picker",
              w.room.name == "Living Room" and cmb.currentIndex() == 0)
    return ok


def test_house_presets(M, w) -> bool:
    """Walls and roofs list their own materials; Save as preset… keeps the whole
    house; editing the room never touches the preset; opening it again brings
    it back as saved; the room you're working on is autosaved."""
    from app.models import presets as P
    from app.models.materials import ROOF_MATERIALS, WALL_MATERIALS
    from app.utils.persistence import load_room

    hp = w.inspector.house
    walls = [hp.cmb_wall.itemText(i) for i in range(hp.cmb_wall.count())]
    roofs = [hp.cmb_roof.itemText(i) for i in range(hp.cmb_roof.count())]
    ok = _ok("walls and roofs offer their own materials (no 'Glass Window' wall)",
             walls == list(WALL_MATERIALS) and roofs == list(ROOF_MATERIALS) and "Glass Window" not in walls,
             f"walls={walls}")
    w._new_house()
    hp.cmb_wall.setCurrentText("Wood Wall")
    hp.cmb_roof.setCurrentText("Tin Roof")
    w.room.windows[0].open = 0.4
    QID = QtWidgets.QInputDialog
    orig_get, orig_confirm = QID.getText, w._confirm
    QID.getText = staticmethod(lambda *a, **k: ("Test house", True))
    w._confirm = lambda *_a: True
    try:
        saved = w._save_house_preset()
        path = P.house_preset_path("Test house")
        ok &= _ok("Save as preset… writes the house", saved and path is not None and path.is_file()
                  and w.ctx.preset_name == "Test house" and not w.ctx.dirty, str(path))
        before = path.read_text(encoding="utf-8")
        # Change the room after saving: the preset must not follow
        hp.cmb_wall.setCurrentText("Brick Wall")
        w.room.windows[0].open = 0.0
        w.ctx.edited("house")
        w._autosave_now()
        auto = load_room(str(P.autosave_path()))
        ok &= _ok("the room you're working on is autosaved (preset untouched)",
                  auto.wall_material == "Brick Wall" and path.read_text(encoding="utf-8") == before
                  and json.loads(P.autosave_path().read_text(encoding="utf-8")).get("from_preset") == "Test house")
        i = hp.cmb_preset.findText("Test house")
        w._load_house_preset(i)
        ok &= _ok("opening the preset brings it back as saved",
                  w.room.wall_material == "Wood Wall" and hp.cmb_wall.currentText() == "Wood Wall"
                  and abs(float(w.room.windows[0].open) - 0.4) < 1e-9 and w.ctx.path is None
                  and hp.cmb_preset.currentText() == "Test house", w.room.wall_material)
        ok &= _ok("the room you had before opening it is kept",
                  load_room(str(P.previous_room_path())).wall_material == "Brick Wall")
        w._restore_previous_room()
        ok &= _ok("Back to your previous room brings it back",
                  w.room.wall_material == "Brick Wall" and w.ctx.preset_name == "Test house")
        w._restore_previous_room()
        ok &= _ok("doing it again swaps forward", w.room.wall_material == "Wood Wall")
        hp.cmb_preset.setCurrentIndex(hp.cmb_preset.findText("Test house"))
        w._delete_house_preset()
        ok &= _ok("Delete removes the preset, keeps the room",
                  P.house_preset_path("Test house") is None and w.room.wall_material == "Wood Wall"
                  and w.ctx.preset_name is None)
    finally:
        QID.getText = orig_get
        w._confirm = orig_confirm
    w._new_house()
    return ok


def test_inspector_follows_selection(M, w) -> bool:
    from app.ui.studio.inspector import PAGE_HOUSE, PAGE_SPEAKER, PAGE_WINDOW, PAGE_YOU
    insp = w.inspector
    r = w.room
    w._on_selection(("speaker", 0))
    ok = _ok("speaker selected → Speaker page with its name",
             insp.current_page() == PAGE_SPEAKER and insp.title() == r.speakers[0].name, insp.title())
    w._on_selection(("window", 1))
    ok &= _ok("window selected → Window page with its name",
              insp.current_page() == PAGE_WINDOW and insp.title() == r.windows[1].name)
    w.canvas.floor.set_selection("listener", 0)  # a click on the plan
    ok &= _ok("plan click on You → You page", insp.current_page() == PAGE_YOU and w._selected == ("listener", 0))
    insp.you.spin_x.setValue(1.25)
    ok &= _ok("You page moves the listener", abs(r.listener.x - 1.25) < 1e-9)
    insp.you.spin_yaw.setValue(90)
    ok &= _ok("You page sets facing", abs(r.listener.yaw - 1.5707963) < 1e-6 and insp.you.wheel.value() == 90)
    insp.btn_close.click()
    ok &= _ok("close → House page", insp.current_page() == PAGE_HOUSE and w._selected == ("none", -1))
    # editing in the inspector reaches the room and the objects list
    w._on_selection(("window", 0))
    insp.window_page.sld_open.setValue(30)
    _settle()
    row = next(w.objects.list.item(i) for i in range(w.objects.list.count())
               if w.objects.list.item(i).data(QtCore.Qt.UserRole) == ("window", 0))
    ok &= _ok("Open slider → room + objects status",
              abs(r.windows[0].open - 0.30) < 1e-9 and "30%" in str(row.data(QtCore.Qt.UserRole + 1)))
    w.objects._clicked(next(w.objects.list.item(i) for i in range(w.objects.list.count())
                            if w.objects.list.item(i).data(QtCore.Qt.UserRole) == ("speaker", 1)))
    ok &= _ok("objects list click selects", w._selected == ("speaker", 1) and insp.current_page() == PAGE_SPEAKER)
    w._on_selection(("none", -1))
    return ok


def test_window_buttons(M, w) -> bool:
    page = w.inspector.window_page
    if not w.room.windows:
        return _ok("house has a window to test", False)
    w.room.windows[0].free_place = False
    w._on_selection(("window", 0))
    ok = _ok("Snap back hidden for a wall window", page.btn_snap_window.isHidden())
    w.room.windows[0].free_place = True
    w._on_selection(("window", 0))
    ok &= _ok("Snap back shown for a free-placed window", not page.btn_snap_window.isHidden())
    page.btn_snap_window.click()
    ok &= _ok("Snap back clears free_place", not w.room.windows[0].free_place and page.btn_snap_window.isHidden())
    page.cmb_style.setCurrentIndex(page.cmb_style.findData("slider"))
    ok &= _ok("hinge + angle grey out for a sliding window",
              not page.cmb_hinge.isEnabled() and not page.spin_angle.isEnabled()
              and w.room.windows[0].open_style == "slider")
    page.cmb_style.setCurrentIndex(page.cmb_style.findData("custom"))
    ok &= _ok("custom design shows only for Custom", not page.custom_box.isHidden())
    n = len(w.room.windows)
    page.btn_del_window.click()
    ok &= _ok("Remove window removes it", len(w.room.windows) == n - 1 and w._selected == ("none", -1))
    return ok


def test_add_speaker_defaults(M, w) -> bool:
    n = len(w.room.speakers)
    w.objects.btn_add_speaker.click()
    s = w.room.speakers[-1]
    ok = _ok("Add speaker uses the canvas-tool defaults 0.45 / 0.28 / 0.18",
             (s.width, s.height, s.depth) == (0.45, 0.28, 0.18), str((s.width, s.height, s.depth)))
    ok &= _ok("the new speaker is selected", len(w.room.speakers) == n + 1 and w._selected == ("speaker", n))
    return ok


def test_delete_and_escape(M, w) -> bool:
    floor = w.canvas.floor
    n = len(w.room.speakers)
    w._on_selection(("speaker", 0))
    floor.setFocus()
    _key(floor, QtCore.Qt.Key_Delete)
    ok = _ok("Delete on the canvas removes the selected speaker",
             len(w.room.speakers) == n - 1 and w._selected == ("none", -1))
    nw = len(w.room.windows)
    w._on_selection(("window", 0))
    _key(w.canvas.view3d, QtCore.Qt.Key_Delete)
    ok &= _ok("Delete in 3D removes the selected window", len(w.room.windows) == nw - 1)
    w._on_selection(("listener", 0))
    _key(floor, QtCore.Qt.Key_Escape)
    ok &= _ok("Esc deselects", w._selected == ("none", -1))
    w.canvas.set_tool("speaker")
    _key(floor, QtCore.Qt.Key_Escape)
    ok &= _ok("Esc cancels a placement tool", w.canvas.tool == "select" and w.palette.buttons["select"].isChecked())
    _key(floor, QtCore.Qt.Key_W, "w")
    ok &= _ok("tool letter keys pick tools", w.canvas.tool == "window" and w.palette.buttons["window"].isChecked())
    _key(floor, QtCore.Qt.Key_Escape)
    w._on_selection(("speaker", 0))
    name = w.inspector.speaker.ed_name
    name.setFocus()
    nn = len(w.room.speakers)
    _key(name, QtCore.Qt.Key_Delete)
    ok &= _ok("Delete while typing in a field does not delete the speaker", len(w.room.speakers) == nn)
    w._new_house()
    return ok


def test_canvas_resize_keeps_contents_inside(M, w) -> bool:
    floor = w.canvas.floor
    r = w.room
    r.speakers[0].x = r.width - 0.2
    floor._resize_edge = "e"
    floor._apply_resize(2.5, 0.0)
    inside = all(0.0 < s.x < r.width and 0.0 < s.z < r.depth for s in r.speakers)
    ok = _ok("dragging the east wall in clamps speakers inside", abs(r.width - 2.5) < 1e-9 and inside)
    x0 = r.listener.x
    floor._resize_edge = "w"
    floor._apply_resize(-1.0, 0.0)
    ok &= _ok("the west wall resizes too (contents shift with it)",
              abs(r.width - 3.5) < 1e-9 and abs(r.listener.x - (x0 + 1.0)) < 1e-6)
    floor._resize_edge = None
    w._new_house()
    return ok


def test_play_stop(M, w) -> bool:
    t = w.transport
    t.cmb_output.setCurrentIndex(0)
    t.btn_play.click()
    ok = _ok("Play (You) starts headphones", _CALLS["start_headphones"] >= 1 and w._play_mode == "headphones"
             and t.btn_play.text() == "Stop")
    stops = _CALLS["stop_all"]
    t.btn_play.click()
    ok &= _ok("pressing it again stops", _CALLS["stop_all"] > stops and w._play_mode is None
              and "Stopped" in t.lbl_live.text() and t.btn_play.text() == "Play")
    w.canvas.floor.setFocus()
    _key(w.canvas.floor, QtCore.Qt.Key_Space, " ")
    ok &= _ok("Space plays", w._play_mode == "headphones")
    _key(w.canvas.floor, QtCore.Qt.Key_Space, " ")
    ok &= _ok("Space stops", w._play_mode is None)
    ok &= _ok("headphones picker shows for You", not t.cmb_headphones.isHidden())
    t.cmb_output.setCurrentIndex(t.cmb_output.findData("multi"))
    ok &= _ok("headphones picker hides for Speakers only", t.cmb_headphones.isHidden())
    t.cmb_output.setCurrentIndex(0)
    return ok


def test_copy_sound_settings(M, w) -> bool:
    from app.models import settings_schema as S
    w._copy_sound_settings()
    data = json.loads(QtWidgets.QApplication.clipboard().text())
    return _ok("Copy sound settings (JSON) has every key",
               all(s.key in data for s in S.SETTINGS) and "master_volume" in data)


def test_user_copy_of_builtin(M, w) -> bool:
    """A user preset with a built-in's values stays highlighted and deletable."""
    from app.models import presets as P
    d = w.dock
    d.on_mix_preset(d.cmb_mix_preset.findData("Recommended"))
    d.ask_preset_name = lambda kind: "Mine too"
    d.confirm = lambda *a: True
    d.save_preset("mix")
    ok = _ok("saved copy of a built-in is the selected mix",
             d.cmb_mix_preset.currentData() == "Mine too" and d.btn_mix_del_preset.isEnabled(),
             str(d.cmb_mix_preset.currentData()))
    d.on_mix_preset(d.cmb_mix_preset.findData("Mine too"))
    ok &= _ok("picking the copy keeps it selected", d.cmb_mix_preset.currentData() == "Mine too")
    d.delete_preset("mix")
    ok &= _ok("the copy can be deleted", not P.user("mix") and d.cmb_mix_preset.currentData() == "Recommended")
    return ok


def test_preset_slug_clash_asks(M, w) -> bool:
    from app.models import presets as P
    d = w.dock
    asked = []
    d.confirm = lambda *a: asked.append(a) or False
    d.ask_preset_name = lambda kind: "Rain!"
    d.save_preset("weather")
    n0 = len(asked)
    d.ask_preset_name = lambda kind: "Rain?"
    d.save_preset("weather")
    names = [p.name for p in P.user("weather")]
    ok = _ok("same-file preset name asks before replacing", len(asked) == n0 + 1 and names == ["Rain!"],
             str(names))
    ok &= _ok("your own weather chips are marked", "(yours)" in d.weather_chips.buttons()["Rain!"].text())
    d.confirm = lambda *a: True
    d.on_weather_chip("Rain!")
    d.delete_preset("weather")
    return ok


def test_output_switch_failure_reverts(M, w) -> bool:
    """Switching to Speakers with no device-connected speaker keeps the bar
    honest: the combo goes back to what is still playing."""
    t = w.transport
    for s in w.room.speakers:
        s.audio_device = None
    t.cmb_output.setCurrentIndex(0)
    t.btn_play.click()
    playing = w._play_mode == "headphones" and "Playing" in t.lbl_live.text()
    t.cmb_output.setCurrentIndex(t.cmb_output.findData("multi"))
    ok = _ok("failed output switch puts the combo back",
             playing and w._play_mode == "headphones" and t.cmb_output.currentData() == "headphones"
             and "You (headphones)" in t.lbl_live.text(), t.lbl_live.text())
    ok &= _ok("…and points you at a speaker to connect", w._selected == ("speaker", 0))
    t.stop()
    return ok


def test_connect_speakers_prompt_opens_design(M, w) -> bool:
    """In Listen the inspector is hidden, so the "connect your speakers"
    prompt must bring Design (and the speaker's Plays-on picker) back."""
    t = w.transport
    for s in w.room.speakers:
        s.audio_device = None
    w.set_mode("listen")
    t.cmb_output.setCurrentIndex(t.cmb_output.findData("multi"))
    t.btn_play.click()
    ok = _ok("Play on Speakers with none connected switches to Design",
             w.mode() == "design" and w._selected == ("speaker", 0) and w.inspector.isVisibleTo(w)
             and w._play_mode is None, f"{w.mode()} {w._selected}")
    t.cmb_output.setCurrentIndex(0)
    w._on_selection(("none", -1))
    return ok


def test_name_edit_stays_with_its_object(M, w) -> bool:
    """A half-typed name / notes belongs to the object it was typed for, even
    when the selection changes from the (focus-less) Objects list or by code."""
    w._new_house()
    _settle()
    r = w.room
    wp, sp = w.inspector.window_page, w.inspector.speaker
    names0 = [x.name for x in r.windows]
    w._on_selection(("window", 0))
    wp.ed_name.setFocus()
    _settle(1)
    wp.ed_name.setText("RENAMED_A")
    w._on_selection(("window", 1))           # e.g. an Objects-list click
    w.canvas.floor.setFocus()
    _settle(1)
    ok = _ok("typed window name goes to the window it was typed for",
             [x.name for x in r.windows] == ["RENAMED_A"] + names0[1:] and wp.ed_name.text() == names0[1],
             str([x.name for x in r.windows]))
    w._on_selection(("window", 0))
    wp.ed_name.setFocus()
    _settle(1)
    w._on_selection(("window", 1))            # focus only, nothing typed
    w.canvas.floor.setFocus()
    _settle(1)
    ok &= _ok("switching with an untouched name field renames nothing",
              [x.name for x in r.windows] == ["RENAMED_A"] + names0[1:], str([x.name for x in r.windows]))
    snames = [x.name for x in r.speakers]
    notes1 = r.speakers[1].notes
    w._on_selection(("speaker", 0))
    sp.ed_notes.setFocus()
    _settle(1)
    sp.ed_notes.setText("by the sofa")
    w._on_selection(("speaker", 1))
    _settle(1)
    ok &= _ok("speaker notes stay with their speaker",
              r.speakers[0].notes == "by the sofa" and r.speakers[1].notes == notes1
              and [x.name for x in r.speakers] == snames and sp.ed_notes.text() == notes1,
              f"{r.speakers[0].notes!r} / {r.speakers[1].notes!r} / field {sp.ed_notes.text()!r}")
    w._on_selection(("speaker", 0))
    sp.ed_name.setFocus()
    _settle(1)
    sp.ed_name.setText("Sofa")
    n = len(r.speakers)
    sp.btn_remove.click()  # the name commits to speaker 0 first, then speaker 0 goes
    ok &= _ok("Remove commits the name before the list shifts",
              len(r.speakers) == n - 1 and [x.name for x in r.speakers] == snames[1:],
              str([x.name for x in r.speakers]))
    # Ctrl+S with the remembered path saves what is being typed
    path = os.path.join(_PRESET_TMP, "save_test.json")
    w.ctx.path = path
    w._on_selection(("window", 0))
    wp.ed_name.setFocus()
    _settle(1)
    wp.ed_name.setText("Typed then saved")
    w._save()
    with open(path, encoding="utf-8") as fh:
        saved = json.load(fh)
    ok &= _ok("Save includes a name still being typed, and stays clean",
              saved["windows"][0]["name"] == "Typed then saved" and not w.ctx.dirty)
    w.ctx.path = None
    w._new_house()
    return ok


def test_space_after_clicking_a_button(M, w) -> bool:
    """Clicking a panel button must not keep focus, or Space presses it again."""
    d = w.dock
    t = w.transport
    t.cmb_output.setCurrentIndex(0)
    w.canvas.floor.setFocus()
    btn = d._reset_buttons["mix"]
    QtTest.QTest.mouseClick(btn, QtCore.Qt.LeftButton)
    _settle(1)
    focus = QtWidgets.QApplication.focusWidget()
    ok = _ok("dock / inspector buttons do not take focus on click", focus is not btn, type(focus).__name__)
    _key(focus if focus is not None else w, QtCore.Qt.Key_Space, " ")
    ok &= _ok("Space then plays instead of re-pressing the button", w._play_mode == "headphones")
    t.stop(quiet=True)
    from app.ui.studio.controls import ToggleSwitch
    sw = d.findChildren(ToggleSwitch)[0]
    ok &= _ok("switches do not take focus on click either", sw.focusPolicy() == QtCore.Qt.TabFocus)
    return ok


def test_chrome_and_annotations(M, w) -> bool:
    """The plan never fits under the folded Objects pill; Listen hides the 3D
    editing marks; Spread evenly lives on the House page."""
    from app.ui.theme import C, T
    c = w.canvas
    w.set_mode("design")
    w.objects.set_collapsed(True)
    _settle(1)
    left = c.floor._insets.left() if hasattr(c.floor, "_insets") else None
    edge = w.objects.geometry().right()
    ok = _ok("folded Objects pill still reserves its width on the plan",
             left is not None and left >= edge, f"inset={left} pill_right={edge}")
    w.objects.set_collapsed(False)
    w.set_mode("listen")
    ok &= _ok("Listen hides hinge bars / arrows in 3D", c.view3d._annotations is False)
    w.set_mode("design")
    ok &= _ok("Design shows them again", c.view3d._annotations is True)
    ok &= _ok("Spread evenly is a House setting, not a single speaker's",
              hasattr(w.inspector.house, "btn_even") and not hasattr(w.inspector.speaker, "btn_even"))
    ok &= _ok("no-device status uses the warning colour on the plan too", C.warn_text == T.warning)
    return ok


def test_play_error_shows_stopped(M, w) -> bool:
    from app.audio.spatial_engine import SpatialRainEngine
    orig = SpatialRainEngine.start_headphones

    def fail(self, *a, **k):
        raise RuntimeError("no audio device")

    SpatialRainEngine.start_headphones = fail
    try:
        w.transport.cmb_output.setCurrentIndex(0)
        w.transport.btn_play.click()
    finally:
        SpatialRainEngine.start_headphones = orig
    return _ok("a playback error leaves the bar on Stopped",
               w._play_mode is None and "Stopped" in w.transport.lbl_live.text(), w.transport.lbl_live.text())


def test_material_hints_follow_role(M, w) -> bool:
    house = w.inspector.house
    house.cmb_roof.setCurrentText("Glass Roof")
    house.cmb_wall.setCurrentText("Tin Wall")
    return _ok("hints match the field they sit under",
               house.lbl_roof_hint.text().startswith("Glass roof")
               and house.lbl_wall_hint.text().startswith("Tin walls"),
               f"{house.lbl_roof_hint.text()} / {house.lbl_wall_hint.text()}")


# ----- phones -----

def _wait(cond, timeout: float = 10.0) -> bool:
    import time
    app = QtWidgets.QApplication.instance()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if cond():
            return True
        QtCore.QThread.msleep(20)
    app.processEvents()
    return bool(cond())


def _phone_dialog(w):
    if w._phone_dialog is None or not w._phone_dialog.isVisible():
        w.app_bar.btn_phone.click()
        _settle(1)
    return w._phone_dialog


def test_phone_button_and_server(M, w) -> bool:
    """The app bar has a Phone button that fits at 1280 px; it opens the Phones
    dialog, whose switch starts the server with a scannable code and link."""
    import urllib.request
    from app.ui.studio.phone_dialog import PhoneDialog
    bar = w.app_bar
    w.set_mode("design")
    _settle(1)
    b = bar.btn_phone
    geo = [x.geometry() for x in (bar.btn_weather, b, bar.btn_file, bar.btn_gear)]
    in_order = all(a.right() < c.left() for a, c in zip(geo, geo[1:]))
    inside = all(g.left() >= 0 and g.right() <= bar.width() for g in geo)
    fits = b.width() >= b.sizeHint().width() and bar.btn_weather.width() >= bar.btn_weather.sizeHint().width()
    ok = _ok("Phone button sits between weather and File, unclipped at 1280 px",
             b.isVisible() and b.text() == "Phone" and in_order and inside and fits,
             f"bar={bar.width()} {[(g.left(), g.right()) for g in geo]}")
    ok &= _ok("off by default (no remembered choice)", w.remote.status == "off" and b.badge_count() == 0)
    dlg = _phone_dialog(w)
    ok &= _ok("Phone button opens the Phones dialog", isinstance(dlg, PhoneDialog) and dlg.isVisible()
              and not dlg.join.isVisibleTo(dlg) and not dlg.sw_on.isChecked())
    w.remote.port = 0                      # ephemeral port for the test
    dlg.sw_on.setChecked(True)
    up = _wait(lambda: w.remote.status == "on")
    srv = w.remote.server
    ok &= _ok("the switch starts the phone server", up and srv is not None and srv.running and srv.port > 0,
              f"{w.remote.status} {w.remote.error}")
    pm = dlg.qr_pixmap()
    ok &= _ok("QR code pixmap is shown", pm is not None and pm.width() >= 120 and dlg.join.isVisibleTo(dlg),
              str(pm.size() if pm else None))
    if pm is not None:
        img = pm.toImage()
        corner = QtGui.QColor(img.pixel(1, 1))
        ok &= _ok("QR has a white quiet zone", corner.lightness() == 255, corner.name())
    url = dlg.ed_url.text()
    ok &= _ok("link field shows the URL with its key", url.startswith("http://") and f"?k={srv.key}" in url
              and dlg.ed_url.isReadOnly(), url)
    dlg.copy_url()
    ok &= _ok("Copy puts the link on the clipboard", QtWidgets.QApplication.clipboard().text() == url)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{srv.port}/?k={srv.key}", timeout=5) as r:
            status = r.status
    except Exception as e:  # noqa: BLE001
        status = repr(e)
    ok &= _ok("the page is served with the key", status == 200, str(status))
    ok &= _ok("app bar shows sharing is on", b.property("on") is True)
    with open(os.environ["RAINROOM_REMOTE_CONFIG"], encoding="utf-8") as fh:
        saved = json.load(fh)
    ok &= _ok("the choice + key are remembered outside the house file",
              saved.get("enabled") is True and saved.get("key") == srv.key, str(saved))
    return ok


def test_phone_weather_reaches_desktop(M, w) -> bool:
    """A phone's weather edit lands in the desktop Room, the dock and the app bar."""
    import threading
    from app.models import settings_schema as S
    from app.ui.studio.weather_dock import _display, rain_rate_text
    hub, d = w.remote.hub, w.dock
    d.on_weather_chip("Steady rain")
    w.remote.wait_idle()
    _settle(1)
    th = threading.Thread(target=hub.weather_from_phone, args=("droplet_density", 0.91))
    th.start()
    th.join(5)
    got = _wait(lambda: abs(w.room.droplet_density - 0.91) < 1e-6)
    s = S.by_key("droplet_density")
    ok = _ok("phone Amount → desktop Room", got, str(w.room.droplet_density))
    ok &= _ok("…and the dock slider + caption",
              d._setting_inputs["droplet_density"].value() == M._ticks(s, 0.91)
              and d._setting_values["droplet_density"].text() == _display(s, 0.91))
    want = f"{d.current_weather() or 'Custom'} · {rain_rate_text(w.room)}"
    ok &= _ok("…and the app-bar weather button", w.app_bar.weather_text() == want, w.app_bar.weather_text())
    th = threading.Thread(target=hub.apply_preset, args=("Storm",))
    th.start()
    th.join(5)
    got = _wait(lambda: d.current_weather() == "Storm")
    ok &= _ok("phone preset → dock chip + app bar", got and w.app_bar.weather_text().startswith("Storm · "),
              w.app_bar.weather_text())
    w.remote.wait_idle()
    ok &= _ok("no echo: hub keeps the phone's preset", hub.preset() == "Storm", str(hub.preset()))
    return ok


def test_phone_edits_never_sent_back(M, w) -> bool:
    """After a phone edit the bridge only tells the hub the dock's preset name:
    re-sending the Room's values from a busy UI thread could move other phones'
    sliders backwards."""
    import threading
    hub = w.remote.hub
    calls = []
    orig = hub.set_shared

    def recording(values, preset_name=None):
        calls.append((dict(values), preset_name))
        return orig(values, preset_name)

    hub.set_shared = recording
    try:
        for v in (0.31, 0.32, 0.33, 0.34):
            th = threading.Thread(target=hub.weather_from_phone, args=("droplet_density", v))
            th.start()
            th.join(5)
        _wait(lambda: abs(w.room.droplet_density - 0.34) < 1e-6)
        _settle(1)
        w.remote.wait_idle()
        sent_values = [c for c in calls if c[0]]
        ok = _ok("phone edits are not pushed back as values", not sent_values, str(sent_values[:3]))
        ok &= _ok("…and the hub keeps the newest phone value",
                  abs(hub.shared_snapshot()["droplet_density"] - 0.34) < 1e-9)
    finally:
        hub.set_shared = orig
    return ok


def test_phone_settings_from_another_pc(M, w) -> bool:
    """A configs/remote.json made on another PC (e.g. shipped in a zip) never
    turns sharing on by itself or reuses its key."""
    import tempfile
    from app.remote.zones import ZoneHub
    from app.ui.studio.remote_bridge import RemoteBridge, machine_id
    d = tempfile.mkdtemp(prefix="rr_remote_cfg_")
    ok = True
    for machine, want in (("0123456789abcdef", False), (machine_id(), True)):
        p = os.path.join(d, f"remote_{want}.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"enabled": True, "key": "Shipped1234", "port": 8790, "machine": machine}, fh)
        hub = ZoneHub(zones_dir=os.path.join(d, "zones"), device_lister=lambda: [])
        b = RemoteBridge(w.ctx, settings_path=p, hub=hub)
        try:
            got = (b.remembered_on(), b._key() == "Shipped1234", b.port)
            ok &= _ok(f"remote.json from {'this' if want else 'another'} PC: sharing {'remembered' if want else 'stays off'}",
                      got == (want, want, 8790), str(got))
        finally:
            b.shutdown()
            b.wait_idle()
    return ok


def test_desktop_slider_reaches_hub(M, w) -> bool:
    """Desktop slider moves reach the hub snapshot, throttled while dragging."""
    hub, d = w.remote.hub, w.dock
    calls = []
    orig = hub.set_shared

    def counting(values, preset_name=None):
        calls.append(values.get("droplet_density"))
        return orig(values, preset_name)

    hub.set_shared = counting
    try:
        sld = d._setting_inputs["droplet_density"]
        start = sld.value()
        for i in range(1, 21):               # a quick drag: 20 steps in a few ms
            sld.setValue(start - i)
        _settle(1)
        _wait(lambda: not w.remote._push_timer.isActive(), 2)
        w.remote.wait_idle()
        snap = hub.shared_snapshot()
        ok = _ok("a desktop slider reaches the hub snapshot",
                 abs(snap["droplet_density"] - w.room.droplet_density) < 1e-9, str(snap["droplet_density"]))
        ok &= _ok("pushes are throttled while dragging", 1 <= len(calls) <= 3, str(len(calls)))
        d._setting_inputs["wind_vary_speed"].setChecked(not w.room.wind_vary_speed)
        _wait(lambda: not w.remote._push_timer.isActive(), 2)
        w.remote.wait_idle()
        ok &= _ok("toggles reach it too", hub.shared_snapshot()["wind_vary_speed"] == bool(w.room.wind_vary_speed))
    finally:
        hub.set_shared = orig
    return ok


def test_phone_list_badge_and_stop(M, w) -> bool:
    """Connected phones show as a badge and in the dialog; a phone playing on a
    PC output can be stopped from the dialog."""
    hub = w.remote.hub
    dlg = _phone_dialog(w)
    z = hub.zone_for("smoketest-phone")
    sink = lambda _obj: None  # noqa: E731
    z.attach(sink, "Test phone")
    ok = _ok("badge counts connected phones", _wait(lambda: w.app_bar.btn_phone.badge_count() == 1))
    rows = dlg.phone_rows()
    ok &= _ok("dialog lists the phone with its room", len(rows) == 1 and rows[0].lbl_name.text() == "Bedroom"
              and "Test phone" in rows[0].lbl_info.text() and rows[0].btn_stop is None
              and dlg.lbl_empty.isHidden(), rows[0].lbl_info.text() if rows else "none")
    z.set_output("pc", None).result(5)       # engine start is stubbed: no device opens
    _wait(lambda: any(r.btn_stop is not None for r in dlg.phone_rows()))
    rows = dlg.phone_rows()
    ok &= _ok("a phone playing on this PC gets Stop", len(rows) == 1 and rows[0].btn_stop is not None
              and "Playing on" in rows[0].lbl_info.text(), rows[0].lbl_info.text() if rows else "none")
    z.detach(sink)
    _wait(lambda: w.app_bar.btn_phone.badge_count() == 0)
    rows = dlg.phone_rows()
    ok &= _ok("…and stays listed after the phone leaves", len(rows) == 1 and rows[0].btn_stop is not None
              and w.app_bar.btn_phone.badge_count() == 0)
    rows[0].btn_stop.click()
    gone = _wait(lambda: z.mode == "off" and not dlg.phone_rows())
    ok &= _ok("Stop turns it off and the list empties", gone and not dlg.lbl_empty.isHidden())
    return ok


def test_phone_new_link_and_switch_off(M, w) -> bool:
    dlg = _phone_dialog(w)
    old = w.remote.server.key
    dlg.btn_new.click()
    changed = _wait(lambda: w.remote.server.key != old and old not in dlg.ed_url.text())
    ok = _ok("New link changes the key and the shown link",
             changed and w.remote.server.key in dlg.ed_url.text(), dlg.ed_url.text())
    dlg.sw_on.setChecked(False)
    down = _wait(lambda: w.remote.status == "off")
    ok &= _ok("the switch stops the server", down and not w.remote.server.running
              and not dlg.join.isVisibleTo(dlg) and w.app_bar.btn_phone.property("on") is False)
    _settle(1)
    ok &= _ok("switched off, the dialog shrinks to its how-to", dlg.height() < 480, f"{dlg.height()} px")
    with open(os.environ["RAINROOM_REMOTE_CONFIG"], encoding="utf-8") as fh:
        ok &= _ok("off is remembered", json.load(fh).get("enabled") is False)
    dlg.sw_on.setChecked(True)
    ok &= _ok("it starts again", _wait(lambda: w.remote.status == "on"))
    _settle(1)
    ok &= _ok("…and the dialog grows back for the code and phones", dlg.height() >= 560, f"{dlg.height()} px")
    dlg.close()
    return ok


def test_app_close_stops_phone_server(M, w) -> bool:
    """Run last: closing the window stops the server without blocking."""
    import time
    if w.remote.status != "on":
        w.remote.set_enabled(True, remember=False)
        _wait(lambda: w.remote.status == "on")
    srv = w.remote.server
    was = srv is not None and srv.running
    t0 = time.perf_counter()
    w.close()
    took = time.perf_counter() - t0
    w.remote.wait_idle()
    ok = _ok("closing the window stops the phone server",
             was and not srv.running and w.remote._closing, f"was={was} running={srv.running if srv else None}")
    ok &= _ok("…without blocking the UI", took < 0.5, f"{took * 1000:.0f} ms")
    return ok


def main() -> int:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    _mute_dialogs()
    M, w = _make_main()
    w.resize(1280, 720)
    w.show()
    _settle()
    print("UI smoke tests")
    tests = (
        test_layout_and_transport, test_mode_switch, test_volume, test_every_setting_has_one_widget,
        test_weather_presets, test_weather_quick_pick, test_mix_presets_and_user_presets, test_reset_buttons,
        test_apply_room_blocks_footprint, test_thickness_placeholders, test_example_houses, test_house_presets,
        test_inspector_follows_selection, test_window_buttons, test_add_speaker_defaults,
        test_delete_and_escape, test_canvas_resize_keeps_contents_inside, test_play_stop,
        test_copy_sound_settings, test_user_copy_of_builtin, test_preset_slug_clash_asks,
        test_output_switch_failure_reverts, test_play_error_shows_stopped, test_material_hints_follow_role,
        test_amount_caption_uses_preset_words, test_connect_speakers_prompt_opens_design,
        test_name_edit_stays_with_its_object, test_space_after_clicking_a_button, test_chrome_and_annotations,
        test_phone_button_and_server, test_phone_weather_reaches_desktop, test_phone_edits_never_sent_back,
        test_phone_settings_from_another_pc, test_desktop_slider_reaches_hub,
        test_phone_list_badge_and_stop, test_phone_new_link_and_switch_off,
        test_app_close_stops_phone_server,      # keep last: it closes the window
    )
    passed = 0
    for t in tests:
        try:
            ok = t(M, w)
        except Exception as e:  # report and keep going
            import traceback
            traceback.print_exc()
            ok = _ok(t.__name__, False, repr(e))
        passed += bool(ok)
    w.close()
    app.processEvents()
    import shutil
    shutil.rmtree(_PRESET_TMP, ignore_errors=True)
    print(f"{passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
