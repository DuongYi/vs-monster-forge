# -*- coding: utf-8 -*-
"""Lõi của Xưởng Quái: không gian làm việc, hàng đợi việc và các thao tác trên một con quái.

Cấu trúc không gian làm việc:
  workspace/monsters/<monster_id>/
      spec.json                 spec quái (postprocess.py)
      ref/ref.png               ảnh chuẩn: đầu vào cho AI + chuẩn đo lệch màu
      source/spine.json         file Spine JSON gốc (nếu tạo từ Spine)
      raw/<anim>/               khung đang dùng cho anim (nhập từ Spine hoặc biến thể AI đã chọn)
      variants/<anim>/seed_<n>/ các biến thể AI
      variants/<anim>/_meta/    ảnh xem trước + QA nhanh của từng biến thể
      previews/                 GIF xem trước của nguồn đang dùng
      out/                      kết quả hậu xử lý (atlas, manifest, QA, ảnh xem trước)
  workspace/export/<monster_id>/  bản đã duyệt
"""
from __future__ import annotations

import json
import queue
import re
import shutil
import sys
import threading
import time
import traceback
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "plugin" / "skills" / "asset-gen"
sys.path.insert(0, str(SKILL / "scripts"))

import comfy_local as cl  # noqa: E402
import numpy as np  # noqa: E402
import postprocess as pp  # noqa: E402
import spine_timing as st  # noqa: E402
from PIL import Image  # noqa: E402

APP_VERSION = "0.1.0"
TEMPLATES_DIR = SKILL / "workflows"
PRESETS_FILE = SKILL / "config" / "anim_presets.json"
THRESHOLDS_FILE = SKILL / "config" / "qa_thresholds.json"
SETTINGS_FILE = Path(__file__).resolve().parent / "settings.json"

ID_RE = re.compile(r"^[a-z0-9_]{2,64}$")
ANIM_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
IMG_EXTS = {".png", ".webp", ".jpg", ".jpeg"}
MAX_UPLOAD = 300 * 1024 * 1024

MOTION_HINTS = {
    "idle": "idle breathing loop, subtle body motion",
    "stand": "standing idle loop, subtle body motion",
    "run": "running in place, looping run cycle",
    "walk": "walking in place, looping walk cycle",
    "move": "moving in place, looping cycle",
    "fly": "hovering in place, looping wing flap",
    "attack": "lunges forward and strikes, then returns to stance",
    "skill": "gathers energy and unleashes a powerful special attack",
    "hurt": "flinches backward from a hit, then recovers",
    "hit": "flinches backward from a hit, then recovers",
    "die": "collapses and falls to the ground, stays down",
    "appear": "emerges dramatically and takes a battle stance",
}
DEFAULT_SUFFIX = ("2D game monster, full body in frame, side view facing right, static camera, "
                  "locked-off shot, plain flat solid {bg} background")
BG_WORDS = {"auto": "pure green", "green": "pure green", "magenta": "pure magenta"}

DEFAULT_SETTINGS = {
    "workspace": str(ROOT / "workspace"),
    "comfy_url": cl.DEFAULT_URL,
    "reviewer": "",
    "prompt_suffix": DEFAULT_SUFFIX,
}


class AppError(Exception):
    """Lỗi hiển thị thẳng cho người dùng."""


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def motion_hint(anim: str) -> str:
    return MOTION_HINTS.get(anim.lower().split("_")[0], f"{anim} animation")


def natural(p: Path):
    return pp.natural_key(p)


