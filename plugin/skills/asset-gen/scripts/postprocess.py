#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
postprocess.py — Hậu xử lý khung frame-by-frame (FBF) cho quái, chạy sau bước sinh ảnh ComfyUI.

Luồng:
  nạp khung (thư mục PNG hoặc 1 video) → xoá nền (alpha có sẵn / màu khoá phẳng) → khử viền màu nền
  → chọn khung (tự tìm điểm lặp khi nguồn là video) → scale đồng nhất → căn điểm neo (chân)
  → ghép atlas lưới đều → manifest (pivot, fps, loop, sự kiện đòn đánh, truy vết) → kiểm tra tự động.

Lệnh:
  python postprocess.py run   --spec SPEC.json --input RAW_DIR --out OUT_DIR [--thresholds T.json] [--strict]
  python postprocess.py check --manifest OUT_DIR/<monster_id>.fbf.json [--strict]

Cấu trúc RAW_DIR (mỗi animation trong spec cần đúng một nguồn):
  RAW_DIR/provenance.json        (tuỳ chọn – skill asset-gen ghi: workflow, model, LoRA, seed…)
  RAW_DIR/idle/0001.png ...      (chuỗi khung, sắp theo tên)
  RAW_DIR/attack.mp4             (hoặc một video; .webm/.mov/.gif cũng được)

Mã thoát: 0 = đạt (có thể kèm cảnh báo) · 2 = QA có lỗi (hoặc có cảnh báo khi --strict) · 1 = lỗi đầu vào.
Phụ thuộc: numpy, opencv-python, pillow, scipy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageSequence
from scipy import ndimage

