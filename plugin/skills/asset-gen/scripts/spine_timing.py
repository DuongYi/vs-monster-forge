#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
spine_timing.py — Lấy tên animation, thời lượng và mốc sự kiện từ file Spine JSON của quái cũ,
sinh "spec mồi" cho postprocess.py. Mục đích: bản FBF giữ đúng tên anim, tên sự kiện và nhịp
thời gian như bản Spine, để code client (BossData.UseSkill…) và server không phải đổi.

Dùng:
  python spine_timing.py path/to/monster.json --fps 12 --monster-id mob_lang_dau_01 \
         --target-height 180 --out specs/mob_lang_dau_01.json [--only idle,run,attack,hurt,die]

Chỉ đọc được Spine **JSON** (Spine Editor ▸ Export ▸ JSON). File .skel nhị phân cần xuất lại sang JSON.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

LOOP_HINTS = ("idle", "stand", "run", "walk", "move", "fly")


def collect_times(node) -> list[float]:
    """Gom mọi mốc 'time' trong cây timeline của một animation (bones, slots, deform, drawOrder…)."""
    out: list[float] = []
    if isinstance(node, list):
        for item in node:
            if isinstance(item, dict) and "time" in item:
                try:
                    out.append(float(item["time"]))
                except (TypeError, ValueError):
                    pass
            out += collect_times(item)
    elif isinstance(node, dict):
        for v in node.values():
            out += collect_times(v)
    return out


def read_spine(path: Path) -> dict:
    data = json.loads(path.read_text("utf-8"))
    if "animations" not in data:
        raise ValueError("Không thấy khoá 'animations' – có phải file Spine JSON không?")
    result = {"spine_version": (data.get("skeleton") or {}).get("spine"), "animations": {}}
    for name, anim in data["animations"].items():
        times = collect_times(anim)
        events = []
        for ev in anim.get("events", []) or []:
            events.append({"name": ev.get("name"), "time": float(ev.get("time", 0.0))})
        result["animations"][name] = {"duration_s": max(times) if times else 0.0, "events": events}
    return result


def seed_spec(info: dict, fps: float, monster_id: str, target_height: int, only: list[str] | None,
              source: str) -> dict:
    anims = {}
    warnings = []
    for name, a in info["animations"].items():
        if only and name not in only:
            continue
        dur = a["duration_s"]
        frames = max(1, int(round(dur * fps)))
        if dur <= 0:
            warnings.append(f"{name}: thời lượng 0 (anim tư thế tĩnh) – đặt tạm 1 khung")
        entry = {
            "frames": frames,
            "fps": fps,
            "loop": any(name.lower().startswith(h) for h in LOOP_HINTS),
            "target_duration_ms": round(dur * 1000.0, 1),
        }
        if a["events"]:
            ev_frames: dict[str, list[int]] = {}
            ev_ms: dict[str, list[float]] = {}
            for ev in a["events"]:
                fr = min(frames - 1, int(round(ev["time"] * fps)))
                ev_frames.setdefault(ev["name"], []).append(fr)
                ev_ms.setdefault(ev["name"], []).append(round(ev["time"] * 1000.0, 1))
            entry["events"] = {k: (v[0] if len(v) == 1 else v) for k, v in ev_frames.items()}
            entry["target_events_ms"] = {k: (v[0] if len(v) == 1 else v) for k, v in ev_ms.items()}
        anims[name] = entry
    if only:
        missing = [n for n in only if n not in anims]
        if missing:
            warnings.append(f"không có trong file Spine: {', '.join(missing)}")
    return {
        "monster_id": monster_id,
        "display_name": None,
        "target_height_px": target_height,
        "anchor": {"x": "feet", "y": "ground"},
        "scale_mode": "global",
        "padding_px": 4,
        "background": {"mode": "auto"},
        "style_reference": None,
        "animations": anims,
        "_source": {"spine_json": source, "spine_version": info.get("spine_version"), "fps": fps,
                    "warnings": warnings},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Sinh spec FBF mồi từ Spine JSON (giữ tên anim, nhịp, sự kiện).")
    ap.add_argument("spine_json")
    ap.add_argument("--fps", type=float, default=12.0)
    ap.add_argument("--monster-id")
    ap.add_argument("--target-height", type=int, default=180)
    ap.add_argument("--only", help="danh sách anim cần lấy, cách nhau dấu phẩy")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    p = Path(args.spine_json)
    try:
        info = read_spine(p)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"LỖI: {e}", file=sys.stderr)
        return 1
    mid = args.monster_id or re.sub(r"[^a-z0-9_]+", "_", p.stem.lower()).strip("_")
    only = [s.strip() for s in args.only.split(",")] if args.only else None
    spec = seed_spec(info, args.fps, mid, args.target_height, only, str(p))
    text = json.dumps(spec, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"Đã ghi {args.out}")
    else:
        print(text)
    for w in spec["_source"]["warnings"]:
        print(f"Cảnh báo: {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
