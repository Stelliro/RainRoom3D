# tools/single_drop_helper.py — no-noise analytic render helper (v8)
# Purpose: avoid CMD inline-paren issues; import engine and render a single wet drop.
from pathlib import Path
import sys, traceback

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out"
OUT.mkdir(parents=True, exist_ok=True)

def main():
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        import app.audio.engine as eng
        print("[helper v8] engine:", Path(eng.__file__).resolve())
        out = str((OUT / "single_drop.wav").resolve())
        kw = {
            "out_path": out,
            "sr": 48000,
            "surface": "water",
            "size_mm": 3.5,
            "normalize": True,
        }
        # Try modern config if available
        if hasattr(eng, "configure_room"):
            Room = eng.Room
            FL = getattr(eng, "_FallbackListener", None)
            if FL is None:
                class _FL: 
                    def __init__(self,x=0.0,y=1.6,z=0.0): self.x,self.y,self.z=x,y,z
                FL = _FL
            room = Room(width=5.0, height=3.0, depth=6.0, windows=[{"orientation":"west","x":1.5,"y":1.0,"w":1.0,"h":1.0,"open_ratio":1.0}])
            setattr(room, "listener", FL(2.5,1.6,3.0))
            if hasattr(eng, "_ENGINE_STATE"):
                eng._ENGINE_STATE.update({
                    "master_gain_db": 30.0,
                    "wetness": 1.0,
                    "hp_cut": 130.0,
                    "antimetal": 1.0,
                    "diffuse_g": 0.08,
                    "declick": 1.0,
                    "roundness": 2.4,
                    "attack_ms": 18.0,
                    "predelay_ms": 2.6,
                    "plop_db": -4.0,
                    "slap_db": -36.0,
                    "splat_db": -20.0,
                    "splash_db": +1.0,
                    "spray_db": -11.0,
                    "spray_tail_ms": 130.0,
                })
        eng.render_single_drop(**kw)
        print("[helper v8] wrote", out)
        return 0
    except SystemExit:
        raise
    except Exception as e:
        with open(OUT / "engine_error.log", "w", encoding="utf-8") as f:
            f.write("Helper failure:\n")
            f.write("".join(traceback.format_exception(e)))
        print("[helper v8] ERROR; see out/engine_error.log")
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
