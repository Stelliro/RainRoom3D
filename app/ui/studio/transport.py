"""Transport bar: Play/Stop, status, output choice, headphones, volume.

It also owns playback: which engine start call runs for each output, the
"no speakers yet" checks, and switching output while rain plays.
"""

from __future__ import annotations

from typing import Optional

from PySide6 import QtCore, QtWidgets

from app.models import settings_schema as schema
from app.ui.studio.context import StudioContext
from app.ui.studio.controls import ElidedLabel, _from_ticks, _ticks, block, combo, label, set_prop, slider, vsep
from app.ui.studio.icons import icon
from app.ui.studio.page_speaker import fill_device_combo
from app.ui.theme import SP, T

# output label, play mode, tooltip
PLAY_OUTPUTS = (
    ("You (headphones)", "headphones",
     "Binaural rain heard at the blue “You” marker — best on headphones."),
    ("Speakers", "multi",
     "Each mapped room speaker plays what it would hear at its spot."),
    ("You + speakers", "all",
     "Headphones and the mapped room speakers together."),
)


class _StatusPill(QtWidgets.QFrame):
    """A coloured dot + the live state text ("Stopped" / "Playing · …")."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 14, 0)
        lay.setSpacing(8)
        self.dot = QtWidgets.QLabel()
        self.dot.setFixedSize(8, 8)
        self.text = QtWidgets.QLabel("Stopped")
        lay.addWidget(self.dot)
        lay.addWidget(self.text)
        self.setFixedHeight(30)
        self.set_state("idle")

    def set_state(self, state: str) -> None:
        col = {"playing": T.success, "error": T.danger}.get(state, T.text3)
        bg = {"playing": T.success_soft, "error": T.danger_soft}.get(state, T.overlay)
        self.dot.setStyleSheet(f"background: {col}; border-radius: 4px;")
        self.setStyleSheet(f"QFrame {{ background: {bg}; border: 1px solid {T.border}; border-radius: 15px; }}"
                           f"QLabel {{ border: none; background: transparent; color: "
                           f"{T.text if state == 'playing' else T.text2}; font-weight: 600; }}")
        self.dot.setStyleSheet(f"background: {col}; border-radius: 4px; border: none;")


class TransportBar(QtWidgets.QFrame):
    playStateChanged = QtCore.Signal(object)   # play mode or None

    def __init__(self, ctx: StudioContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("Transport")
        self.setFixedHeight(64)
        self._play_mode: Optional[str] = None
        self._msg_timer = QtCore.QTimer(self)
        self._msg_timer.setSingleShot(True)
        self._msg_timer.timeout.connect(lambda: self.lbl_msg.setText(""))
        # The window points this at "select the first speaker" etc.
        self.focus_speaker = lambda idx: None

        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(20, 0, 20, 0)
        lay.setSpacing(SP + 4)

        self.btn_play = QtWidgets.QPushButton("Play")
        self.btn_play.setObjectName("PlayBtn")
        self.btn_play.setProperty("playing", False)
        self.btn_play.setIconSize(QtCore.QSize(16, 16))
        self.btn_play.setMinimumWidth(104)
        self.btn_play.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_play.setFocusPolicy(QtCore.Qt.NoFocus)
        lay.addWidget(self.btn_play)

        self.pill = _StatusPill()
        self.lbl_live = self.pill.text
        lay.addWidget(self.pill)

        self.lbl_msg = ElidedLabel("")
        self.lbl_msg.setObjectName("MsgLabel")
        self.lbl_msg.setMinimumWidth(60)
        self.lbl_msg.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        lay.addWidget(self.lbl_msg, 1)

        lay.addWidget(label("Output", "FieldLabel"))
        self.cmb_output = combo(tip="Where the rain plays. Changing it while playing switches over.")
        for text, mode, tip in PLAY_OUTPUTS:
            self.cmb_output.addItem(text, userData=mode)
            self.cmb_output.setItemData(self.cmb_output.count() - 1, tip, QtCore.Qt.ToolTipRole)
        self.cmb_output.setMinimumWidth(184)
        lay.addWidget(self.cmb_output)

        self.hp_icon = QtWidgets.QLabel()
        self.hp_icon.setPixmap(icon("headphones", T.text2, 18).pixmap(18, 18))
        self.hp_icon.setToolTip("Your headphones")
        self.cmb_headphones = combo(tip="Which output your headphones are plugged into")
        self.cmb_headphones.setMinimumWidth(150)
        self.cmb_headphones.setMaximumWidth(240)
        lay.addWidget(self.hp_icon)
        lay.addWidget(self.cmb_headphones)

        lay.addWidget(vsep(28))
        vic = QtWidgets.QLabel()
        vic.setPixmap(icon("volume", T.text2, 18).pixmap(18, 18))
        vic.setToolTip(schema.VOLUME.label)
        lay.addWidget(vic)
        self.sld_volume = slider(0, _ticks(schema.VOLUME, schema.VOLUME.hi), schema.VOLUME.help)
        self.sld_volume.setFixedWidth(150)
        lay.addWidget(self.sld_volume)
        self.lbl_volume = label("", "SetValue")
        self.lbl_volume.setFixedWidth(40)
        lay.addWidget(self.lbl_volume)

        self.btn_play.clicked.connect(self.toggle)
        self.cmb_output.currentIndexChanged.connect(self._on_output_changed)
        self.cmb_headphones.currentIndexChanged.connect(
            lambda _i: self.ctx.set_listener_device(self.cmb_headphones.currentData()))
        self.sld_volume.valueChanged.connect(self._on_volume)
        ctx.message.connect(self.show_message)
        ctx.devicesChanged.connect(self.load_headphones)
        ctx.roomReplaced.connect(self._on_room_replaced)
        # Only a device change alters the headphones picker (moving / turning You does not).
        # Rebuilt on the next event-loop turn, through one restartable timer: the change may
        # come from this very combo, and several changes in a row need just one rebuild.
        self._hp_timer = QtCore.QTimer(self)
        self._hp_timer.setSingleShot(True)
        self._hp_timer.setInterval(0)
        self._hp_timer.timeout.connect(self.load_headphones)
        ctx.routingChanged.connect(lambda what: self._hp_timer.start() if what == "listener" else None)
        ctx.routingChanged.connect(self._on_routing_changed)
        self._set_live("Stopped", "idle")
        self._update_output_extras()

    def set_large(self, on: bool) -> None:
        """Listen mode: a bigger Play button."""
        set_prop(self.btn_play, "large", bool(on))
        self.btn_play.setMinimumWidth(128 if on else 104)

    # ----- state -----
    @property
    def play_mode(self) -> Optional[str]:
        return self._play_mode

    def show_message(self, text: str, ms: int = 4000) -> None:
        self.lbl_msg.setText(text)
        self.lbl_msg.setToolTip(text)
        self._msg_timer.start(max(1500, int(ms)) if ms else 6000)

    def _set_live(self, text: str, state: str, tip: str = "") -> None:
        self.lbl_live.setText(text)
        self.pill.set_state(state)
        self.pill.setToolTip(tip or text)

    def _announce(self, detail: str, full: str) -> None:
        """The pill already says what is playing: the message line only adds
        the detail (which device, how many speakers); the full sentence is the
        pill's tooltip."""
        self.pill.setToolTip(full)
        self.ctx.say(detail)

    def _set_button(self) -> None:
        playing = self._play_mode is not None
        self.btn_play.setText("Stop" if playing else "Play")
        self.btn_play.setIcon(icon("stop" if playing else "play", T.text if playing else T.accent_text, 16))
        self.btn_play.setToolTip("Stop all audio  (Space)" if playing else "Start the rain on the chosen output  (Space)")
        set_prop(self.btn_play, "playing", playing)

    def _update_output_extras(self) -> None:
        mode = self.cmb_output.currentData()
        show = mode in ("headphones", "all")
        self.hp_icon.setVisible(show)
        self.cmb_headphones.setVisible(show)
        self._set_button()

    def load_headphones(self) -> None:
        fill_device_combo(self.cmb_headphones, self.ctx, "System default",
                          getattr(self.ctx.room.listener, "audio_device", None))

    def _on_room_replaced(self) -> None:
        self.load_headphones()
        self.sync_volume()

    # ----- volume -----
    def _on_volume(self, t: int) -> None:
        v = schema.set(self.ctx.room, "master_volume", _from_ticks(schema.VOLUME, t))
        self.ctx.engine.set_volume(v)
        self.lbl_volume.setText(schema.VOLUME.display(v))
        self.ctx.set_dirty(True)

    def sync_volume(self) -> None:
        v = schema.get(self.ctx.room, "master_volume")
        self.ctx.engine.set_volume(v)
        with block(self.sld_volume):
            self.sld_volume.setValue(_ticks(schema.VOLUME, schema.VOLUME.clamp(v)))
        self.lbl_volume.setText(schema.VOLUME.display(v))

    # ----- playback -----
    def toggle(self) -> bool:
        if self._play_mode is not None:
            self.stop()
            return False
        return self.play()

    def play(self) -> bool:
        """Start rain on whichever output is picked. True if it started."""
        mode = self.cmb_output.currentData()
        return {"headphones": self._play_headphones, "multi": self._play_multi,
                "all": self._play_all}.get(mode, self._play_headphones)()

    def stop(self, quiet: bool = False) -> None:
        try:
            self.ctx.engine.stop_all()
        except Exception:
            pass
        was = self._play_mode
        self._play_mode = None
        self._set_live("Stopped", "idle")
        self._set_button()
        if not quiet:
            self.ctx.say("Audio stopped", 2500)
        if was is not None:
            self.playStateChanged.emit(None)

    def _on_output_changed(self, *_):
        self._update_output_extras()
        # Switching output while rain plays moves the rain over right away.
        if self._play_mode is None:
            return
        if not self.play() and self._play_mode is not None:
            # The new output could not start (e.g. no speaker has a device) and the
            # old one is still sounding: put the combo back so the bar says what you hear.
            self._show_output_mode(self._play_mode)

    def _on_routing_changed(self, what: str) -> None:
        # A new device for a playing output only takes effect on a fresh start.
        uses = {"headphones": ("listener",), "multi": ("speaker",), "all": ("listener", "speaker")}
        if self._play_mode is not None and what in uses.get(self._play_mode, ()):
            QtCore.QTimer.singleShot(0, self._restart)

    def _restart(self) -> None:
        if self._play_mode is None:
            return
        mode = self._play_mode
        if not self.play() and self._play_mode is not None:
            self._show_output_mode(mode)

    def _show_output_mode(self, mode: str) -> None:
        i = self.cmb_output.findData(mode)
        if i >= 0:
            with block(self.cmb_output):
                self.cmb_output.setCurrentIndex(i)
            self._update_output_extras()

    def _set_playing(self, mode: str) -> None:
        self._play_mode = mode
        i = self.cmb_output.findData(mode)
        text = self.cmb_output.itemText(i) if i >= 0 else mode
        self._set_live(f"Playing · {text}", "playing")
        self._set_button()
        self.playStateChanged.emit(mode)

    def _play_failed(self, e: Exception) -> bool:
        # stop_all already ran, so nothing is playing now — say so.
        self._play_mode = None
        self._set_live("Stopped", "idle")
        self._set_button()
        self.ctx.say(f"Could not start: {e}", 8000)
        QtWidgets.QMessageBox.critical(self.window(), "Playback error", str(e))
        self.playStateChanged.emit(None)
        return False

    def _ensure_speakers_for_multi(self) -> bool:
        """True if at least one room speaker is connected to a device."""
        room = self.ctx.room
        if room.assigned_speakers():
            return True
        if not room.speakers:
            ans = QtWidgets.QMessageBox.question(
                self.window(), "No room speakers",
                "This house has no speakers — only “You” (headphones).\n\n"
                "Place 3 speakers now so you can connect them to real outputs?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            )
            if ans != QtWidgets.QMessageBox.Yes:
                return False
            self.ctx.spread_speakers(3, ask=False)
        if not room.assigned_speakers():
            QtWidgets.QMessageBox.information(
                self.window(), "Connect your speakers",
                "Your speakers are not connected to a real device yet.\n\n"
                "1) Click a speaker in the view (or in Objects)\n"
                "2) Under “Plays on”, choose a real output (speakers, monitor, cable…)\n"
                "3) Press Test sound, then Play again\n\n"
                "Tip: do not map every speaker to your headphones unless you want that.",
            )
            if room.speakers:
                self.focus_speaker(0)
            return False
        return True

    def _play_headphones(self) -> bool:
        hp = getattr(self.ctx.room.listener, "audio_device", None)
        try:
            self.ctx.engine.stop_all()
            self.ctx.engine.room = self.ctx.room
            self.ctx.engine.start_headphones(headphones_device=hp)
        except Exception as e:
            return self._play_failed(e)
        self._set_playing("headphones")
        dev = self.ctx.device_label(hp)
        self._announce(f"Headphones: {dev}",
                       f"Playing on your headphones ({dev}); room speakers silent.")
        return True

    def _play_multi(self) -> bool:
        if not self._ensure_speakers_for_multi():
            return False
        try:
            self.ctx.engine.stop_all()
            self.ctx.engine.room = self.ctx.room
            self.ctx.engine.start(include_you=False)
        except Exception as e:
            return self._play_failed(e)
        self._set_playing("multi")
        n = len(self.ctx.room.assigned_speakers())
        self._announce(f"{n} room speaker(s)",
                       f"Playing on {n} speaker(s) only (not your headphones)")
        return True

    def _play_all(self) -> bool:
        if not self._ensure_speakers_for_multi():
            return False
        hp = getattr(self.ctx.room.listener, "audio_device", None)
        try:
            self.ctx.engine.stop_all()
            self.ctx.engine.room = self.ctx.room
            self.ctx.engine.start(include_you=True, headphones_device=hp)
        except Exception as e:
            return self._play_failed(e)
        self._set_playing("all")
        dev, n = self.ctx.device_label(hp), len(self.ctx.room.assigned_speakers())
        self._announce(f"Headphones: {dev} + {n} speaker(s)",
                       f"Playing on you ({dev}) + {n} speaker(s)")
        return True


__all__ = ["PLAY_OUTPUTS", "TransportBar"]
