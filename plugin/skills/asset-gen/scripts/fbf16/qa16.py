#!/usr/bin/env python3
"""qa16.py – kiểm bộ 16 khung chuẩn hoạ sĩ (1-16 + a/b/c) cho quái FBF, không cần AI.
Đo từng khung so với khung 1 (ảnh gốc):
  feet_dx  : điểm chân lệch so với trung vị nhóm anim (px)       – bắt "khớp/nhảy hình"
  h_ratio  : chiều cao hình / chiều cao khung 1                 – bắt đổi cỡ
  hue_bh   : khoảng cách Bhattacharyya histogram sắc (LCh)      – bắt đổi màu
  ident    : tỉ lệ điểm SIFT khớp với khung 1 (lật/đổi dáng vẫn giữ chi tiết cục bộ) – bắt mất chi tiết / sai nhân vật
  dup      : chênh trung bình với khung trước (≈0 là khung trùng)
Dùng: python qa16.py <thư mục 1..16.png> [--ref ảnh_gốc.png] [--nguong config/fbf16/nguong-16-khung.json] [--json out.json]
Mã thoát: 0 = không cờ · 2 = có khung bị cắm cờ (người xem lại).
"""
import sys, json
from pathlib import Path
import numpy as np, cv2
from PIL import Image

GROUPS = {"idle": [1, 2], "walk": [3, 4, 5, 6, 7, 8], "attack": [9, 10, 11], "skill": [12, 13, 14], "die": [15, 16]}
SIFT = cv2.SIFT_create(nfeatures=3000)

def load(p): return np.asarray(Image.open(p).convert("RGBA"))

def feet(al):
    ys, xs = np.nonzero(al > 128); bot = ys.max(); band = ys >= bot - 25
    return float(xs[band].mean()), int(bot), int(bot - ys.min()), (xs.min(), xs.max(), ys.min())

def hue_hist(img):
    lab = cv2.cvtColor(img[..., :3].astype(np.float32) / 255, cv2.COLOR_RGB2LAB)
    c = np.hypot(lab[..., 1], lab[..., 2]); h = np.degrees(np.arctan2(lab[..., 2], lab[..., 1])) % 360
    m = (img[..., 3] > 128) & (c > 12)
    hist, _ = np.histogram(h[m], bins=36, range=(0, 360), weights=c[m]); hist = hist.astype(np.float32)
    return hist / max(hist.sum(), 1e-6)

def bhatt(a, b): return float(np.sqrt(max(0.0, 1 - np.sum(np.sqrt(a * b)))))

def sift(img):
    g = cv2.cvtColor(img[..., :3], cv2.COLOR_RGB2GRAY); m = (img[..., 3] > 128).astype(np.uint8) * 255
    return SIFT.detectAndCompute(g, m)

def ident(k1, d1, k2, d2):
    if d1 is None or d2 is None or len(k1) < 10 or len(k2) < 10: return 0.0
    mt = cv2.BFMatcher().knnMatch(d2, d1, k=2)
    good = [a for a, b in (x for x in mt if len(x) == 2) if a.distance < 0.75 * b.distance]
    return len(good) / min(len(k1), len(k2))

def run(folder, ref=None):
    folder = Path(folder); ref = Path(ref) if ref else folder / "1.png"
    R = load(ref); rf = feet(R[..., 3]); rh = hue_hist(R); rk, rd = sift(R)
    rows = {}; seen = []
    for i in range(1, 17):
        im = load(folder / f"{i}.png"); fx, bot, h, (x0, x1, y0) = feet(im[..., 3]); k, d = sift(im)
        area = int((im[..., 3] > 128).sum()); width = int(x1 - x0)
        rows[i] = {"feet_x": fx, "bottom": bot, "h_ratio": h / rf[2], "hue_bh": bhatt(rh, hue_hist(im)),
                   "ident": ident(rk, rd, k, d), "touch_edge": bool(x0 <= 0 or x1 >= im.shape[1] - 1 or y0 <= 0),
                   "area": area, "width": width,
                   "dup": None if not seen else min(float(np.abs(p.astype(np.int16) - im.astype(np.int16)).mean()) for p in seen)}
        seen.append(im)
    for g, idx in GROUPS.items():
        med = float(np.median([rows[i]["feet_x"] for i in idx]))
        ma = float(np.median([rows[i]["area"] for i in idx])); mw = float(np.median([rows[i]["width"] for i in idx]))
        for i in idx:
            rows[i]["feet_dx"] = rows[i]["feet_x"] - med; rows[i]["anim"] = g
            rows[i]["area_r"] = rows[i]["area"] / ma; rows[i]["width_r"] = rows[i]["width"] / mw
    return rows

# ---- Ngưỡng hiệu chỉnh trên 3 bộ hoạ sĩ 58/59/60 (10/11/2026) ----
STRICT = {"idle", "walk", "skill"}          # dáng gần khung 1 → kiểm chặt
def verdict(rows, baseline):
    out = {}
    for i, x in rows.items():
        f = []
        if i > 1:
            b = baseline[str(i)]
            if x["anim"] in STRICT and x["ident"] < 0.6 * b["ident"][0]: f.append("nhận dạng thấp (mất chi tiết / sai nhân vật)")
            if x["hue_bh"] > b["hue"][1] + 0.1: f.append("lệch màu")
            if abs(x["feet_dx"]) > b["dx"][1] + 12: f.append("nhảy chân")
            if not (b["h"][0] - 0.05 <= x["h_ratio"] <= b["h"][1] + 0.05): f.append("sai cỡ")
            if x["anim"] in STRICT and (x["width_r"] < 0.9 or x["area_r"] < 0.9): f.append("thiếu mảng (vũ khí/phụ kiện mất?)")
            if x["dup"] is not None and x["dup"] < 2.0: f.append("khung trùng")
        if x["bottom"] < 490: f.append("chân không chạm đáy")
        if x["touch_edge"]: f.append("chạm mép")
        out[i] = f
    return out


if __name__ == "__main__":
    a = sys.argv
    rows = run(a[1], a[a.index("--ref") + 1] if "--ref" in a else None)
    nguong = Path(a[a.index("--nguong") + 1]) if "--nguong" in a else Path(__file__).resolve().parents[2] / "config" / "fbf16" / "nguong-16-khung.json"
    flags = verdict(rows, json.loads(nguong.read_text()))
    for i, r in rows.items():
        print(f"{i:>2} {r['anim']:<6} chân_dx {r['feet_dx']:+6.1f}  đáy {r['bottom']}  cao {r['h_ratio']:.2f}  sắc {r['hue_bh']:.3f}  "
              f"nhận_dạng {r['ident']:.3f}  rộng {r['width_r']:.2f}  trùng {'' if r['dup'] is None else round(r['dup'], 1)}  "
              f"{'⚠ ' + '; '.join(flags[i]) if flags[i] else 'ok'}")
    if "--json" in a: Path(a[a.index("--json") + 1]).write_text(json.dumps({"rows": rows, "flags": flags}, ensure_ascii=False, indent=1))
    sys.exit(2 if any(flags.values()) else 0)
