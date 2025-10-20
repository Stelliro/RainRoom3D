
import os, time, csv
import numpy as np
from scipy.io import wavfile

from app.models.materials import MATERIAL_PRESETS
from app.audio.engine import RainEngine

def _list_material_names():
    names = [getattr(m, 'name', f'Material{i}') for i, m in enumerate(MATERIAL_PRESETS)]
    out = []
    for n in names:
        if n not in out:
            out.append(n)
    return out

def _slug(s: str) -> str:
    return ''.join(c if c.isalnum() else '_' for c in s).strip('_')

def run_audio_test_single(main_window, room, seconds: float = 8.0, samplerate: int = 48000):
    """Render exactly ONE WAV per rain sound (per material), using current room intensity and mode.
    Saves into logs/audio_tests_single/. Returns (folder, manifest_csv)."""
    out_dir = os.path.join(os.getcwd(), 'logs', 'audio_tests_single')
    os.makedirs(out_dir, exist_ok=True)

    engine = RainEngine(room, samplerate=samplerate)

    mode = 'headphones' if getattr(room, 'headphones_mode', False) else 'speakers'
    tstamp = time.strftime('%Y%m%d_%H%M%S')

    manifest_rows = []
    for mat_name in _list_material_names():
        try:
            engine.set_materials(roof_name=mat_name, window_name=mat_name)
        except Exception:
            pass

        stereo = engine.render_offline(seconds=seconds, mode=mode)
        base = f"{tstamp}_{_slug(mat_name)}.wav"
        wav_path = os.path.join(out_dir, base)
        wavfile.write(wav_path, samplerate, np.clip(stereo, -1.0, 1.0).astype(np.float32))

        manifest_rows.append({
            'material': mat_name,
            'mode': mode,
            'seconds': seconds,
            'samplerate': samplerate,
            'wav_path': wav_path
        })

    csv_path = os.path.join(out_dir, f"manifest_{tstamp}.csv")
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(manifest_rows[0].keys()))
        w.writeheader(); w.writerows(manifest_rows)

    return out_dir, csv_path
