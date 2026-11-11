#!/usr/bin/env python3
"""recolor_fx.py – sinh đạn a/b/c cho quái mới bằng cách đổi sắc bộ đạn chuẩn (không cần AI).
Bộ a/b/c của 58/59/60 trùng hình 100% (alpha giống hệt từng điểm), chỉ khác màu kiểu gradient map.
Cách làm: xoay sắc trong không gian LCh (giữ độ sáng L và độ đậm C) về sắc phát sáng của quái mới.
Dùng: python recolor_fx.py <thư mục bộ đạn gốc> <thư mục ra> (--hue H | --from-image ảnh_quái.png [--chroma-ref khung1_quái_gốc.png])
"""
import sys
from pathlib import Path
import numpy as np, cv2
from PIL import Image

def lab(x): return cv2.cvtColor(x.astype(np.float32) / 255, cv2.COLOR_RGB2LAB)

def glow_hue(img, mask):
    l = lab(img[..., :3]); c = np.hypot(l[..., 1], l[..., 2]); h = np.arctan2(l[..., 2], l[..., 1])
    m = mask & (c > 35) & (l[..., 0] > 45)
    return float(np.degrees(np.arctan2((c[m] * np.sin(h[m])).sum(), (c[m] * np.cos(h[m])).sum())) % 360), int(m.sum())

def chroma_p97(img, mask):
    l = lab(img[..., :3]); c = np.hypot(l[..., 1], l[..., 2]); return float(np.percentile(c[mask & (c > 20)], 97))

def recolor(img, hue, gain=1.0):
    l = lab(img[..., :3]); c = np.hypot(l[..., 1], l[..., 2]) * gain; h = np.radians(hue)
    l[..., 1] = c * np.cos(h); l[..., 2] = c * np.sin(h)
    rgb = np.clip(cv2.cvtColor(l, cv2.COLOR_LAB2RGB), 0, 1)
    return np.dstack([(rgb * 255 + .5).astype(np.uint8), img[..., 3]])

def main():
    src, out = Path(sys.argv[1]), Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
    if "--hue" in sys.argv:
        hue = float(sys.argv[sys.argv.index("--hue") + 1])
    else:
        im = np.asarray(Image.open(sys.argv[sys.argv.index("--from-image") + 1]).convert("RGBA"))
        hue, n = glow_hue(im, im[..., 3] > 200); print(f"sắc phát sáng (LCh): {hue:.0f}° ({n} px)")
    gain = 1.0
    if "--chroma-ref" in sys.argv and "--from-image" in sys.argv:  # ảnh khung 1 của quái sở hữu bộ đạn gốc
        r = np.asarray(Image.open(sys.argv[sys.argv.index("--chroma-ref") + 1]).convert("RGBA"))
        gain = chroma_p97(im, im[..., 3] > 200) / chroma_p97(r, r[..., 3] > 200); print(f"hệ số độ đậm: {gain:.2f}")
    for n in "abc":
        im = np.asarray(Image.open(src / f"{n}.png").convert("RGBA"))
        Image.fromarray(recolor(im, hue, gain)).save(out / f"{n}.png")
    print("xong", out)

if __name__ == "__main__":
    main()
