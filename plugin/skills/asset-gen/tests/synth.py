# -*- coding: utf-8 -*-
"""Sinh quái giả lập để test hậu xử lý: thân elip, đầu, 2 chân đánh nhịp, đuôi, vẽ khử răng cưa trên nền màu khoá."""
from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

KEY = (0, 255, 0)


def draw_monster(phase: float = 0.0, offset=(0, 0), size=(512, 512), scale: float = 1.0, key=KEY,
                 tail_deg: float | None = None, swap_colors: bool = False, bg=None) -> np.ndarray:
    w, h = size
    img = np.empty((h, w, 3), np.uint8)
    img[:] = key
    if bg is not None:
        img[:] = bg
    s = scale
    cx = w // 2 + int(offset[0])
    ground = int(h * 0.85) + int(offset[1])
    leg_h, leg_w = int(70 * s), max(2, int(18 * s))
    swing = int(round(14 * s * math.sin(phase)))
    for sign in (-1, 1):
        lx = cx + sign * int(30 * s) + sign * swing
        cv2.rectangle(img, (lx - leg_w // 2, ground - leg_h), (lx + leg_w // 2, ground - 1), (90, 60, 30), -1)
    body_cy = ground - leg_h - int(40 * s) + int(round(4 * s * math.sin(2 * phase)))
    # đuôi theo cos để chu kỳ không đối xứng nửa vòng (khung pha π khác khung pha 0)
    ang = math.radians(30 * math.cos(phase) if tail_deg is None else tail_deg)
    tx0, ty0 = cx - int(80 * s), body_cy
    tx1 = int(tx0 - 70 * s * math.cos(ang))
    ty1 = int(ty0 - 70 * s * math.sin(ang))
    cv2.line(img, (tx0, ty0), (tx1, ty1), (120, 80, 40), max(2, int(14 * s)), cv2.LINE_AA)
    cv2.ellipse(img, (cx, body_cy), (int(90 * s), int(55 * s)), 0, 0, 360, (140, 90, 50), -1, cv2.LINE_AA)
    cv2.circle(img, (cx + int(85 * s), body_cy - int(45 * s)), int(38 * s), (170, 120, 70), -1, cv2.LINE_AA)
    cv2.circle(img, (cx + int(98 * s), body_cy - int(55 * s)), max(2, int(7 * s)), (240, 240, 220), -1, cv2.LINE_AA)
    cv2.ellipse(img, (cx - int(20 * s), body_cy), (int(25 * s), int(12 * s)), 0, 0, 360, (200, 40, 40), -1, cv2.LINE_AA)
    if swap_colors:
        bgmask = np.all(img == np.array(key, np.uint8), axis=-1)
        sw = img[..., ::-1].copy()
        sw[bgmask] = key
        img = sw
    return img


def write_frames(d: Path, n: int, jitter: int = 0, seed: int = 0, period: int | None = None,
                 lunge_px: int = 0, tail_sweep: bool = False, swap_at: int | None = None, **kw) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    for i in range(n):
        off = rng.integers(-jitter, jitter + 1, 2) if jitter else np.array([0, 0])
        off = off + np.array([lunge_px * i, 0])
        phase = 2 * math.pi * i / (period or n)
        tail = (60.0 * i / max(1, n - 1)) if tail_sweep else None
        img = draw_monster(phase, off, tail_deg=tail, swap_colors=(swap_at == i), **kw)
        Image.fromarray(img).save(d / f"{i:04d}.png")
    return d


def write_video(path: Path, n: int, period: int, fps: int = 16, **kw) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    first = draw_monster(0.0, **kw)
    h, w = first.shape[:2]
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    if not vw.isOpened():
        return False
    for i in range(n):
        img = draw_monster(2 * math.pi * i / period, **kw)
        vw.write(cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    vw.release()
    return path.exists() and path.stat().st_size > 0


def write_rgba_frames(d: Path, n: int, **kw) -> Path:
    """Khung PNG đã có alpha thật (như khi ComfyUI có node tách nền)."""
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        img = draw_monster(2 * math.pi * i / n, **kw).astype(np.float32)
        key = np.array(KEY, np.float32)
        a = (np.linalg.norm(img - key, axis=-1) > 60).astype(np.float32)
        a = cv2.GaussianBlur(a, (3, 3), 0)
        rgba = np.dstack([img, a * 255]).astype(np.uint8)
        Image.fromarray(rgba, "RGBA").save(d / f"{i:04d}.png")
    return d
