#!/usr/bin/env python3
"""ai_to_fbf16.py – ảnh AI nền trắng (768×768, cùng tỉ lệ với ảnh gốc 500→768) → khung PNG 500×500 RGBA đúng định dạng hoạ sĩ.
Tách nền: vùng gần trắng nối liền với viền ảnh (giữ chỗ trắng bên trong nhân vật như xương), mép mềm + gỡ ám trắng.
Chuẩn hoá: thu về đúng tỉ lệ ảnh gốc (500/768), đặt đáy chân ở y=498, giữ vị trí x như AI vẽ.
Dùng: python ai_to_fbf16.py <thư mục ảnh AI {khung}_s{seed}.png> <thư mục ra> --goc 1.png [--seed 1] [--canh-co] [--mau 0.7]\n  --canh-co: ước tỉ lệ bằng điểm đặc trưng SIFT khớp ảnh gốc (AI hay phóng to/thu nhỏ khi đổi dáng). --mau: kéo màu về ảnh gốc. --ti-le f.json: tỉ lệ diện tích khung/khung 1 của bộ dáng khuôn (dự phòng).\n  --cao f.json [--cao-khung 2,3,..]: dáng đứng canh theo tỉ lệ chiều cao khung/khung 1 của bộ khuôn (mặc định khung 2–8, 12–14).\n  --chi-tiet x0,y0,x1,y1: vùng một chi tiết cứng trên ảnh gốc (vd đèn lồng) để dò tỉ lệ – ưu tiên nhất.\n  Mặc định dùng tỉ lệ khuôn trong config/fbf16/ (khuôn chibi 58/59/60); quái khác khuôn thì thêm --khong-khuon.
"""
import sys, json
from pathlib import Path
import numpy as np, cv2
from PIL import Image
from scipy import ndimage
sys.path.insert(0, str(Path(__file__).parent))
from lantern_scale import find_scale

def cutout(rgb, t0=10, t1=45):
    d = 255 - rgb.min(axis=2).astype(np.float32)          # khoảng cách tới trắng
    cand = d < t1
    lab, _ = ndimage.label(cand)
    border = np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]])); border = border[border > 0]
    bg = np.isin(lab, border)
    a = np.ones(d.shape, np.float32)
    a[bg] = np.clip((d[bg] - t0) / (t1 - t0), 0, 1)
    fg = a > 0.5; lab2, n = ndimage.label(fg)            # bỏ đảo rác nhỏ
    if n > 1:
        sizes = ndimage.sum(fg, lab2, range(1, n + 1)); keep = sizes >= 0.002 * sizes.max()
        a[~np.isin(lab2, np.nonzero(keep)[0] + 1) & fg] = 0
    c = rgb.astype(np.float32)
    m = (a > 0.02) & (a < 1)
    c[m] = np.clip((c[m] - (1 - a[m, None]) * 255) / a[m, None], 0, 255)   # gỡ ám trắng ở mép
    return np.dstack([c, a * 255]).astype(np.uint8)

def to500(rgba, scale=500 / 768, size=500, foot_y=498, cx=None):
    """Thu về tỉ lệ, đặt đáy chân ở foot_y; nếu có cx thì dời ngang cho tâm hộp hình trùng cx (tâm hộp ảnh gốc)."""
    h, w = rgba.shape[:2]
    im = Image.fromarray(rgba).resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
    a = np.asarray(im)[..., 3]; ys, xs = np.nonzero(a > 128)
    dy = foot_y - ys.max()
    dx = 0 if cx is None else int(round(cx - (xs.min() + xs.max()) / 2))
    if cx is None and im.width != size: dx = (size - im.width) // 2
    out = Image.new("RGBA", (size, size)); out.paste(im, (dx, dy), im)
    return out, dy

SIFT = cv2.SIFT_create(nfeatures=4000)

def sift_scale(rgba_ai, rgba_goc):
    """Tỉ lệ (px ảnh AI / px ảnh gốc) ước từ điểm đặc trưng khớp nhau (đầu lâu, đèn lồng, hoa văn giữ nguyên cỡ dù dáng đổi)."""
    def kd(x):
        g = cv2.cvtColor(np.ascontiguousarray(x[..., :3]), cv2.COLOR_RGB2GRAY); m = (x[..., 3] > 128).astype(np.uint8) * 255
        return SIFT.detectAndCompute(g, m)
    k1, d1 = kd(rgba_goc); k2, d2 = kd(rgba_ai)
    if d1 is None or d2 is None: return None, 0
    mt = cv2.BFMatcher().knnMatch(d2, d1, k=2)
    good = [a for a, b in (x for x in mt if len(x) == 2) if a.distance < 0.75 * b.distance]
    if len(good) < 12: return None, len(good)
    src = np.float32([k1[m.trainIdx].pt for m in good]); dst = np.float32([k2[m.queryIdx].pt for m in good])
    M, inl = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=4.0)
    if M is None or inl.sum() < 10: return None, int(0 if inl is None else inl.sum())
    return float(np.hypot(M[0, 0], M[1, 0])), int(inl.sum())

