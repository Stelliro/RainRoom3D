# tools/ab_render.py — render 6 extreme variants to prove params are applied (no white noise)
from __future__ import annotations
import sys, json, traceback, hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out" / "ab"
OUT.mkdir(parents=True, exist_ok=True)

def md5(path: Path) -> str:
    import hashlib
    m = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            m.update(chunk)
    return m.hexdigest()

def set_state(eng, d):
    # Update _ENGINE_STATE keys if present; ignore others
    st = getattr(eng, "_ENGINE_STATE", None)
    if isinstance(st, dict):
        for k,v in d.items():
            st[k] = v
    # Also try configure_room if present (respects sr/master gain and allows occlusion)
    if hasattr(eng, "configure_room") and hasattr(eng, "Room"):
        try:
            room = eng.Room(width=5.0, height=3.0, depth=6.0, windows=[{"orientation":"west","x":1.5,"y":1.0,"w":1.0,"h":1.0,"open_ratio":1.0}])
            if not hasattr(room, "listener"):
                class _FL:
                    def __init__(self,x=2.5,y=1.6,z=3.0): self.x,self.y,self.z=x,y,z
                setattr(room, "listener", _FL())
            eng.configure_room(room, sr=int(d.get("sr", 48000)), master_gain_db=float(d.get("master_gain_db", 24.0)), **{k:v for k,v in d.items() if k not in ("sr","master_gain_db")})
        except Exception:
            pass

def render_variant(eng, name: str, params: dict):
    set_state(eng, params)
    outp = OUT / f"{name}.wav"
    try:
        eng.render_single_drop(out_path=str(outp), sr=int(params.get("sr",48000)), surface="water", size_mm=3.5, normalize=True)
    except TypeError:
        # fallback: older signature
        eng.render_single_drop(str(outp), int(params.get("sr",48000)))
    return outp

def main():
    sys.path.insert(0, str(ROOT))
    import importlib
    try:
        eng = importlib.import_module("app.audio.engine")
    except Exception as e:
        print("[ab] ERROR: cannot import app.audio.engine:", e)
        raise

    print("[ab] engine:", Path(eng.__file__).resolve())

    # Six extreme variants
    presets = [
        ("A_body_only", dict(master_gain_db=24.0, attack_ms=30.0, roundness=3.2, predelay_ms=0.0, hp_cut=80.0,
                             plop_db=-1.0, slap_db=-90.0, splat_db=-90.0, splash_db=-90.0, spray_db=-90.0,
                             morph_hlfhf=0.0, bounces=1)),
        ("B_bright_only", dict(master_gain_db=24.0, attack_ms=10.0, roundness=1.0, predelay_ms=0.0, hp_cut=400.0,
                               plop_db=-90.0, slap_db=-8.0, splat_db=-8.0, splash_db=+3.0, spray_db=-6.0,
                               morph_hlfhf=1.0, morph_u1_ms=6.0, morph_u2_ms=16.0, morph_width_ms=2.0, bounces=1)),
        ("C_slap_only", dict(master_gain_db=24.0, attack_ms=0.0, roundness=0.0, predelay_ms=0.0, hp_cut=120.0,
                             plop_db=-90.0, slap_db=-2.0, splat_db=-90.0, splash_db=-90.0, spray_db=-90.0,
                             morph_hlfhf=0.0, bounces=1)),
        ("D_multi_bounce_soft", dict(master_gain_db=24.0, attack_ms=28.0, roundness=3.0, predelay_ms=4.5, hp_cut=95.0,
                                     plop_db=-3.0, slap_db=-40.0, splat_db=-26.0, splash_db=+1.0, spray_db=-12.0,
                                     morph_hlfhf=1.0, morph_u1_ms=12.0, morph_u2_ms=30.0, morph_width_ms=6.0,
                                     bounces=4, bounce_gap_ms=15.0, bounce_decay=0.56, bounce_brighten=0.16,
                                     bubble_db=-6.0, bubble_f1=420.0, bubble_f2=1750.0)),
        ("E_predelay_extreme", dict(master_gain_db=24.0, attack_ms=26.0, roundness=3.0, predelay_ms=8.0, hp_cut=90.0,
                                    plop_db=-2.0, slap_db=-44.0, splat_db=-30.0, splash_db=+2.0, spray_db=-12.0,
                                    morph_hlfhf=1.0, morph_u1_ms=12.0, morph_u2_ms=32.0, morph_width_ms=7.0,
                                    bounces=3)),
        ("F_zero_soft_bright", dict(master_gain_db=24.0, attack_ms=0.0, roundness=0.0, predelay_ms=0.0, hp_cut=300.0,
                                    plop_db=-90.0, slap_db=-6.0, splat_db=-6.0, splash_db=+3.0, spray_db=-8.0,
                                    morph_hlfhf=0.0, bounces=1)),
    ]

    report = {"engine_path": str(Path(eng.__file__).resolve()), "variants": []}
    for name,params in presets:
        p = render_variant(eng, name, params)
        info = {"name": name, "path": str(p.resolve()), "md5": md5(p), "bytes": p.stat().st_size, "params": params}
        report["variants"].append(info)
        print("[ab]", name, "->", p.name, info["md5"], info["bytes"], "bytes")

    # write report
    txt = ["A/B render results", f"engine: {report['engine_path']}",""]
    for v in report["variants"]:
        txt.append(f"{v['name']}: {Path(v['path']).name}  size={v['bytes']}  md5={v['md5']}")
    (OUT/"ab_report.txt").write_text("\n".join(txt), encoding="utf-8")
    (OUT/"ab_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("[ab] wrote", (OUT/"ab_report.txt"))

if __name__ == "__main__":
    main()
