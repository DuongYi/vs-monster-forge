#!/usr/bin/env python3
"""fbf16_qa.py – đưa bộ 16 khung chuẩn (1-16 + a/b/c) qua postprocess.py của Xưởng Quái
và đo thêm vài chỉ số riêng cho định dạng hoạ sĩ.
Dùng: python fbf16_qa.py <thư mục bộ khung> <thư mục ra> [--ref 1.png]
"""
import json, subprocess, sys, shutil
from pathlib import Path
import numpy as np
from PIL import Image
import cv2

PP = Path(__file__).resolve().parents[1] / "postprocess.py"   # postprocess.py của Xưởng Quái
GROUPS = {"idle": [1, 2], "walk": [3, 4, 5, 6, 7, 8], "attack": [9, 10, 11], "skill": [12, 13, 14], "die": [15, 16]}
FPS = {"idle": 3, "walk": 4.5, "attack": 6.7, "skill": 7, "die": 2}
LOOP = {"idle": True, "walk": True}

def load(p):
    return np.asarray(Image.open(p).convert("RGBA"))

def feet(al):
    ys, xs = np.nonzero(al > 128)
    if len(ys) == 0: return None
    bot = ys.max(); band = (ys >= bot - 25)
    return float(xs[band].mean()), int(bot), int(ys.min()), int(xs.min()), int(xs.max())

def hist_ab(img):
    al = img[..., 3] > 128
    lab = cv2.cvtColor(img[..., :3], cv2.COLOR_RGB2LAB)
    h = cv2.calcHist([lab], [1, 2], al.astype(np.uint8), [24, 24], [0, 256, 0, 256])
    return cv2.normalize(h, h).flatten()

def main():
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    ref = Path(sys.argv[sys.argv.index("--ref") + 1]) if "--ref" in sys.argv else src / "1.png"
    out.mkdir(parents=True, exist_ok=True)
    raw = out / "raw"; shutil.rmtree(raw, ignore_errors=True)
    for name, idx in GROUPS.items():
        d = raw / name; d.mkdir(parents=True)
        for k, i in enumerate(idx):
            shutil.copy(src / f"{i}.png", d / f"{k + 1:04d}.png")
    spec = {"monster_id": "m_" + "".join(c for c in src.name.lower() if c.isalnum()), "target_height_px": 340, "pixels_per_unit": 125,
            "anchor": {"x": "feet", "y": "ground"}, "scale_mode": "global", "scale_reference": "idle",
            "padding_px": 4, "background": {"mode": "auto", "spill": False, "keep_islands": True},
            "style_reference": str(ref.resolve()),
            "animations": {n: {"frames": len(i), "fps": FPS[n], "loop": LOOP.get(n, False),
                               **({"stabilize": "lock"} if n in ("idle", "walk") else {"stabilize": "none"})}
                           for n, i in GROUPS.items()}}
    (out / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=1))
    r = subprocess.run([sys.executable, str(PP), "run", "--spec", str(out / "spec.json"), "--input", str(raw),
                        "--out", str(out / "pp")], capture_output=True, text=True)
    (out / "postprocess.log").write_text(r.stdout + r.stderr)

    # chỉ số riêng
    refimg = load(ref); hr = hist_ab(refimg)
    rows = []; prev = None
    for i in range(1, 17):
        p = src / f"{i}.png"
        if not p.exists(): rows.append({"f": i, "missing": True}); continue
        im = load(p); al = im[..., 3]
        fx, bot, top, x0, x1 = feet(al)
        touch = [s for s, c in (("trái", x0 <= 0), ("phải", x1 >= im.shape[1] - 1), ("trên", top <= 0)) if c]
        b = float(cv2.compareHist(hr, hist_ab(im), cv2.HISTCMP_BHATTACHARYYA))
        dup = None
        if prev is not None and prev.shape == im.shape:
            dup = float(np.abs(prev.astype(np.int16) - im.astype(np.int16)).mean())
        rows.append({"f": i, "size": list(im.shape[:2]), "feet_x": round(fx, 1), "bottom": bot,
                     "height": bot - top, "touch": touch, "color_bhatt_vs_ref": round(b, 3),
                     "mean_absdiff_prev": None if dup is None else round(dup, 2)})
        prev = im
    for n in "abc":
        p = src / f"{n}.png"
        rows.append({"f": n, "exists": p.exists(), "size": list(load(p).shape[:2]) if p.exists() else None})
    (out / "metrics.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    print(r.stdout[-1500:], r.stderr[-800:])
    for row in rows: print(row)

if __name__ == "__main__":
    main()