def match_color(rgba, goc, strength=0.7):
    """Kéo trung bình/độ lệch Lab của hình AI về ảnh gốc (chỉ vùng có hình)."""
    def lab(x): return cv2.cvtColor(np.ascontiguousarray(x[..., :3]).astype(np.float32) / 255, cv2.COLOR_RGB2LAB)
    a, g = lab(rgba), lab(goc); ma, mg = rgba[..., 3] > 128, goc[..., 3] > 128
    out = a.copy()
    for c in range(3):
        mu_a, sd_a = a[..., c][ma].mean(), a[..., c][ma].std() + 1e-6; mu_g, sd_g = g[..., c][mg].mean(), g[..., c][mg].std()
        t = (a[..., c] - mu_a) / sd_a * sd_g + mu_g
        out[..., c] = a[..., c] * (1 - strength) + t * strength
    rgb = np.clip(cv2.cvtColor(out, cv2.COLOR_LAB2RGB), 0, 1)
    return np.dstack([(rgb * 255 + .5).astype(np.uint8), rgba[..., 3]])

def main():
    src, dst = Path(sys.argv[1]), Path(sys.argv[2]); dst.mkdir(parents=True, exist_ok=True)
    seed = sys.argv[sys.argv.index("--seed") + 1] if "--seed" in sys.argv else "1"
    goc = Path(sys.argv[sys.argv.index("--goc") + 1])
    Image.open(goc).convert("RGBA").save(dst / "1.png")
    global GOC, CANH, MAU
    GOC = np.asarray(Image.open(goc).convert("RGBA")); CANH = "--canh-co" in sys.argv
    global TL
    global CT
    CT = None
    if "--chi-tiet" in sys.argv:
        x0, y0, x1, y1 = [int(v) for v in sys.argv[sys.argv.index("--chi-tiet") + 1].split(",")]; CT = GOC[y0:y1, x0:x1]
    global CAO, CAO_KHUNG
    CFG = Path(__file__).resolve().parents[2] / "config" / "fbf16"   # tỉ lệ chuẩn của khuôn chibi 58/59/60
    KHUON = "--khong-khuon" not in sys.argv
    CAO = json.loads(Path(sys.argv[sys.argv.index("--cao") + 1]).read_text()) if "--cao" in sys.argv else (json.loads((CFG / "ti_le_cao_khuon.json").read_text()) if KHUON else None)
    CAO_KHUNG = sys.argv[sys.argv.index("--cao-khung") + 1].split(",") if "--cao-khung" in sys.argv else [str(i) for i in range(2, 9)] + ["12", "13", "14"]
    TL = json.loads(Path(sys.argv[sys.argv.index("--ti-le") + 1]).read_text()) if "--ti-le" in sys.argv else (json.loads((CFG / "ti_le_dien_tich_khuon.json").read_text()) if KHUON else None)
    MAU = float(sys.argv[sys.argv.index("--mau") + 1]) if "--mau" in sys.argv else 0
    for f in range(2, 17):
        p = src / f"{f}_s{seed}.png"
        if not p.exists(): print("thiếu", p); continue
        rgba = cutout(np.asarray(Image.open(p).convert("RGB")))
        sc, n = (sift_scale(rgba, GOC) if CANH else (None, 0))
        how = ""
        if CT is not None:
            sc_ct, ok_ct = None, 0
            sc_v, sc_s, _ = find_scale(np.asarray(Image.open(p).convert("RGB")), CT)
            if sc_v >= 0.30: sc, how = sc_s, f"chi tiết cứng (điểm {sc_v:.2f})"
        if CAO and str(f) in CAO_KHUNG:   # dáng đứng: canh theo chiều cao chuẩn của khuôn
            ys = np.nonzero(rgba[..., 3] > 128)[0]; yg = np.nonzero(GOC[..., 3] > 128)[0]
            scale = CAO[str(f)] * (yg.max() - yg.min()) / (ys.max() - ys.min()); how = "chiều cao khuôn"
        elif sc: scale = 1 / sc
        elif TL:   # canh theo diện tích chuẩn của khung trong bộ dáng khuôn
            area_ai = float((rgba[..., 3] > 128).sum()); area_goc = float((GOC[..., 3] > 128).sum())
            scale = (TL[str(f)] * area_goc / area_ai) ** 0.5
        else: scale = 500 / 768
        if MAU: rgba = match_color(rgba, GOC, MAU)
        gx = np.nonzero(GOC[..., 3] > 128)[1]
        out, dy = to500(rgba, scale=scale, cx=(gx.min() + gx.max()) / 2); out.save(dst / f"{f}.png")
        print(f"  tỉ lệ {how or ('SIFT' if sc else ('diện tích khuôn' if TL else 'mặc định'))} {scale:.3f} ({n} điểm khớp)")
        al = np.asarray(out)[..., 3]; ys, xs = np.nonzero(al > 128)
        print(f"{f}: dời dọc {dy:+d}px, hộp x {xs.min()}–{xs.max()} y {ys.min()}–{ys.max()}")

if __name__ == "__main__":
    main()