VERSION = "0.1.0"
MANIFEST_SCHEMA = "dpvs.fbf/1"
IMAGE_EXTS = {".png", ".webp", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv", ".avi", ".gif"}

# Ngưỡng mặc định. File --thresholds (JSON) ghi đè từng khoá. Các ngưỡng QA là giá trị khởi điểm,
# cần hiệu chỉnh trên 5–10 quái thật đầu tiên.
DEFAULT_THRESHOLDS: dict = {
    # xoá nền (đơn vị ΔE trong không gian Lab)
    "bg_uniform_max_de": 14.0,      # độ lệch màu tối đa của viền ảnh để coi là "nền phẳng"
    "key_soft_in_de": 18.0,         # dưới mức này: chắc chắn là nền (alpha 0)
    "key_soft_out_de": 40.0,        # trên mức này: chắc chắn là quái (alpha 1); ở giữa: mép mềm
    "alpha_floor": 0.04,            # alpha nhỏ hơn mức này bị xoá hẳn
    "min_island_frac": 0.003,       # mảng rời nhỏ hơn tỉ lệ này so với mảng lớn nhất bị coi là rác
    "spill_band_px": 3,             # độ rộng dải mép (px nguồn) được khử ám màu nền
    # QA
    "halo_de": 28.0,
    "halo_ratio": {"warn": 0.02, "fail": 0.06},
    "key_residue_de": 15.0,
    "key_residue_ratio": {"warn": 0.005, "fail": 0.03},
    "pop_step": {"warn": 0.04, "fail": 0.08},          # bước nhảy trọng tâm / chiều cao quái (anim khoá neo)
    "area_flicker": {"warn": 0.15, "fail": 0.30},      # thay đổi diện tích giữa 2 khung liên tiếp
    "loop_seam_ratio": {"warn": 1.6, "fail": 2.5},     # bước cuối→đầu so với bước trung vị (khoảng cách chamfer)
    "color_drift_bhatt": {"warn": 0.30, "fail": 0.45}, # khoảng cách Bhattacharyya histogram a*b*
    "color_drift_dl": {"warn": 8.0, "fail": 15.0},     # chênh độ sáng trung bình L*
    "scale_spread": {"warn": 1.15, "fail": 1.35},      # chiều cao khung đầu các anim so với target
    "palette_de": 6.0,
    "palette_in_ratio_min": 0.98,
    "atlas_budget_px": 2048,
    "atlas_max_px": 4096,
}

# Cách giữ neo mặc định theo tên animation (ghi đè bằng "stabilize" trong spec).
#   lock    – mọi khung đặt đúng điểm neo (hết rung; dùng cho đứng yên, trúng đòn)
#   detrend – bỏ trôi tuyến tính của camera nhưng giữ dao động (chạy tại chỗ)
#   none    – giữ chuyển động gốc so với khung đầu (lao tới, nhảy, ngã)
STABILIZE_BY_NAME = {
    "idle": "lock", "stand": "lock", "hurt": "lock", "hit": "lock", "stun": "lock",
    "run": "detrend", "walk": "detrend", "move": "detrend",
}


class InputError(Exception):
    """Lỗi đầu vào: spec sai, thiếu nguồn, nền không phẳng…"""


# ───────────────────────────── tiện ích ─────────────────────────────

def natural_key(p: Path):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_json(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def lab01(rgb01) -> np.ndarray:
    """RGB float 0..1 (…,3) → Lab thật (L 0..100, a/b ≈ -128..127)."""
    a = np.ascontiguousarray(rgb01, dtype=np.float32)
    shp = a.shape
    lab = cv2.cvtColor(a.reshape(-1, 1, 3), cv2.COLOR_RGB2Lab)
    return lab.reshape(shp)


def load_thresholds(path: str | None) -> dict:
    th = json.loads(json.dumps(DEFAULT_THRESHOLDS))
    if path:
        user = json.loads(Path(path).read_text("utf-8"))
        for k, v in user.items():
            if k.startswith("_"):
                continue
            if isinstance(v, dict) and isinstance(th.get(k), dict):
                th[k].update(v)
            else:
                th[k] = v
    return th


# ───────────────────────────── spec ─────────────────────────────

def validate_spec(spec: dict) -> None:
    errs = []
    mid = spec.get("monster_id")
    if not isinstance(mid, str) or not re.fullmatch(r"[a-z0-9_]+", mid or ""):
        errs.append("monster_id phải là chuỗi a-z, 0-9, _")
    th = spec.get("target_height_px")
    if not isinstance(th, (int, float)) or th <= 0:
        errs.append("target_height_px phải là số dương")
    anims = spec.get("animations")
    if not isinstance(anims, dict) or not anims:
        errs.append("animations phải là object không rỗng")
    else:
        for name, a in anims.items():
            if not isinstance(a.get("frames"), int) or a["frames"] < 1:
                errs.append(f"[{name}] frames phải là số nguyên ≥ 1")
            if not isinstance(a.get("fps"), (int, float)) or a["fps"] <= 0:
                errs.append(f"[{name}] fps phải là số dương")
            st = a.get("stabilize")
            for v in (st.values() if isinstance(st, dict) else [st]):
                if v is not None and v not in ("lock", "detrend", "none"):
                    errs.append(f"[{name}] stabilize '{v}' không hợp lệ (lock|detrend|none)")
    anchor = spec.get("anchor", {})
    if anchor.get("x", "feet") not in ("feet", "body"):
        errs.append("anchor.x phải là feet|body")
    if anchor.get("y", "ground") not in ("ground", "body"):
        errs.append("anchor.y phải là ground|body")
    if spec.get("scale_mode", "global") not in ("global", "first_frame", "fixed"):
        errs.append("scale_mode phải là global|first_frame|fixed")
    if spec.get("scale_mode") == "fixed" and not isinstance(spec.get("scale"), (int, float)):
        errs.append("scale_mode=fixed cần khoá scale (số)")
    if errs:
        raise InputError("Spec không hợp lệ:\n  - " + "\n  - ".join(errs))


def stabilize_modes(name: str, a: dict) -> tuple[str, str]:
    st = a.get("stabilize")
    default = STABILIZE_BY_NAME.get(name.lower().split("_")[0], "none")
    if isinstance(st, dict):
        return st.get("x", default), st.get("y", default)
    if isinstance(st, str):
        return st, st
    return default, default


def parse_events(a: dict) -> list[dict]:
    """events: {"hit": 5} hoặc {"hit": [3, 6]}; target_events_ms cùng dạng (tuỳ chọn)."""
    out = []
    targets = a.get("target_events_ms", {}) or {}
    for name, v in (a.get("events") or {}).items():
        frames = v if isinstance(v, list) else [v]
        tv = targets.get(name)
        tlist = tv if isinstance(tv, list) else ([tv] if tv is not None else [])
        for i, fr in enumerate(frames):
            out.append({"name": name, "frame": int(fr),
                        "target_ms": (float(tlist[i]) if i < len(tlist) else None)})
    return out


# ───────────────────────────── nạp nguồn ─────────────────────────────

def find_source(input_dir: Path, anim: str) -> tuple[str, list[Path]]:
    d = input_dir / anim
    if d.is_dir():
        imgs = sorted([p for p in d.iterdir() if p.suffix.lower() in IMAGE_EXTS], key=natural_key)
        if imgs:
            return "frames", imgs
        vids = sorted(p for p in d.iterdir() if p.suffix.lower() in VIDEO_EXTS)
        if len(vids) == 1:
            return "video", vids
    for ext in sorted(VIDEO_EXTS):
        p = input_dir / f"{anim}{ext}"
        if p.exists():
            return "video", [p]
    raise InputError(f"Không tìm thấy nguồn cho animation '{anim}' trong {input_dir} "
                     f"(cần thư mục {anim}/ chứa ảnh, hoặc file {anim}.mp4/.webm/.gif)")


def source_from_path(p: Path) -> tuple[str, list[Path]]:
    """Nguồn chỉ định trực tiếp: thư mục ảnh, thư mục chứa đúng 1 video, hoặc 1 file video."""
    p = Path(p)
    if p.is_dir():
        imgs = sorted([f for f in p.iterdir() if f.suffix.lower() in IMAGE_EXTS], key=natural_key)
        if imgs:
            return "frames", imgs
        vids = sorted(f for f in p.iterdir() if f.suffix.lower() in VIDEO_EXTS)
        if len(vids) == 1:
            return "video", vids
        raise InputError(f"{p} không có ảnh, hoặc có nhiều hơn 1 video")
    if p.suffix.lower() in VIDEO_EXTS and p.exists():
        return "video", [p]
    raise InputError(f"Không dùng được nguồn {p}")


def anim_provenance(input_dir: Path, anim: str, src_dir: Path | None = None) -> dict | None:
    """provenance.json riêng của một anim (comfy_local.py run ghi vào thư mục đích)."""
    cands = [src_dir / "provenance.json"] if src_dir else []
    cands += [input_dir / anim / "provenance.json", input_dir / f"{anim}.provenance.json"]
    for p in cands:
        if p.exists():
            try:
                return json.loads(p.read_text("utf-8"))
            except json.JSONDecodeError:
                return {"_error": f"không đọc được {p.name}"}
    return None


def load_still(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("RGBA"), dtype=np.float32) / 255.0


def video_fps(path: Path) -> float | None:
    if path.suffix.lower() == ".gif":
        with Image.open(path) as im:
            d = im.info.get("duration")
            return 1000.0 / d if d else None
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) if cap.isOpened() else 0
    cap.release()
    return float(fps) if fps and fps > 0 else None


def iter_frames(kind: str, files: list[Path]):
    if kind == "frames":
        for p in files:
            yield load_still(p)
        return
    path = files[0]
    if path.suffix.lower() == ".gif":
        with Image.open(path) as im:
            for f in ImageSequence.Iterator(im):
                yield np.asarray(f.convert("RGBA"), dtype=np.float32) / 255.0
        return
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise InputError(f"Không mở được video {path}")
    try:
        while True:
            ok, bgr = cap.read()
            if not ok:
                break
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
            yield np.dstack([rgb, np.ones(rgb.shape[:2], np.float32)])
    finally:
        cap.release()


# ───────────────────────────── xoá nền ─────────────────────────────

def estimate_key(frame: np.ndarray, border: int = 4) -> tuple[np.ndarray, float]:
    rgb = frame[..., :3]
    h, w = rgb.shape[:2]
    b = max(1, min(border, h // 4, w // 4))
    ring = np.concatenate([rgb[:b].reshape(-1, 3), rgb[-b:].reshape(-1, 3),
                           rgb[:, :b].reshape(-1, 3), rgb[:, -b:].reshape(-1, 3)])
    key = np.median(ring, axis=0).astype(np.float32)
    d = np.linalg.norm(lab01(ring) - lab01(key[None])[0], axis=-1)
    return key, float(np.percentile(d, 90))


def resolve_key(first: np.ndarray, bgcfg: dict, th: dict) -> tuple[np.ndarray | None, dict]:
    mode = bgcfg.get("mode", "auto")
    if mode == "alpha" or (mode == "auto" and float(first[..., 3].min()) < 0.98):
        return None, {"mode": "alpha"}
    if mode == "key" and bgcfg.get("color"):
        key = np.asarray(bgcfg["color"], np.float32) / 255.0
        return key, {"mode": "key", "key_rgb": [int(round(c * 255)) for c in key]}
    key, spread = estimate_key(first)
    if spread > th["bg_uniform_max_de"]:
        raise InputError(
            f"Nền ảnh không phẳng (độ lệch viền ΔE≈{spread:.1f} > {th['bg_uniform_max_de']}). "
            "Hãy sinh trên nền một màu (khai báo trong prompt/workflow), xuất PNG có alpha, "
            "hoặc thêm node tách nền trong ComfyUI.")
    return key, {"mode": "key_auto", "key_rgb": [int(round(c * 255)) for c in key],
                 "border_spread_de": round(spread, 2)}


def suppress_spill(rgb: np.ndarray, alpha: np.ndarray, key: np.ndarray, band_px: int) -> np.ndarray:
    """Khử ám màu nền ở dải mép, chỉ khi màu khoá có 1 kênh trội (xanh lá / xanh dương / đỏ)."""
    order = np.argsort(key)[::-1]
    dom, o1, o2 = int(order[0]), int(order[1]), int(order[2])
    if key[dom] - key[o1] < 0.4:
        return rgb
    bg = alpha < 0.5
    if not bg.any() or bg.all():
        return rgb
    dist = ndimage.distance_transform_edt(~bg)
    edge = (alpha > 0) & (dist <= band_px)
    limit = np.maximum(rgb[..., o1], rgb[..., o2])
    out = rgb.copy()
    out[..., dom] = np.where(edge, np.minimum(rgb[..., dom], limit), rgb[..., dom])
    return out


def clean_alpha(alpha: np.ndarray, th: dict, keep_islands: bool) -> np.ndarray:
    alpha = np.where(alpha < th["alpha_floor"], 0.0, alpha).astype(np.float32)
    if keep_islands:
        return alpha
    lbl, n = ndimage.label(alpha > 0, structure=np.ones((3, 3), bool))
    if n <= 1:
        return alpha
    sums = ndimage.sum(alpha, lbl, index=np.arange(1, n + 1))
    keep = sums >= sums.max() * th["min_island_frac"]
    lut = np.concatenate([[False], keep])
    return alpha * lut[lbl]


def remove_background(frame: np.ndarray, key: np.ndarray | None, th: dict,
                      spill: bool = True, keep_islands: bool = False) -> tuple[np.ndarray, np.ndarray]:
    rgb = frame[..., :3].astype(np.float32)
    a0 = frame[..., 3].astype(np.float32)
    if key is None:
        alpha = a0.copy()
    else:
        d = np.linalg.norm(lab01(rgb) - lab01(key[None])[0], axis=-1)
        t_in, t_out = th["key_soft_in_de"], th["key_soft_out_de"]
        alpha = np.clip((d - t_in) / (t_out - t_in), 0.0, 1.0).astype(np.float32) * a0
        semi = (alpha > 0) & (alpha < 0.999)
        aa = np.maximum(alpha, 1e-3)[..., None]
        unmixed = np.clip((rgb - (1.0 - aa) * key) / aa, 0.0, 1.0)  # gỡ màu nền khỏi pixel mép
        rgb = np.where(semi[..., None], unmixed, rgb)
        if spill:
            rgb = suppress_spill(rgb, alpha, key, int(th["spill_band_px"]))
    alpha = clean_alpha(alpha, th, keep_islands)
    return rgb.astype(np.float32), alpha


# ───────────────────────────── đo & cắt ─────────────────────────────

@dataclass
class Cut:
    """Khung đã xoá nền, cắt sát vùng có hình (toạ độ gốc = (ox, oy) trong khung nguồn)."""
    rgb: np.ndarray
    alpha: np.ndarray
    ox: int
    oy: int
    src_index: int


@dataclass
class Meas:
    x0: int
    y0: int
    x1: int
    y1: int
    ground_y: float
    feet_x: float
    body_x: float
    body_y: float
    area: float

    @property
    def height(self) -> int:
        return self.y1 - self.y0


def crop_cut(rgb, alpha, src_index, margin=2) -> Cut | None:
    ys, xs = np.nonzero(alpha > 0)
    if ys.size == 0:
        return None
    h, w = alpha.shape
    x0, x1 = max(0, xs.min() - margin), min(w, xs.max() + 1 + margin)
    y0, y1 = max(0, ys.min() - margin), min(h, ys.max() + 1 + margin)
    return Cut(rgb[y0:y1, x0:x1].copy(), alpha[y0:y1, x0:x1].copy(), int(x0), int(y0), src_index)


def measure_alpha(alpha: np.ndarray, ox: float = 0.0, oy: float = 0.0) -> Meas | None:
    solid = alpha > 0.5
    if not solid.any():
        return None
    ys, xs = np.nonzero(solid)
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
    width, height = x1 - x0, y1 - y0
    counts = solid.sum(axis=1)
    rows = np.nonzero(counts >= max(2, int(round(0.02 * width))))[0]
    ground = int(rows.max() + 1) if rows.size else y1
    band_h = max(2, int(round(0.06 * height)))
    band = alpha[max(0, ground - band_h):ground]
    xs_c = np.arange(alpha.shape[1], dtype=np.float64) + 0.5
    ys_c = np.arange(alpha.shape[0], dtype=np.float64) + 0.5
    bw = band.sum()
    feet_x = float((band.sum(axis=0) * xs_c).sum() / bw) if bw > 0 else (x0 + x1) / 2.0
    tot = float(alpha.sum())
    body_x = float((alpha.sum(axis=0) * xs_c).sum() / tot)
    body_y = float((alpha.sum(axis=1) * ys_c).sum() / tot)
    return Meas(x0 + int(ox), y0 + int(oy), x1 + int(ox), y1 + int(oy),
                ground + oy, feet_x + ox, body_x + ox, body_y + oy, tot)


def thumb(rgb, alpha, size=64) -> np.ndarray:
    pm = np.dstack([rgb * alpha[..., None], alpha]).astype(np.float32)
    h, w = alpha.shape
    s = size / max(h, w)
    return cv2.resize(pm, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA)


def tdiff(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.abs(a - b).mean())


def edge_map(rgb: np.ndarray, alpha: np.ndarray, size: int = 128) -> np.ndarray:
    """Đường viền bóng + nét bên trong (Canny) ở độ phân giải nhỏ – dùng đo khoảng dịch chuyển giữa 2 khung."""
    h, w = alpha.shape
    s = size / max(h, w)
    W, H = max(1, round(w * s)), max(1, round(h * s))
    a = cv2.resize(alpha, (W, H), interpolation=cv2.INTER_AREA)
    sil = a > 0.5
    e = sil & ~ndimage.binary_erosion(sil)
    comp = rgb * alpha[..., None] + 0.5 * (1 - alpha[..., None])
    g = cv2.resize(cv2.cvtColor(to_u8(comp), cv2.COLOR_RGB2GRAY), (W, H), interpolation=cv2.INTER_AREA)
    return e | (cv2.Canny(g, 40, 120) > 0)


def chamfer(e1: np.ndarray, e2: np.ndarray) -> float:
    """Khoảng cách chamfer đối xứng (px). Tăng gần tuyến tính theo độ dịch – không bão hoà như chênh pixel."""
    if not e1.any() or not e2.any():
        return float(max(e1.shape))
    d1 = ndimage.distance_transform_edt(~e1)
    d2 = ndimage.distance_transform_edt(~e2)
    return 0.5 * (float(d2[e1].mean()) + float(d1[e2].mean()))


def select_indices(thumbs: list[np.ndarray], n: int, loop: bool,
                   pick: list[int] | None = None, trim: list[int] | None = None) -> tuple[list[int], dict]:
    total = len(thumbs)
    if pick is not None:
        bad = [i for i in pick if not 0 <= int(i) < total]
        if bad:
            raise InputError(f"pick có chỉ số ngoài phạm vi 0..{total - 1}: {bad}")
        return [int(i) for i in pick], {"method": "pick"}
    s, e = (trim or [0, total])
    s, e = max(0, int(s)), min(total, int(e))
    if e - s <= n:
        return list(range(s, e)), {"method": "all"}
    if loop:
        d = np.array([tdiff(thumbs[s], thumbs[j]) for j in range(s, e)])
        dmax = d.max()
        rise = np.nonzero(d >= 0.5 * dmax)[0]
        start = max(int(rise[0]) if rise.size else n, n)
        if start < len(d):
            seg = d[start:]
            gmin = seg.min()
            j = start + int(np.nonzero(seg <= gmin * 1.25 + 1e-4)[0][0])
            while j + 1 < len(d) and d[j + 1] < d[j]:
                j += 1
            end = s + j  # khung end ≈ khung s → cửa sổ lặp [s, end)
            idx = np.round(np.linspace(s, end, n, endpoint=False)).astype(int).tolist()
            return idx, {"method": "auto_loop", "window": [s, end], "seam_diff": round(float(d[j]), 5)}
    idx = np.round(np.linspace(s, e - 1, n)).astype(int).tolist()
    return idx, {"method": "even", "window": [s, e]}


# ───────────────────────────── scale & đặt khung ─────────────────────────────

def resize_cut(cut: Cut, s: float) -> tuple[np.ndarray, np.ndarray, float, float]:
    h, w = cut.alpha.shape
    nw, nh = max(1, int(round(w * s))), max(1, int(round(h * s)))
    pm = np.dstack([cut.rgb * cut.alpha[..., None], cut.alpha]).astype(np.float32)
    interp = cv2.INTER_AREA if s < 1 else cv2.INTER_LANCZOS4
    out = np.clip(cv2.resize(pm, (nw, nh), interpolation=interp), 0.0, 1.0)
    if out.ndim == 2:  # ảnh 1px
        out = out.reshape(nh, nw, 4)
    a = out[..., 3]
    rgb = np.where(a[..., None] > 1e-4, out[..., :3] / np.maximum(a[..., None], 1e-4), 0.0)
    return np.clip(rgb, 0, 1).astype(np.float32), a.astype(np.float32), nw / w, nh / h


def anchor_series(meas: list[Meas], point_x: str, point_y: str, mode_x: str, mode_y: str):
    xs = np.array([m.feet_x if point_x == "feet" else m.body_x for m in meas], dtype=np.float64)
    ys = np.array([m.ground_y if point_y == "ground" else m.body_y for m in meas], dtype=np.float64)

    def apply(v, mode):
        if mode == "lock":
            return v
        if mode == "none" or len(v) < 3:
            return np.full_like(v, v[0])
        idx = np.arange(len(v))
        return np.polyval(np.polyfit(idx, v, 1), idx)  # detrend

    return apply(xs, mode_x), apply(ys, mode_y)


def bleed_rgb(rgb: np.ndarray, alpha: np.ndarray, band: int = 4) -> np.ndarray:
    """Tô màu pixel trong suốt sát mép bằng màu pixel có hình gần nhất – tránh viền đen khi Unity lọc song tuyến."""
    empty = alpha <= 0
    if not empty.any() or empty.all():
        return rgb
    dist, (iy, ix) = ndimage.distance_transform_edt(empty, return_indices=True)
    filled = rgb[iy, ix]
    out = np.where((empty & (dist <= band))[..., None], filled, rgb)
    return np.where((empty & (dist > band))[..., None], 0.0, out)


# ───────────────────────────── QA ─────────────────────────────

def color_stats(rgb: np.ndarray, alpha: np.ndarray) -> dict | None:
    m = alpha > 0.5
    if m.sum() < 20:
        return None
    lab = lab01(rgb[m][None])[0]
    ab = np.clip(lab[:, 1:], -63.9, 63.9)
    hist, _, _ = np.histogram2d(ab[:, 0], ab[:, 1], bins=16, range=[[-64, 64], [-64, 64]])
    hist = hist.astype(np.float64)
    hist /= max(hist.sum(), 1e-9)
    return {"L": float(lab[:, 0].mean()), "hist": hist}


def bhatt(h1: np.ndarray, h2: np.ndarray) -> float:
    return float(np.sqrt(max(0.0, 1.0 - float(np.sum(np.sqrt(h1 * h2))))))


def grade(value: float, lim: dict, higher_is_bad: bool = True) -> str:
    if higher_is_bad:
        return "fail" if value > lim["fail"] else "warn" if value > lim["warn"] else "pass"
    return "fail" if value < lim["fail"] else "warn" if value < lim["warn"] else "pass"


def qa_animation(name: str, a: dict, frames: list[np.ndarray], th: dict, key_rgb: list[int] | None,
                 ref: dict | None, stab: tuple[str, str], pivot: tuple[int, int]) -> list[dict]:
    checks: list[dict] = []

    def add(check, status, value=None, limit=None, msg=""):
        checks.append({"scope": name, "check": check, "status": status,
                       "value": value, "limit": limit, "msg": msg})

    n_spec, fps = a["frames"], float(a["fps"])
    frame_ms = 1000.0 / fps
    add("frame_count", "pass" if len(frames) == n_spec else "fail", len(frames), n_spec,
        "" if len(frames) == n_spec else f"có {len(frames)} khung, spec cần {n_spec}")

    alphas = [f[..., 3] for f in frames]
    rgbs = [f[..., :3] for f in frames]
    meas = [measure_alpha(al) for al in alphas]
    empty = [i for i, m in enumerate(meas) if m is None]
    if empty:
        add("empty_frame", "fail", empty, None, "khung trống")
        return checks

    # nền & mép
    key = None if key_rgb is None else np.asarray(key_rgb, np.float32) / 255.0
    halo_ratios, residue_ratios, stray = [], [], 0
    for rgb, al in zip(rgbs, alphas):
        stray += int(((al > 0) & (al < th["alpha_floor"] * 0.5)).sum())
        if key is None:
            continue
        d = np.linalg.norm(lab01(rgb) - lab01(key[None])[0], axis=-1)
        edge = (al > 0.02) & (al < 0.98)
        halo_ratios.append(float(((d < th["halo_de"]) & edge).sum()) / max(1, int(edge.sum())))
        solid = al > 0.5
        residue_ratios.append(float(((d < th["key_residue_de"]) & solid).sum()) / max(1, int(solid.sum())))
    if key is not None:
        v = max(halo_ratios)
        add("halo", grade(v, th["halo_ratio"]), round(v, 4), th["halo_ratio"],
            "viền ám màu nền ở mép" if v > th["halo_ratio"]["warn"] else "")
        v = max(residue_ratios)
        add("key_residue", grade(v, th["key_residue_ratio"]), round(v, 4), th["key_residue_ratio"],
            "còn mảng màu khoá bên trong quái (nền sót hoặc quái trùng màu khoá → đổi màu khoá)"
            if v > th["key_residue_ratio"]["warn"] else "")
    add("stray_alpha", "pass" if stray == 0 else "warn", stray, 0,
        "" if stray == 0 else "còn pixel gần trong suốt rời rạc")

    # ổn định
    heights = np.array([m.height for m in meas], np.float64)
    h_med = float(np.median(heights))
    areas = np.array([m.area for m in meas], np.float64)
    locked = stab[0] == "lock" and stab[1] == "lock"
    if len(frames) > 1:
        cx = np.array([m.body_x for m in meas])
        cy = np.array([m.body_y for m in meas])
        steps = np.hypot(np.diff(cx), np.diff(cy)) / max(h_med, 1.0)
        v = float(steps.max())
        st = grade(v, th["pop_step"]) if locked else ("warn" if v > th["pop_step"]["fail"] * 2 else "pass")
        add("pop", st, round(v, 4), th["pop_step"],
            f"trọng tâm nhảy mạnh ở khung {int(steps.argmax()) + 1}" if st != "pass" else "")
        fl = np.abs(np.diff(areas)) / max(float(np.median(areas)), 1.0)
        v = float(fl.max())
        st = grade(v, th["area_flicker"])
        if not locked and st == "fail":
            st = "warn"
        add("area_flicker", st, round(v, 4), th["area_flicker"],
            f"diện tích đổi đột ngột ở khung {int(fl.argmax()) + 1} (biến hình/nhấp nháy)" if st != "pass" else "")
        if locked:
            gx = np.array([m.feet_x for m in meas])
            gy = np.array([m.ground_y for m in meas])
            jit = float(max(gx.max() - gx.min(), gy.max() - gy.min()))
            add("anchor_jitter_px", "pass" if jit <= 1.5 else "warn", round(jit, 2), 1.5,
                "" if jit <= 1.5 else "điểm neo còn rung sau khi khoá")

    # vòng lặp
    if a.get("loop") and len(frames) >= 3:
        em = [edge_map(r, al) for r, al in zip(rgbs, alphas)]
        steps = [chamfer(em[i], em[i + 1]) for i in range(len(em) - 1)]
        typical = max(float(np.median(steps)), 0.25)
        ratio = chamfer(em[-1], em[0]) / typical
        add("loop_seam", grade(ratio, th["loop_seam_ratio"]), round(ratio, 3), th["loop_seam_ratio"],
            "khung cuối không nối mượt về khung đầu" if ratio > th["loop_seam_ratio"]["warn"] else "")

    # màu
    if ref is not None:
        worst_b, worst_l, worst_i = 0.0, 0.0, 0
        for i, (rgb, al) in enumerate(zip(rgbs, alphas)):
            cs = color_stats(rgb, al)
            if cs is None:
                continue
            b = bhatt(cs["hist"], ref["hist"])
            dl = abs(cs["L"] - ref["L"])
            if b > worst_b:
                worst_b, worst_i = b, i
            worst_l = max(worst_l, dl)
        st = grade(worst_b, th["color_drift_bhatt"])
        add("color_drift", st, round(worst_b, 4), th["color_drift_bhatt"],
            f"màu lệch so với ảnh chuẩn, nặng nhất khung {worst_i}" if st != "pass" else "")
        st = grade(worst_l, th["color_drift_dl"])
        add("brightness_drift", st, round(worst_l, 2), th["color_drift_dl"],
            "độ sáng lệch so với ảnh chuẩn" if st != "pass" else "")

    # bảng màu (chỉ khi spec khai báo palette)
    pal = a.get("_palette")
    if pal:
        pal_lab = lab01(np.asarray(pal, np.float32)[None] / 255.0)[0]
        ins, tot = 0, 0
        for rgb, al in zip(rgbs, alphas):
            m = al > 0.5
            lab = lab01(rgb[m][None])[0]
            dmin = np.min(np.linalg.norm(lab[:, None, :] - pal_lab[None], axis=-1), axis=1)
            ins += int((dmin <= th["palette_de"]).sum())
            tot += int(m.sum())
        r = ins / max(tot, 1)
        add("palette", "pass" if r >= th["palette_in_ratio_min"] else "fail", round(r, 4),
            th["palette_in_ratio_min"], "" if r >= th["palette_in_ratio_min"] else "nhiều pixel ngoài bảng màu")

    # nhịp thời gian – phải khớp thời lượng/sự kiện cũ để server và client không lệch
    dur = len(frames) * frame_ms
    if a.get("target_duration_ms") is not None:
        diff = abs(dur - float(a["target_duration_ms"]))
        add("duration", "pass" if diff <= frame_ms + 1e-6 else "fail", round(dur, 1), a["target_duration_ms"],
            "" if diff <= frame_ms + 1e-6 else f"thời lượng {dur:.0f}ms lệch mục tiêu {a['target_duration_ms']}ms quá 1 khung")
    for ev in parse_events(a):
        if not 0 <= ev["frame"] < len(frames):
            add(f"event:{ev['name']}", "fail", ev["frame"], [0, len(frames) - 1], "sự kiện nằm ngoài dải khung")
            continue
        if ev["target_ms"] is not None:
            t = ev["frame"] * frame_ms
            diff = abs(t - ev["target_ms"])
            add(f"event:{ev['name']}", "pass" if diff <= frame_ms + 1e-6 else "fail", round(t, 1), ev["target_ms"],
                "" if diff <= frame_ms + 1e-6 else f"sự kiện {ev['name']} lệch {diff:.0f}ms so với nhịp cũ")
    return checks


def summarize(checks: list[dict]) -> str:
    sts = {c["status"] for c in checks}
    return "fail" if "fail" in sts else "warn" if "warn" in sts else "pass"


# ───────────────────────────── xuất ─────────────────────────────

def to_u8(img: np.ndarray) -> np.ndarray:
    return np.clip(np.round(img * 255.0), 0, 255).astype(np.uint8)


def checker(h, w, s=8) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w]
    c = (((yy // s) + (xx // s)) % 2).astype(np.float32)
    return (0.78 + 0.12 * c)[..., None].repeat(3, axis=2)


def contact_sheet(anims: list[dict], cell_w: int, cell_h: int, pivot: tuple[int, int],
                  status: dict[str, str], max_w: int = 1600) -> np.ndarray:
    """Bảng xem trước cho người duyệt và cho AI tự soát: nền ca-rô, chữ thập đỏ = pivot, vạch xanh = mặt đất."""
    cols = max(len(x["frames"]) for x in anims)
    label_h = 22
    W, H = cols * cell_w, len(anims) * (cell_h + label_h)
    sheet = np.full((H, W, 3), 255, np.uint8)
    colors = {"pass": (25, 140, 50), "warn": (215, 140, 0), "fail": (205, 25, 25)}
    for r, an in enumerate(anims):
        y = r * (cell_h + label_h)
        txt = (f"{an['name']}  {len(an['frames'])}f @{an['fps']:g}fps  "
               f"{'loop' if an['loop'] else 'once'}  QA:{status[an['name']].upper()}")
        cv2.putText(sheet, txt, (4, y + 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    colors[status[an["name"]]], 1, cv2.LINE_AA)
        for c, f in enumerate(an["frames"]):
            x = c * cell_w
            a = f[..., 3:4]
            tile = to_u8(f[..., :3] * a + checker(cell_h, cell_w) * (1 - a)).copy()
            px, py = pivot
            cv2.line(tile, (0, py), (cell_w - 1, py), (0, 180, 205), 1)
            cv2.line(tile, (px - 5, py), (px + 5, py), (230, 0, 0), 1)
            cv2.line(tile, (px, py - 5), (px, py + 5), (230, 0, 0), 1)
            cv2.rectangle(tile, (0, 0), (cell_w - 1, cell_h - 1), (150, 150, 150), 1)
            cv2.putText(tile, str(c), (3, 12), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (50, 50, 50), 1, cv2.LINE_AA)
            for ev in an["events"]:
                if ev["frame"] == c:
                    cv2.putText(tile, ev["name"], (3, cell_h - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                                (215, 0, 0), 1, cv2.LINE_AA)
            sheet[y + label_h:y + label_h + cell_h, x:x + cell_w] = tile
    if W > max_w:
        s = max_w / W
        sheet = cv2.resize(sheet, (max_w, max(1, int(H * s))), interpolation=cv2.INTER_AREA)
    return sheet


def save_gif(path: Path, frames: list[np.ndarray], fps: float, pivot_y: int) -> None:
    imgs = []
    for f in frames:
        a = f[..., 3:4]
        bg = np.full(f.shape[:2] + (3,), 0.5, np.float32)
        bg[pivot_y:pivot_y + 1] = (0.2, 0.8, 0.9)
        imgs.append(Image.fromarray(to_u8(f[..., :3] * a + bg * (1 - a))))
    imgs[0].save(path, save_all=True, append_images=imgs[1:], duration=int(round(1000 / fps)), loop=0, disposal=2)


def write_report_md(path: Path, monster_id: str, status: str, checks: list[dict]) -> None:
    icon = {"pass": "✅", "warn": "⚠️", "fail": "❌"}
    lines = [f"# QA khung FBF – `{monster_id}`", "", f"**Kết luận: {icon[status]} {status.upper()}**", "",
             "| Phạm vi | Kiểm tra | KQ | Giá trị | Ngưỡng | Ghi chú |", "|---|---|---|---|---|---|"]
    for c in checks:
        lim = c["limit"]
        lim_s = (f"warn {lim['warn']} / fail {lim['fail']}" if isinstance(lim, dict) else
                 ("" if lim is None else json.dumps(lim, ensure_ascii=False)))
        lines.append(f"| {c['scope']} | {c['check']} | {icon[c['status']]} | "
                     f"{json.dumps(c['value'], ensure_ascii=False)} | {lim_s} | {c['msg']} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ───────────────────────────── chạy chính ─────────────────────────────

def load_reference(spec: dict, spec_dir: Path, th: dict) -> dict | None:
    ref_path = spec.get("style_reference")
    if not ref_path:
        return None
    p = (spec_dir / ref_path) if not Path(ref_path).is_absolute() else Path(ref_path)
    if not p.exists():
        raise InputError(f"Không thấy ảnh chuẩn style_reference: {p}")
    img = load_still(p)
    key, _ = resolve_key(img, spec.get("background", {"mode": "auto"}), th)
    rgb, al = remove_background(img, key, th)
    return color_stats(rgb, al)


def build(spec: dict, input_dir: Path, out_dir: Path, th: dict, spec_dir: Path,
          sources: dict[str, Path] | None = None) -> dict:
    """sources: {tên anim: thư mục/file} để chỉ định nguồn riêng, thay cho input_dir/<anim>."""
    validate_spec(spec)
    sources = {k: Path(v) for k, v in (sources or {}).items()}
    mid = spec["monster_id"]
    bgcfg = spec.get("background", {"mode": "auto"})
    anchor = spec.get("anchor", {})
    px_kind, py_kind = anchor.get("x", "feet"), anchor.get("y", "ground")
    target_h = float(spec["target_height_px"])
    pad = int(spec.get("padding_px", 4))
    scale_mode = spec.get("scale_mode", "global")

    # 1) nạp, xoá nền, chọn khung
    anims = []
    for name, a in spec["animations"].items():
        kind, files = source_from_path(sources[name]) if name in sources else find_source(input_dir, name)
        it = iter_frames(kind, files)
        try:
            first = next(it)
        except StopIteration:
            raise InputError(f"[{name}] nguồn rỗng")
        key, key_info = resolve_key(first, bgcfg, th)
        cuts, thumbs = [], []

        def take(fr, i):
            rgb, al = remove_background(fr, key, th, spill=bgcfg.get("spill", True),
                                        keep_islands=bgcfg.get("keep_islands", False))
            thumbs.append(thumb(rgb, al))
            cuts.append(crop_cut(rgb, al, i))

        take(first, 0)
        for i, fr in enumerate(it, start=1):
            take(fr, i)
        idx, sel = select_indices(thumbs, a["frames"], bool(a.get("loop")), a.get("pick"), a.get("trim"))
        chosen = [cuts[i] for i in idx]
        if any(c is None for c in chosen):
            bad = [idx[k] for k, c in enumerate(chosen) if c is None]
            raise InputError(f"[{name}] khung trống sau khi xoá nền (chỉ số nguồn {bad})")
        meas = [measure_alpha(c.alpha, c.ox, c.oy) for c in chosen]
        if any(m is None for m in meas):
            raise InputError(f"[{name}] có khung không còn vùng đặc (alpha>0.5) sau khi xoá nền")
        src_files = ([files[i] for i in idx] if kind == "frames" else [files[0]])
        anims.append({"name": name, "spec": a, "kind": kind, "files": files, "src_files": src_files,
                      "src_count": len(cuts), "src_fps": video_fps(files[0]) if kind == "video" else None,
                      "key": key, "key_info": key_info, "idx": idx, "sel": sel,
                      "cuts": chosen, "meas": meas, "stab": stabilize_modes(name, a)})

    # 2) scale
    first_h = {an["name"]: an["meas"][0].height for an in anims}
    if scale_mode == "fixed":
        scales = {an["name"]: float(spec["scale"]) for an in anims}
    elif scale_mode == "first_frame":
        scales = {n: target_h / max(h, 1) for n, h in first_h.items()}
    else:
        ref_name = spec.get("scale_reference", anims[0]["name"])
        ref = next((an for an in anims if an["name"] == ref_name), None)
        if ref is None:
            raise InputError(f"scale_reference '{ref_name}' không có trong animations")
        s = target_h / max(float(np.median([m.height for m in ref["meas"]])), 1.0)
        scales = {an["name"]: s for an in anims}

    # 3) neo + resize
    placed = []  # (anim_i, rgb, alpha, lx, ly)
    for ai, an in enumerate(anims):
        s = scales[an["name"]]
        ax, ay = anchor_series(an["meas"], px_kind, py_kind, *an["stab"])
        an["anchor_src"] = list(zip(ax.tolist(), ay.tolist()))
        for k, cut in enumerate(an["cuts"]):
            rgb, al, sx, sy = resize_cut(cut, s)
            al = np.where(al < th["alpha_floor"], 0.0, al)
            placed.append((ai, rgb, al, (ax[k] - cut.ox) * sx, (ay[k] - cut.oy) * sy))

    left = max(p[3] for p in placed)
    right = max(p[2].shape[1] - p[3] for p in placed)
    top = max(p[4] for p in placed)
    bottom = max(p[2].shape[0] - p[4] for p in placed)
    L, R = int(math.ceil(left)) + pad, int(math.ceil(right)) + pad
    T, B = int(math.ceil(top)) + pad, int(math.ceil(max(bottom, 0))) + pad
    cell_w, cell_h = L + R, T + B
    pivot = (L, T)

    out_frames: list[list[np.ndarray]] = [[] for _ in anims]
    for ai, rgb, al, lx, ly in placed:
        cell = np.zeros((cell_h, cell_w, 4), np.float32)
        ox, oy = int(round(L - lx)), int(round(T - ly))
        h, w = al.shape
        x0, y0 = max(0, ox), max(0, oy)
        x1, y1 = min(cell_w, ox + w), min(cell_h, oy + h)
        cell[y0:y1, x0:x1, :3] = rgb[y0 - oy:y1 - oy, x0 - ox:x1 - ox]
        cell[y0:y1, x0:x1, 3] = al[y0 - oy:y1 - oy, x0 - ox:x1 - ox]
        out_frames[ai].append(cell)

    # 4) atlas: lưới ô đều, xếp nối tiếp theo thứ tự anim, chọn số cột cho atlas gần vuông nhất
    total = sum(len(f) for f in out_frames)
    cols = min(range(1, total + 1),
               key=lambda c: (max(c * cell_w, math.ceil(total / c) * cell_h),
                              c * cell_w * math.ceil(total / c) * cell_h))
    atlas_w, atlas_h = cols * cell_w, math.ceil(total / cols) * cell_h
    atlas = np.zeros((atlas_h, atlas_w, 4), np.float32)
    rects: list[list[tuple[int, int]]] = []
    k = 0
    for frames in out_frames:
        rects.append([])
        for f in frames:
            x, y = (k % cols) * cell_w, (k // cols) * cell_h
            atlas[y:y + cell_h, x:x + cell_w] = f
            rects[-1].append((x, y))
            k += 1
    atlas[..., :3] = bleed_rgb(atlas[..., :3], atlas[..., 3])
    out_dir.mkdir(parents=True, exist_ok=True)
    atlas_path = out_dir / f"{mid}.png"
    Image.fromarray(to_u8(atlas), "RGBA").save(atlas_path, optimize=True)

    # 5) QA
    ref_stats = load_reference(spec, spec_dir, th)
    ref_source = "style_reference"
    if ref_stats is None:
        f0 = out_frames[0][0]
        ref_stats = color_stats(f0[..., :3], f0[..., 3])
        ref_source = f"{anims[0]['name']}[0]"
    checks: list[dict] = []
    pal = spec.get("palette")
    for ai, an in enumerate(anims):
        a = dict(an["spec"])
        if pal:
            a["_palette"] = [[int(h[i:i + 2], 16) for i in (1, 3, 5)] for h in pal]
        checks += qa_animation(an["name"], a, out_frames[ai], th, an["key_info"].get("key_rgb"),
                               ref_stats, an["stab"], pivot)
    # cấp quái
    ratios = {an["name"]: first_h[an["name"]] * scales[an["name"]] / target_h for an in anims}
    spread = max(ratios.values()) / max(min(ratios.values()), 1e-6)
    checks.append({"scope": mid, "check": "scale_spread", "status": grade(spread, th["scale_spread"]),
                   "value": round(spread, 3), "limit": th["scale_spread"],
                   "msg": ("chiều cao khung đầu giữa các anim chênh nhau – nguồn sinh khác cỡ? cân nhắc scale_mode=first_frame"
                           if spread > th["scale_spread"]["warn"] else ""),
                   "detail": {k: round(v, 3) for k, v in ratios.items()}})
    big = max(atlas_w, atlas_h)
    st = "fail" if big > th["atlas_max_px"] else "warn" if big > th["atlas_budget_px"] else "pass"
    checks.append({"scope": mid, "check": "atlas_size", "status": st, "value": [atlas_w, atlas_h],
                   "limit": {"warn": th["atlas_budget_px"], "fail": th["atlas_max_px"]},
                   "msg": "" if st == "pass" else "atlas vượt ngân sách – giảm target_height_px hoặc số khung"})
    status = summarize(checks)
    per_anim = {an["name"]: summarize([c for c in checks if c["scope"] == an["name"]]) for an in anims}

    # 6) manifest
    prov_path = input_dir / "provenance.json"
    provenance = json.loads(prov_path.read_text("utf-8")) if prov_path.exists() else {}
    man_anims = []
    for r, an in enumerate(anims):
        a = an["spec"]
        fps = float(a["fps"])
        frames_meta = []
        for c, f in enumerate(out_frames[r]):
            m = measure_alpha(f[..., 3])
            x, y = rects[r][c]
            src = (an["src_files"][c].name if an["kind"] == "frames" else an["src_files"][0].name)
            frames_meta.append({
                "i": c,
                "rect": {"x": x, "y": y, "w": cell_w, "h": cell_h},
                "rect_unity": {"x": x, "y": atlas_h - y - cell_h, "w": cell_w, "h": cell_h},
                "bbox": ({"x": m.x0, "y": m.y0, "w": m.x1 - m.x0, "h": m.y1 - m.y0} if m else None),
                "source": {"file": src, "index": int(an["idx"][c])},
            })
        man_anims.append({
            "name": an["name"], "fps": fps, "loop": bool(a.get("loop", False)),
            "frame_count": len(out_frames[r]), "duration_ms": round(len(out_frames[r]) * 1000.0 / fps, 2),
            "events": [{"name": e["name"], "frame": e["frame"], "time_ms": round(e["frame"] * 1000.0 / fps, 2),
                        "target_ms": e["target_ms"]} for e in parse_events(a)],
            "stabilize": {"x": an["stab"][0], "y": an["stab"][1]},
            "scale": round(scales[an["name"]], 6),
            "selection": an["sel"], "source_frame_count": an["src_count"], "source_fps": an["src_fps"],
            "background": an["key_info"],
            "provenance": anim_provenance(input_dir, an["name"],
                                          sources[an["name"]] if an["name"] in sources and sources[an["name"]].is_dir() else None),
            "frames": frames_meta,
        })
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "monster_id": mid,
        "display_name": spec.get("display_name"),
        "atlas": {"file": atlas_path.name, "w": atlas_w, "h": atlas_h, "sha256": sha256_file(atlas_path)},
        "cell": {"w": cell_w, "h": cell_h},
        "pivot": {"x_px": pivot[0], "y_px": pivot[1],
                  "x_norm": round(pivot[0] / cell_w, 6), "y_norm": round(1.0 - pivot[1] / cell_h, 6),
                  "note": "x_px/y_px tính từ góc trên-trái ô; x_norm/y_norm theo quy ước Unity (gốc dưới-trái)"},
        "pixels_per_unit": spec.get("pixels_per_unit"),
        "anchor": {"x": px_kind, "y": py_kind},
        "scale_mode": scale_mode,
        "target_height_px": target_h,
        "animations": man_anims,
        "provenance": {
            **provenance,
            "sources": [{"anim": an["name"], "file": p.name, "sha256": sha256_file(p)}
                        for an in anims for p in an["src_files"]],
        },
        "postprocess": {"tool": "postprocess.py", "version": VERSION, "spec_sha256": sha256_json(spec),
                        "thresholds": th},
        "qa": {"status": status, "per_animation": per_anim, "reference": ref_source,
               "report": "qa_report.json"},
        "qa_reference": {"L": ref_stats["L"], "hist": ref_stats["hist"].tolist()} if ref_stats else None,
        "spec": spec,
    }
    (out_dir / f"{mid}.fbf.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "qa_report.json").write_text(json.dumps({"monster_id": mid, "status": status, "checks": checks},
                                                       ensure_ascii=False, indent=2), encoding="utf-8")
    write_report_md(out_dir / "qa_report.md", mid, status, checks)

    # 7) xem trước
    prev = out_dir / "preview"
    prev.mkdir(exist_ok=True)
    sheet_input = [{"name": an["name"], "frames": out_frames[r], "fps": float(an["spec"]["fps"]),
                    "loop": bool(an["spec"].get("loop")), "events": parse_events(an["spec"])}
                   for r, an in enumerate(anims)]
    Image.fromarray(contact_sheet(sheet_input, cell_w, cell_h, pivot, per_anim)).save(prev / "contact_sheet.png")
    for r, an in enumerate(anims):
        save_gif(prev / f"{an['name']}.gif", out_frames[r], float(an["spec"]["fps"]), pivot[1])

    return {"status": status, "manifest": manifest, "checks": checks, "out_dir": str(out_dir)}


def check_manifest(manifest_path: Path, th_override: dict | None = None) -> dict:
    """Chạy lại QA từ atlas + manifest (vd. sau khi hoạ sĩ sửa tay atlas)."""
    man = json.loads(manifest_path.read_text("utf-8"))
    if man.get("schema") != MANIFEST_SCHEMA:
        raise InputError(f"Manifest không đúng schema {MANIFEST_SCHEMA}")
    th = th_override or man["postprocess"]["thresholds"]
    atlas = load_still(manifest_path.parent / man["atlas"]["file"])
    spec = man["spec"]
    ref = man.get("qa_reference")
    ref_stats = {"L": ref["L"], "hist": np.asarray(ref["hist"])} if ref else None
    pivot = (man["pivot"]["x_px"], man["pivot"]["y_px"])
    checks = []
    for an in man["animations"]:
        frames = []
        for fm in an["frames"]:
            r = fm["rect"]
            frames.append(atlas[r["y"]:r["y"] + r["h"], r["x"]:r["x"] + r["w"]].copy())
        a = dict(spec["animations"][an["name"]])
        if spec.get("palette"):
            a["_palette"] = [[int(h[i:i + 2], 16) for i in (1, 3, 5)] for h in spec["palette"]]
        stab = (an["stabilize"]["x"], an["stabilize"]["y"])
        checks += qa_animation(an["name"], a, frames, th, an["background"].get("key_rgb"), ref_stats, stab, pivot)
    big = max(man["atlas"]["w"], man["atlas"]["h"])
    st = "fail" if big > th["atlas_max_px"] else "warn" if big > th["atlas_budget_px"] else "pass"
    checks.append({"scope": man["monster_id"], "check": "atlas_size", "status": st,
                   "value": [man["atlas"]["w"], man["atlas"]["h"]],
                   "limit": {"warn": th["atlas_budget_px"], "fail": th["atlas_max_px"]}, "msg": ""})
    actual_sha = sha256_file(manifest_path.parent / man["atlas"]["file"])
    edited = actual_sha != man["atlas"]["sha256"]
    return {"status": summarize(checks), "checks": checks, "atlas_edited": edited}


# ───────────────────────────── CLI ─────────────────────────────

def _print_summary(status: str, checks: list[dict]) -> None:
    icon = {"pass": "OK  ", "warn": "WARN", "fail": "FAIL"}
    for c in checks:
        if c["status"] != "pass":
            print(f"  [{icon[c['status']]}] {c['scope']}.{c['check']} = {c['value']}  {c['msg']}")
    print(f"QA: {status.upper()}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Hậu xử lý khung FBF cho quái (asset-gen).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="xử lý nguồn thô → atlas + manifest + QA")
    r.add_argument("--spec", required=True)
    r.add_argument("--input", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--thresholds")
    r.add_argument("--strict", action="store_true", help="coi cảnh báo là lỗi")
    c = sub.add_parser("check", help="chạy lại QA trên atlas + manifest đã có")
    c.add_argument("--manifest", required=True)
    c.add_argument("--thresholds")
    c.add_argument("--strict", action="store_true")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "run":
            spec_path = Path(args.spec)
            spec = json.loads(spec_path.read_text("utf-8"))
            res = build(spec, Path(args.input), Path(args.out), load_thresholds(args.thresholds), spec_path.parent)
            print(f"Atlas: {Path(args.out) / res['manifest']['atlas']['file']}  "
                  f"({res['manifest']['atlas']['w']}x{res['manifest']['atlas']['h']}, ô {res['manifest']['cell']['w']}x{res['manifest']['cell']['h']})")
        else:
            th = load_thresholds(args.thresholds) if args.thresholds else None
            res = check_manifest(Path(args.manifest), th)
            if res["atlas_edited"]:
                print("Lưu ý: atlas đã bị sửa sau lần xử lý (sha256 khác manifest).")
        _print_summary(res["status"], res["checks"])
        if res["status"] == "fail" or (args.strict and res["status"] == "warn"):
            return 2
        return 0
    except InputError as e:
        print(f"LỖI: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
