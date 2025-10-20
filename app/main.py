
import sys, os, logging
from PySide6 import QtCore, QtGui, QtWidgets
from app.models.room import Room, Window, Speaker, Headphones
from app.models.materials import MATERIAL_PRESETS
from app.utils.persistence import load_room, save_room
from app.audio.engine import RainEngine
from app.audio.multidevice import MultiDeviceEngine
from app.tools.audio_test import run_audio_test_single
from app.logging_setup import setup_logging

LOG_PATH = setup_logging()

GL_AVAILABLE = True
try:
    from app.graphics.glview import GLRoomView, MODE_MOVE, MODE_ROT, MODE_SCALE
except Exception as _e:
    GL_AVAILABLE = False
    logging.getLogger('init').exception('OpenGL disabled: %s', _e)

class Main(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("RainRoom3D — Single-WAV Test Export")
        self.setMinimumSize(1280, 840)
        self.room = Room(4.0, 2.6, 3.2)
        self.engine = RainEngine(self.room)
        self.multi = None
        self.selected = ("none",-1)

        self.gl = GLRoomView() if (GL_AVAILABLE and os.getenv('RAINROOM_NOGPU','0')!='1') else None
        if self.gl:
            self.gl.set_room(self.room)
            self.gl.selectionChanged.connect(self.on_select)

        # Side panel
        side = QtWidgets.QWidget(); side_layout = QtWidgets.QFormLayout(side)
        self.headphones_chk = QtWidgets.QCheckBox("Use Headphones (single-output binaural)")
        self.headphones_chk.stateChanged.connect(lambda s:setattr(self.room, "headphones_mode", s==QtCore.Qt.Checked))

        # Device routing widgets (visible when a Speaker is selected)
        self.dev_label = QtWidgets.QLabel("Output Device for Speaker:")
        self.dev_combo = QtWidgets.QComboBox()
        self.dev_combo.currentIndexChanged.connect(self.on_assign_device)

        # Devices
        try:
            self.multi = MultiDeviceEngine(self.room)
            self.devices = self.multi.devices
        except Exception:
            self.multi = None; self.devices = []
        self.dev_combo.clear()
        self.dev_combo.addItem("— Unassigned —", userData=None)
        for d in self.devices:
            self.dev_combo.addItem(f"[{d['index']}] {d['name']}", userData=d['index'])

        # Controls
        self.btn_play = QtWidgets.QPushButton("▶ Start (single output)"); self.btn_play.clicked.connect(self.start_single)
        self.btn_stop = QtWidgets.QPushButton("■ Stop"); self.btn_stop.clicked.connect(self.stop_all)
        self.btn_play_multi = QtWidgets.QPushButton("▶ Start Multi-Output"); self.btn_play_multi.clicked.connect(self.start_multi)

        # Test Audio button (single WAV per material)
        self.btn_test_audio = QtWidgets.QPushButton("Test Audio (render & analyze)")
        self.btn_test_audio.clicked.connect(self.on_test_audio_single)

        # Spawn buttons
        self.btn_add_spk = QtWidgets.QPushButton("Add Speaker"); self.btn_add_spk.clicked.connect(self.add_speaker)
        self.btn_add_win = QtWidgets.QPushButton("Add Window"); self.btn_add_win.clicked.connect(self.add_window)
        self.btn_add_hp  = QtWidgets.QPushButton("Add Headphones"); self.btn_add_hp.clicked.connect(self.add_headphones)

        grid = QtWidgets.QGridLayout(); central = QtWidgets.QWidget(); central.setLayout(grid)
        if self.gl: grid.addWidget(self.gl, 0, 0, 8, 2)
        grid.addWidget(side, 0, 2, 1, 1)
        grid.addWidget(self.btn_play, 1, 2)
        grid.addWidget(self.btn_play_multi, 2, 2)
        grid.addWidget(self.btn_stop, 3, 2)
        grid.addWidget(self.btn_test_audio, 4, 2)
        grid.addWidget(self.btn_add_spk, 5, 2)
        grid.addWidget(self.btn_add_win, 6, 2)
        grid.addWidget(self.btn_add_hp, 7, 2)
        self.setCentralWidget(central)

        self.dev_label.setVisible(False); self.dev_combo.setVisible(False)
        side_layout.addRow(self.headphones_chk)
        side_layout.addRow(self.dev_label, self.dev_combo)
        self.statusBar().showMessage("Click Test Audio to export ONE WAV per material in logs/audio_tests_single/.")

    # ---- Selection ----
    def on_select(self, sel):
        self.selected = sel
        is_spk = (sel[0] == "speaker" and 0 <= sel[1] < len(self.room.speakers))
        self.dev_label.setVisible(is_spk); self.dev_combo.setVisible(is_spk)
        if is_spk:
            spk = self.room.speakers[sel[1]]
            current = getattr(spk, "audio_device", None)
            idx = 0
            for i in range(1, self.dev_combo.count()):
                if self.dev_combo.itemData(i) == current:
                    idx = i; break
            self.dev_combo.blockSignals(True)
            self.dev_combo.setCurrentIndex(idx)
            self.dev_combo.blockSignals(False)

    def on_assign_device(self, _idx):
        if not (self.selected[0] == "speaker" and 0 <= self.selected[1] < len(self.room.speakers)):
            return
        spk = self.room.speakers[self.selected[1]]
        dev_index = self.dev_combo.currentData()
        try:
            if self.multi is None:
                self.multi = MultiDeviceEngine(self.room)
            self.multi.set_speaker_device(spk, dev_index)
            self.statusBar().showMessage(f"Speaker routed to device: {dev_index if dev_index is not None else 'Unassigned'}")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Routing Error", str(e))

    # ---- Playback ----
    def start_single(self):
        try:
            self.engine.start()
            self.statusBar().showMessage(f"Single-output started @ {self.engine.samplerate} Hz")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Audio error", str(e))

    def start_multi(self):
        self.stop_all()
        try:
            if self.multi is None:
                self.multi = MultiDeviceEngine(self.room)
            self.multi.start()
            self.statusBar().showMessage("Multi-output started (per-speaker devices).")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Multi-output error", str(e))

    def stop_all(self):
        try:
            self.engine.stop()
        except Exception:
            pass
        try:
            if self.multi: self.multi.stop()
        except Exception:
            pass
        self.statusBar().showMessage("Stopped.")

    # ---- Test Audio: ONE WAV per material ----
    def on_test_audio_single(self):
        self.engine.stop()
        folder, manifest_csv = run_audio_test_single(self, self.room)
        QtWidgets.QMessageBox.information(self, "Audio Test Exported",
            f"Saved one WAV per material to:\n{folder}\n\nManifest: {manifest_csv}\n\nSend me those WAVs here and I'll analyze them.")
        self.statusBar().showMessage("Test Audio exported.")

    # ---- Spawning helpers ----
    def add_speaker(self):
        s = Speaker(name=f"S{len(self.room.speakers)+1}", x=self.room.width*0.25+len(self.room.speakers)*0.4, z=self.room.depth-0.5, y=1.1)
        self.room.speakers.append(s); self.gl and self.gl.update()

    def add_window(self):
        w = Window(name=f"Win{len(self.room.windows)+1}", x=min(self.room.width-1.0, 0.5*len(self.room.windows)+0.5), z=self.room.depth-0.2, width=0.9, height=1.0, open=0.7)
        self.room.windows.append(w); self.gl and self.gl.update()

    def add_headphones(self):
        hp = Headphones(name=f"HP{len(self.room.headphones_items)+1}", x=self.room.width*0.75, z=self.room.depth-0.5, y=1.2)
        self.room.headphones_items.append(hp); self.gl and self.gl.update()

def main():
    app = QtWidgets.QApplication(sys.argv)
    w = Main(); w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
