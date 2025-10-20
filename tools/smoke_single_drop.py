# tools/smoke_single_drop.py — robust runner that imports engine directly and reports path
# NO WHITE NOISE: the engine itself enforces this rule and uses analytic signals only.
import sys, os, traceback, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out"
OUT.mkdir(parents=True, exist_ok=True)

def log(msg):
    with open(OUT / "run_single_drop.log", "a", encoding="utf-8") as f:
        f.write(msg + "\n")

def main():
    # make sure project root is importable
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    # Import engine and report its file path
    try:
        import app.audio.engine as eng
        eng_path = Path(eng.__file__).resolve()
        log(f"[smoke] engine module path: {eng_path}")
    except Exception as e:
        with open(OUT / "engine_error.log", "w", encoding="utf-8") as f:
            f.write("Import failure:\n")
            f.write("".join(traceback.format_exception(e)))
        print("[smoke] import failed; see out/engine_error.log")
        sys.exit(2)

    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--surface", default="water")
    ap.add_argument("--size-mm", type=float, default=3.5)
    ap.add_argument("--master-gain-db", type=float, default=30.0)
    ap.add_argument("--normalize", action="store_true")
    ap.add_argument("--wetness", type=float, default=1.0)
    ap.add_argument("--hp-cut", type=float, default=160.0)
    ap.add_argument("--declick", type=float, default=1.0)
    ap.add_argument("--roundness", type=float, default=1.8)
    ap.add_argument("--attack-ms", type=float, default=10.0)
    ap.add_argument("--antimetal", type=float, default=1.0)
    ap.add_argument("--diffuse-g", type=float, default=0.1)
    ap.add_argument("--plop-db", type=float, default=-9.0)
    ap.add_argument("--slap-db", type=float, default=-22.0)
    ap.add_argument("--splat-db", type=float, default=-14.0)
    ap.add_argument("--splash-db", type=float, default=-1.0)
    ap.add_argument("--spray-db", type=float, default=-12.0)
    ap.add_argument("--spray-tail-ms", type=float, default=100.0)
    args = ap.parse_args()

    # Configure a default room with a west window so occlusion works
    try:
        if getattr(eng, "_ENGINE_STATE", {}).get("room") is None:
            Room = getattr(eng, "Room")
            FallbackListener = getattr(eng, "_FallbackListener")
            room = Room(width=5.0, height=3.0, depth=6.0, windows=[{"orientation":"west","x":1.5,"y":1.0,"w":1.0,"h":1.0,"open_ratio":1.0}])
            setattr(room, "listener", FallbackListener(2.5, 1.6, 3.0))
            eng.configure_room(room, sr=48000, master_gain_db=args.master_gain_db,
                               wetness=args.wetness, hp_cut=args.hp_cut, antimetal=args.antimetal,
                               diffuse_g=args.diffuse_g, declick=args.declick, roundness=args.roundness,
                               attack_ms=args.attack_ms)
        # Render
        outp = eng.render_single_drop(out_path=str(OUT/"single_drop.wav"), sr=48000,
                                      surface=args.surface, size_mm=args.size_mm,
                                      normalize=args.normalize)
        print(f"[smoke] wrote {outp}")
        log(f"[smoke] wrote {outp}")
    except SystemExit:
        raise
    except Exception as e:
        with open(OUT / "engine_error.log", "w", encoding="utf-8") as f:
            f.write("Run failure:\n")
            f.write("".join(traceback.format_exception(e)))
        print("[smoke] run failed; see out/engine_error.log")
        sys.exit(3)

    # Verify file exists
    if not (OUT / "single_drop.wav").exists():
        log("[smoke] ERROR: single_drop.wav missing after render_single_drop")
        sys.exit(4)

if __name__ == "__main__":
    main()