def read_json(p: Path, default=None):
    try:
        return json.loads(Path(p).read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def image_files(d: Path) -> list[Path]:
    if not d.is_dir():
        return []
    return sorted([f for f in d.iterdir() if f.is_file() and f.suffix.lower() in IMG_EXTS], key=natural)


def video_files(d: Path) -> list[Path]:
    if not d.is_dir():
        return []
    return sorted(f for f in d.iterdir() if f.is_file() and f.suffix.lower() in pp.VIDEO_EXTS)


def newest_mtime(paths) -> float:
    return max((p.stat().st_mtime for p in paths if p.exists()), default=0.0)


def safe_name(name: str) -> str:
    base = Path(str(name).replace("\\", "/")).name
    base = re.sub(r"[^A-Za-z0-9._\-]+", "_", base).strip("._")
    if not base:
        raise AppError("Tên file không hợp lệ")
    return base[:120]


def make_preview_gif(src: Path, out: Path, fps: float = 12.0, max_side: int = 220, max_frames: int = 64) -> bool:
    """GIF xem nhanh của một nguồn (thư mục ảnh hoặc video). Ảnh có alpha được lót nền ca-rô."""
    try:
        kind, files = pp.source_from_path(src)
    except pp.InputError:
        return False
    frames = []
    for i, f in enumerate(pp.iter_frames(kind, files)):
        if i >= max_frames * 4:
            break
        frames.append(f)
    if not frames:
        return False
    if len(frames) > max_frames:
        idx = np.round(np.linspace(0, len(frames) - 1, max_frames)).astype(int)
        frames = [frames[i] for i in idx]
    h, w = frames[0].shape[:2]
    s = min(1.0, max_side / max(h, w))
    size = (max(1, int(w * s)), max(1, int(h * s)))
    bg = pp.checker(size[1], size[0], 8)
    imgs = []
    for f in frames:
        im = Image.fromarray(pp.to_u8(f)).resize(size, Image.LANCZOS)
        a = np.asarray(im, np.float32)[..., 3:4] / 255.0
        rgb = np.asarray(im, np.float32)[..., :3] / 255.0
        imgs.append(Image.fromarray(pp.to_u8(rgb * a + bg * (1 - a))))
    out.parent.mkdir(parents=True, exist_ok=True)
    imgs[0].save(out, save_all=True, append_images=imgs[1:], duration=int(round(1000 / fps)), loop=0)
    return True


# ───────────────────────────── cài đặt ─────────────────────────────

class Settings:
    def __init__(self, path: Path = SETTINGS_FILE):
        self.path = path
        self.data = dict(DEFAULT_SETTINGS)
        self.data.update(read_json(path, {}) or {})

    def get(self, k):
        return self.data.get(k, DEFAULT_SETTINGS.get(k))

    def update(self, changes: dict) -> dict:
        for k in ("comfy_url", "reviewer", "prompt_suffix", "workspace"):
            if k in changes and isinstance(changes[k], str):
                self.data[k] = changes[k].strip()
        write_json(self.path, self.data)
        return self.data


# ───────────────────────────── hàng đợi việc ─────────────────────────────

class Jobs:
    """Hai làn chạy tuần tự: 'gpu' (sinh AI – mỗi lúc một việc) và 'cpu' (hậu xử lý, xem trước)."""

    def __init__(self):
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._queues = {"gpu": queue.Queue(), "cpu": queue.Queue()}
        self._fns: dict[str, callable] = {}
        self._cancel_hooks: dict[str, callable] = {}
        for lane in self._queues:
            threading.Thread(target=self._worker, args=(lane,), daemon=True).start()

    def submit(self, lane: str, title: str, fn, monster: str | None = None, kind: str = "", anim: str | None = None,
               on_cancel=None) -> dict:
        jid = uuid.uuid4().hex[:12]
        job = {"id": jid, "lane": lane, "title": title, "monster": monster, "kind": kind, "anim": anim,
               "status": "queued", "log": [], "error": None, "result": None,
               "created": now(), "started": None, "finished": None, "t0": None, "elapsed": 0.0,
               "progress": None, "_preview": None, "preview_v": 0}
        with self._lock:
            self._jobs[jid] = job
            self._fns[jid] = fn
            if on_cancel:
                self._cancel_hooks[jid] = on_cancel
        self._queues[lane].put(jid)
        return self.get(jid)

    def _worker(self, lane: str):
        while True:
            jid = self._queues[lane].get()
            with self._lock:
                job = self._jobs.get(jid)
                fn = self._fns.pop(jid, None)
                if not job or job["status"] != "queued":
                    continue
                job["status"] = "running"
                job["started"] = now()
                job["t0"] = time.time()

            def log(*parts):
                msg = " ".join(str(p) for p in parts)
                with self._lock:
                    job["log"].append(msg)
                    del job["log"][:-400]

            def progress(snapshot: dict, preview=None):
                """Tiến độ trực tiếp từ ComfyUI: lưu trạng thái mới nhất và ảnh xem trước mới nhất."""
                with self._lock:
                    old = job["progress"] or {}
                    if (old.get("variant"), old.get("run")) != (snapshot.get("variant"), snapshot.get("run")):
                        job["_preview"] = None  # sang lượt/biến thể mới: bỏ ảnh xem trước của lượt trước
                    job["progress"] = snapshot
                    if preview:
                        job["_preview"] = preview
                        job["preview_v"] += 1

            log.progress = progress

            try:
                result = fn(log)
                with self._lock:
                    job["result"] = result
                    job["status"] = "cancelled" if job.get("cancel_requested") else "done"
            except (AppError, cl.ComfyError, pp.InputError, json.JSONDecodeError) as e:
                with self._lock:
                    job["status"] = "cancelled" if job.get("cancel_requested") else "error"
                    job["error"] = str(e)
            except Exception as e:  # lỗi lập trình: vẫn báo rõ, giữ traceback trong log
                with self._lock:
                    job["status"] = "error"
                    job["error"] = f"Lỗi không mong đợi: {e}"
                    job["log"].extend(traceback.format_exc().splitlines()[-12:])
            finally:
                with self._lock:
                    job["finished"] = now()
                    job["elapsed"] = round(time.time() - job["t0"], 1)
                    self._cancel_hooks.pop(jid, None)

    def get(self, jid: str) -> dict | None:
        with self._lock:
            j = self._jobs.get(jid)
            return self._public(j) if j else None

    def preview(self, jid: str) -> tuple[bytes, str] | None:
        with self._lock:
            j = self._jobs.get(jid)
            return j["_preview"] if j else None

    def _public(self, j: dict) -> dict:
        out = {k: v for k, v in j.items() if k not in ("t0", "_preview")}
        out["has_preview"] = j["_preview"] is not None
        if j["status"] == "running" and j["t0"]:
            out["elapsed"] = round(time.time() - j["t0"], 1)
        out["log"] = list(j["log"][-200:])
        return out

    def list(self, monster: str | None = None, limit: int = 40) -> list[dict]:
        with self._lock:
            jobs = [j for j in self._jobs.values() if monster is None or j["monster"] == monster]
            jobs.sort(key=lambda j: j["created"], reverse=True)
            return [self._public(j) for j in jobs[:limit]]

    def active(self, monster: str, kind: str | None = None, anim: str | None = None) -> bool:
        with self._lock:
            return any(j["monster"] == monster and j["status"] in ("queued", "running")
                       and (kind is None or j["kind"] == kind) and (anim is None or j["anim"] == anim)
                       for j in self._jobs.values())

    def cancel(self, jid: str) -> dict:
        with self._lock:
            j = self._jobs.get(jid)
            if not j:
                raise AppError("Không thấy việc này")
            if j["status"] == "queued":
                j["status"] = "cancelled"
                j["finished"] = now()
                return self._public(j)
            if j["status"] != "running":
                return self._public(j)
            j["cancel_requested"] = True
            hook = self._cancel_hooks.get(jid)
        if hook:
            hook()
        return self.get(jid)


# ───────────────────────────── không gian làm việc ─────────────────────────────

class Workspace:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.monsters_dir = self.root / "monsters"
        self.export_dir = self.root / "export"
        self.monsters_dir.mkdir(parents=True, exist_ok=True)
        self.export_dir.mkdir(parents=True, exist_ok=True)
        self._locks = defaultdict(threading.Lock)

    # — đường dẫn —
    def mdir(self, mid: str) -> Path:
        if not isinstance(mid, str) or not ID_RE.match(mid):
            raise AppError("Mã quái chỉ gồm chữ thường a-z, số và dấu _, dài 2–64 ký tự")
        return self.monsters_dir / mid

    def require(self, mid: str) -> Path:
        d = self.mdir(mid)
        if not (d / "spec.json").exists():
            raise AppError(f"Không có quái '{mid}'")
        return d

    def lock(self, mid: str) -> threading.Lock:
        return self._locks[mid]

    def check_anim(self, mid: str, anim: str) -> dict:
        if not ANIM_RE.match(anim or ""):
            raise AppError("Tên animation không hợp lệ")
        spec = self.spec(mid)
        if anim not in spec["animations"]:
            raise AppError(f"Quái '{mid}' không có animation '{anim}'")
        return spec

    def file_in(self, mid: str, rel: str) -> Path:
        base = self.require(mid).resolve()
        p = (base / rel).resolve()
        if base != p and base not in p.parents:
            raise AppError("Đường dẫn không hợp lệ")
        return p

    def export_file(self, mid: str, rel: str) -> Path:
        base = (self.export_dir / mid).resolve()
        self.mdir(mid)
        p = (base / rel).resolve()
        if base not in p.parents:
            raise AppError("Đường dẫn không hợp lệ")
        return p

    def url(self, mid: str, p: Path) -> str | None:
        if not p.exists():
            return None
        rel = p.resolve().relative_to(self.mdir(mid).resolve()).as_posix()
        return f"/files/{mid}/{rel}?v={p.stat().st_mtime_ns}"

    # — spec —
    def spec(self, mid: str) -> dict:
        s = read_json(self.require(mid) / "spec.json")
        if not isinstance(s, dict):
            raise AppError(f"spec.json của '{mid}' bị hỏng")
        return s

    def save_spec(self, mid: str, spec: dict) -> dict:
        if not isinstance(spec, dict):
            raise AppError("Spec phải là object JSON")
        if spec.get("monster_id") != mid:
            raise AppError("monster_id trong spec phải trùng mã quái")
        for name in (spec.get("animations") or {}):
            if not ANIM_RE.match(name):
                raise AppError(f"Tên animation '{name}' không hợp lệ (chỉ chữ, số, _ và -)")
        try:
            pp.validate_spec(spec)
        except pp.InputError as e:
            raise AppError(str(e))
        write_json(self.mdir(mid) / "spec.json", spec)
        return spec

    # — tạo / liệt kê —
    def presets(self) -> dict:
        data = read_json(PRESETS_FILE, {}) or {}
        return {k: v for k, v in data.items() if not k.startswith("_")}

    def create(self, mid: str, display_name: str = "", preset: str | None = None, spine_json: str | None = None,
               fps: float = 12.0, target_height: int | None = None) -> dict:
        d = self.mdir(mid)
        if (d / "spec.json").exists():
            raise AppError(f"Đã có quái '{mid}'")
        if spine_json:
            try:
                data = json.loads(spine_json)
            except json.JSONDecodeError:
                raise AppError("File Spine không phải JSON hợp lệ. Trong Spine Editor chọn Export ▸ JSON.")
            src = d / "source" / "spine.json"
            src.parent.mkdir(parents=True, exist_ok=True)
            src.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            try:
                info = st.read_spine(src)
            except ValueError as e:
                shutil.rmtree(d, ignore_errors=True)
                raise AppError(f"Không đọc được file Spine: {e}")
            spec = st.seed_spec(info, float(fps), mid, int(target_height or 170), None, "source/spine.json")
            if not spec["animations"]:
                shutil.rmtree(d, ignore_errors=True)
                raise AppError("File Spine không có animation nào")
        else:
            presets = self.presets()
            if preset not in presets:
                raise AppError("Chọn một bộ animation chuẩn hoặc tải file Spine JSON")
            p = json.loads(json.dumps(presets[preset]))
            spec = {
                "monster_id": mid, "target_height_px": int(target_height or p.get("target_height_px", 170)),
                "anchor": p.get("anchor", {"x": "feet", "y": "ground"}), "scale_mode": "global", "padding_px": 4,
                "background": {"mode": "auto"}, "style_reference": None, "animations": p["animations"],
                "_source": {"preset": preset},
            }
        spec["display_name"] = (display_name or "").strip() or None
        d.mkdir(parents=True, exist_ok=True)
        return self.save_spec(mid, spec)

    def delete(self, mid: str) -> None:
        d = self.require(mid)
        shutil.rmtree(d)
        shutil.rmtree(self.export_dir / mid, ignore_errors=True)

    def list(self) -> list[dict]:
        out = []
        for d in sorted(self.monsters_dir.iterdir() if self.monsters_dir.exists() else []):
            spec = read_json(d / "spec.json")
            if not isinstance(spec, dict):
                continue
            anims = list((spec.get("animations") or {}).keys())
            ready = sum(1 for a in anims if image_files(d / "raw" / a) or video_files(d / "raw" / a))
            man = read_json(d / "out" / f"{d.name}.fbf.json") or {}
            appr = read_json(self.export_dir / d.name / "approval.json")
            out.append({"id": d.name, "name": spec.get("display_name") or d.name, "anims": len(anims),
                        "ready": ready, "qa": (man.get("qa") or {}).get("status"),
                        "approved": bool(appr), "updated": newest_mtime([d / "spec.json", d / "out"])})
        return out

    # — ảnh chuẩn & nhập khung —
    def set_ref(self, mid: str, filename: str, data: bytes) -> str:
        d = self.require(mid)
        if len(data) > MAX_UPLOAD:
            raise AppError("Ảnh quá lớn")
        try:
            from io import BytesIO
            im = Image.open(BytesIO(data))
            im.load()
        except Exception:
            raise AppError("File không phải ảnh đọc được (PNG/JPG/WEBP)")
        with self.lock(mid):
            (d / "ref").mkdir(exist_ok=True)
            im.convert("RGBA").save(d / "ref" / "ref.png")
            spec = self.spec(mid)
            spec["style_reference"] = "ref/ref.png"
            self.save_spec(mid, spec)
        return "ref/ref.png"

    def ref_path(self, mid: str) -> Path | None:
        p = self.require(mid) / "ref" / "ref.png"
        return p if p.exists() else None

    def import_frame(self, mid: str, anim: str, filename: str, data: bytes, first: bool) -> int:
        self.check_anim(mid, anim)
        name = safe_name(filename)
        if Path(name).suffix.lower() not in IMG_EXTS:
            raise AppError(f"'{filename}' không phải ảnh PNG/JPG/WEBP")
        if len(data) > MAX_UPLOAD:
            raise AppError("File quá lớn")
        raw = self.mdir(mid) / "raw" / anim
        with self.lock(mid):
            if first:
                shutil.rmtree(raw, ignore_errors=True)
            raw.mkdir(parents=True, exist_ok=True)
            (raw / name).write_bytes(data)
            n = len(image_files(raw))
            write_json(raw / "provenance.json", {"source": "spine_export", "imported": now(), "files": n})
        return n

    def clear_raw(self, mid: str, anim: str) -> None:
        self.check_anim(mid, anim)
        with self.lock(mid):
            shutil.rmtree(self.mdir(mid) / "raw" / anim, ignore_errors=True)

    # — biến thể AI —
    def variant_dirs(self, mid: str, anim: str) -> list[Path]:
        base = self.mdir(mid) / "variants" / anim
        if not base.is_dir():
            return []
        return sorted([d for d in base.iterdir() if d.is_dir() and d.name.startswith("seed_")],
                      key=lambda d: int(d.name[5:]) if d.name[5:].isdigit() else 0)

    def finish_variant(self, mid: str, anim: str, vdir: Path, log=lambda *a: None) -> dict:
        """Ảnh xem trước + QA nhanh cho một biến thể vừa sinh."""
        spec = self.spec(mid)
        a = spec["animations"][anim]
        meta_dir = vdir.parent / "_meta"
        gif = meta_dir / f"{vdir.name}.gif"
        make_preview_gif(vdir, gif, fps=float(a.get("fps", 12)))
        sub = json.loads(json.dumps(spec))
        sub["animations"] = {anim: a}
        if sub.get("scale_reference") and sub["scale_reference"] != anim:
            sub.pop("scale_reference")
        qa_dir = meta_dir / f"{vdir.name}_qa"
        shutil.rmtree(qa_dir, ignore_errors=True)
        try:
            r = pp.build(sub, self.mdir(mid) / "raw", qa_dir, pp.load_thresholds(str(THRESHOLDS_FILE)),
                         self.mdir(mid), sources={anim: vdir})
            status = r["status"]
            issues = [{"check": c["check"], "status": c["status"], "msg": c["msg"]}
                      for c in r["checks"] if c["status"] != "pass"]
        except pp.InputError as e:
            status, issues = "error", [{"check": "input", "status": "fail", "msg": str(e)}]
        prov = read_json(vdir / "provenance.json", {}) or {}
        meta = {"seed": prov.get("seed"), "frames": len(image_files(vdir)) or len(video_files(vdir)),
                "qa": status, "issues": issues, "duration_s": prov.get("duration_s"),
                "template": prov.get("template"), "prompt": (prov.get("params") or {}).get("PROMPT"),
                "created": now()}
        write_json(meta_dir / f"{vdir.name}.json", meta)
        log(f"  QA nhanh {vdir.name}: {status}")
        return meta

    def generate(self, mid: str, anim: str, template: str, prompt: str, seeds: list[int], sets: dict,
                 comfy: cl.Comfy, log, cancel_flag: threading.Event | None = None, poll: float = 2.0) -> dict:
        self.check_anim(mid, anim)
        tpl = (TEMPLATES_DIR / safe_name(template))
        if not tpl.exists() or not tpl.name.endswith(".template.json"):
            raise AppError(f"Không có mẫu workflow '{template}'")
        ref = self.ref_path(mid)
        if not ref:
            raise AppError("Chưa có ảnh chuẩn. Tải ảnh chuẩn của quái lên trước khi sinh bằng AI.")
        _, meta, _ = cl.load_template(tpl)
        images = [ref, ref] if "INPUT_IMAGE_2" in (meta.get("defaults") or {}) else [ref]
        all_sets = {"PROMPT": prompt, **{k: str(v) for k, v in (sets or {}).items() if str(v).strip() != ""}}
        done = []
        for s in seeds:
            if cancel_flag is not None and cancel_flag.is_set():
                log("Đã dừng theo yêu cầu.")
                break
            vdir = self.mdir(mid) / "variants" / anim / f"seed_{s}"
            log(f"Sinh '{anim}' – seed {s}")
            report = getattr(log, "progress", None)
            idx, total = len(done) + 1, len(seeds)

            def on_progress(snap, prev, _idx=idx, _total=total):
                if report:
                    report(dict(snap, variant=_idx, variants=_total), prev)

            cl.run_template(comfy, tpl, all_sets, vdir, seed_list=[s], input_images=images, overwrite=True,
                            tag=f"{mid}_{anim}", poll=poll, log=lambda m: log(m.strip()),
                            progress=on_progress if report else None)
            done.append(self.finish_variant(mid, anim, vdir, log))
        return {"variants": done}

    def choose_variant(self, mid: str, anim: str, seed: int) -> None:
        self.check_anim(mid, anim)
        src = self.mdir(mid) / "variants" / anim / f"seed_{int(seed)}"
        if not src.is_dir():
            raise AppError(f"Không có biến thể seed {seed}")
        raw = self.mdir(mid) / "raw" / anim
        with self.lock(mid):
            shutil.rmtree(raw, ignore_errors=True)
            raw.mkdir(parents=True)
            for f in src.iterdir():
                if f.is_file():
                    shutil.copy2(f, raw / f.name)

    def delete_variant(self, mid: str, anim: str, seed: int) -> None:
        self.check_anim(mid, anim)
        base = self.mdir(mid) / "variants" / anim
        name = f"seed_{int(seed)}"
        with self.lock(mid):
            shutil.rmtree(base / name, ignore_errors=True)
            meta = base / "_meta"
            if meta.exists():
                for p in meta.iterdir():
                    if p.name == f"{name}.json" or p.name == f"{name}.gif" or p.name == f"{name}_qa":
                        if p.is_dir():
                            shutil.rmtree(p, ignore_errors=True)
                        else:
                            p.unlink(missing_ok=True)

    # — hậu xử lý & duyệt —
    def process(self, mid: str, log) -> dict:
        d = self.require(mid)
        spec = self.spec(mid)
        missing = [a for a in spec["animations"]
                   if not (image_files(d / "raw" / a) or video_files(d / "raw" / a))]
        if missing:
            raise AppError("Chưa có khung hình cho: " + ", ".join(missing))
        out = d / "out"
        with self.lock(mid):
            shutil.rmtree(out, ignore_errors=True)
            log("Đang hậu xử lý…")
            r = pp.build(spec, d / "raw", out, pp.load_thresholds(str(THRESHOLDS_FILE)), d)
        log(f"Xong: QA {r['status'].upper()}")
        return {"status": r["status"]}

    def stale_reasons(self, mid: str) -> list[str]:
        d = self.mdir(mid)
        man_p = d / "out" / f"{mid}.fbf.json"
        man = read_json(man_p)
        if not man:
            return []
        reasons = []
        if man["postprocess"]["spec_sha256"] != pp.sha256_json(self.spec(mid)):
            reasons.append("spec đã đổi")
        raw_files = [f for f in (d / "raw").rglob("*") if f.is_file()] if (d / "raw").exists() else []
        if newest_mtime(raw_files + [d / "ref" / "ref.png"]) > man_p.stat().st_mtime:
            reasons.append("khung hình hoặc ảnh chuẩn đã đổi")
        return reasons

    def approve(self, mid: str, reviewer: str, note: str = "", force: bool = False) -> dict:
        d = self.require(mid)
        man_p = d / "out" / f"{mid}.fbf.json"
        man = read_json(man_p)
        if not man:
            raise AppError("Chưa hậu xử lý. Chạy hậu xử lý trước khi duyệt.")
        stale = self.stale_reasons(mid)
        if stale:
            raise AppError("Kết quả hậu xử lý đã cũ (" + ", ".join(stale) + "). Chạy lại hậu xử lý.")
        status = man["qa"]["status"]
        if status == "fail" and not force:
            raise AppError("QA còn lỗi. Sửa rồi chạy lại, hoặc ghi lý do và chọn duyệt dù còn lỗi.")
        if status == "fail" and not (note or "").strip():
            raise AppError("Duyệt khi QA còn lỗi cần ghi lý do.")
        dest = self.export_dir / mid
        with self.lock(mid):
            shutil.rmtree(dest, ignore_errors=True)
            dest.mkdir(parents=True)
            for name in (f"{mid}.png", f"{mid}.fbf.json", "qa_report.md", "qa_report.json"):
                shutil.copy2(d / "out" / name, dest / name)
            shutil.copy2(d / "out" / "preview" / "contact_sheet.png", dest / "contact_sheet.png")
            appr = {"monster_id": mid, "approved_by": (reviewer or "").strip() or "(chưa đặt tên)",
                    "approved_at": now(), "qa_status": status, "forced": bool(status == "fail"),
                    "note": (note or "").strip(), "atlas_sha256": man["atlas"]["sha256"],
                    "spec_sha256": man["postprocess"]["spec_sha256"],
                    "next_step": "Nộp draft qua slice_sprites → validate_payload → submit_draft (chưa nối tự động)."}
            write_json(dest / "approval.json", appr)
        return appr

    def unapprove(self, mid: str) -> None:
        self.require(mid)
        shutil.rmtree(self.export_dir / mid, ignore_errors=True)

    # — trạng thái đầy đủ cho giao diện —
    def state(self, mid: str, jobs: Jobs | None = None) -> dict:
        d = self.require(mid)
        spec = self.spec(mid)
        anims = []
        for name, a in spec["animations"].items():
            raw = d / "raw" / name
            imgs, vids = image_files(raw), video_files(raw)
            prov = read_json(raw / "provenance.json", {}) or {}
            src = None
            if imgs or vids:
                prev = d / "previews" / f"{name}.gif"
                if newest_mtime(imgs + vids) > (prev.stat().st_mtime if prev.exists() else 0):
                    make_preview_gif(raw, prev, fps=float(a.get("fps", 12)))
                origin = ("spine" if prov.get("source") == "spine_export" else
                          "ai" if prov.get("tool") == "comfy_local.py" else "khác")
                src = {"kind": "frames" if imgs else "video", "count": len(imgs) or len(vids), "origin": origin,
                       "seed": prov.get("seed"), "preview": self.url(mid, prev)}
            variants = []
            for vd in self.variant_dirs(mid, name):
                m = read_json(vd.parent / "_meta" / f"{vd.name}.json", {}) or {}
                seed = int(vd.name[5:]) if vd.name[5:].isdigit() else None
                variants.append({"seed": seed, "frames": m.get("frames"), "qa": m.get("qa"),
                                 "issues": m.get("issues", []), "duration_s": m.get("duration_s"),
                                 "prompt": m.get("prompt"), "template": m.get("template"),
                                 "preview": self.url(mid, vd.parent / "_meta" / f"{vd.name}.gif"),
                                 "contact_sheet": self.url(mid, vd.parent / "_meta" / f"{vd.name}_qa" / "preview" / "contact_sheet.png"),
                                 "in_use": src is not None and src["origin"] == "ai" and src["seed"] == seed})
            events = [{"name": e["name"], "frame": e["frame"]} for e in pp.parse_events(a)]
            anims.append({"name": name, "frames": a["frames"], "fps": a["fps"], "loop": bool(a.get("loop")),
                          "events": events, "target_duration_ms": a.get("target_duration_ms"),
                          "source": src, "variants": variants, "motion_hint": motion_hint(name),
                          "busy": bool(jobs and jobs.active(mid, "generate", name))})
        result = None
        man_p = d / "out" / f"{mid}.fbf.json"
        man = read_json(man_p)
        if man:
            rep = read_json(d / "out" / "qa_report.json", {}) or {}
            result = {"status": man["qa"]["status"], "per_animation": man["qa"]["per_animation"],
                      "atlas": {"w": man["atlas"]["w"], "h": man["atlas"]["h"], "url": self.url(mid, d / "out" / f"{mid}.png")},
                      "cell": man["cell"],
                      "contact_sheet": self.url(mid, d / "out" / "preview" / "contact_sheet.png"),
                      "gifs": {an["name"]: self.url(mid, d / "out" / "preview" / f"{an['name']}.gif") for an in man["animations"]},
                      "checks": rep.get("checks", []),
                      "manifest": self.url(mid, man_p), "processed_at": datetime.fromtimestamp(man_p.stat().st_mtime).astimezone().isoformat(timespec="seconds"),
                      "stale": self.stale_reasons(mid)}
        appr = read_json(self.export_dir / mid / "approval.json")
        if appr:
            appr = dict(appr, folder=str((self.export_dir / mid).resolve()))
        ref = d / "ref" / "ref.png"
        return {"id": mid, "name": spec.get("display_name") or mid, "spec": spec,
                "ref": self.url(mid, ref), "anims": anims, "result": result, "approval": appr,
                "processing": bool(jobs and jobs.active(mid, "process"))}


# ───────────────────────────── ứng dụng ─────────────────────────────

class App:
    def __init__(self, settings: Settings | None = None, workspace: Path | None = None):
        self.settings = settings or Settings()
        self.ws = Workspace(Path(workspace or self.settings.get("workspace")))
        self.jobs = Jobs()
        self.poll = 2.0  # giây giữa hai lần hỏi ComfyUI
        self._doctor = {"at": 0.0, "data": None}
        self._doctor_lock = threading.Lock()

    def comfy(self, timeout: float = 60) -> cl.Comfy:
        return cl.Comfy(self.settings.get("comfy_url"), timeout=timeout)

    def doctor(self, refresh: bool = False) -> dict:
        with self._doctor_lock:
            if not refresh and self._doctor["data"] and time.time() - self._doctor["at"] < 60:
                return self._doctor["data"]
            try:
                rep = cl.doctor(self.comfy(timeout=10), TEMPLATES_DIR, cl.load_registry(None))
                data = {"connected": True, **rep}
            except cl.ComfyError as e:
                data = {"connected": False, "error": str(e), "templates": []}
            self._doctor = {"at": time.time(), "data": data}
            return data

    def templates(self) -> list[dict]:
        ready = {t["file"]: t for t in (self.doctor().get("templates") or [])}
        out = []
        for p in sorted(TEMPLATES_DIR.glob("*.template.json")):
            try:
                _, meta, _ = cl.load_template(p)
            except (cl.ComfyError, json.JSONDecodeError):
                continue
            defaults = meta.get("defaults") or {}
            out.append({"file": p.name, "name": meta.get("name"), "description": meta.get("description", ""),
                        "status": meta.get("status"), "rules": meta.get("rules") or {},
                        "min_vram_gb": meta.get("min_vram_gb"),
                        "defaults": {k: v for k, v in defaults.items() if not k.startswith("INPUT_IMAGE") and k != "PREFIX"},
                        "two_images": "INPUT_IMAGE_2" in defaults,
                        "ready": ready.get(p.name, {}).get("ready"),
                        "problems": ready.get(p.name, {}).get("problems", [])})
        return out

    def full_prompt(self, mid: str, motion: str) -> str:
        spec = self.ws.spec(mid)
        bg = spec.get("background") or {}
        word = "pure magenta" if bg.get("color") == [255, 0, 255] else BG_WORDS.get(bg.get("mode", "auto"), "pure green")
        suffix = (self.settings.get("prompt_suffix") or DEFAULT_SUFFIX).replace("{bg}", word)
        motion = (motion or "").strip().rstrip(",")
        return f"{motion}, {suffix}" if motion else suffix

    def start_generate(self, mid: str, anim: str, template: str, motion: str, count: int,
                       seed: int | None, sets: dict) -> dict:
        self.ws.check_anim(mid, anim)
        if not self.ws.ref_path(mid):
            raise AppError("Chưa có ảnh chuẩn. Tải ảnh chuẩn của quái lên trước khi sinh bằng AI.")
        count = max(1, min(int(count or 1), 4))
        if seed is None:
            used = [int(d.name[5:]) for d in self.ws.variant_dirs(mid, anim) if d.name[5:].isdigit()]
            seed = (max(used) + 1) if used else 1001
        seeds = [int(seed) + i for i in range(count)]
        prompt = self.full_prompt(mid, motion)
        cancel = threading.Event()
        comfy = self.comfy()

        def run(log):
            log(f"Prompt: {prompt}")
            return self.ws.generate(mid, anim, template, prompt, seeds, sets, comfy, log, cancel, poll=self.poll)

        def on_cancel():
            cancel.set()
            comfy.interrupt()

        return self.jobs.submit("gpu", f"Sinh {count} biến thể '{anim}' – {mid}", run, monster=mid,
                                kind="generate", anim=anim, on_cancel=on_cancel)

    def start_process(self, mid: str) -> dict:
        self.ws.require(mid)
        if self.jobs.active(mid, "process"):
            raise AppError("Đang hậu xử lý quái này")
        return self.jobs.submit("cpu", f"Hậu xử lý – {mid}", lambda log: self.ws.process(mid, log),
                                monster=mid, kind="process")
