#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
comfy_local.py — Điều khiển ComfyUI chạy ngay trên máy (mặc định http://127.0.0.1:8188).
Không cần Comfy Cloud, không cần MCP, chỉ dùng thư viện chuẩn Python 3.10+.

Lệnh:
  doctor      Kiểm máy: GPU, VRAM, RAM, phiên bản ComfyUI, mô hình đã có, mẫu nào chạy được, gợi ý cấu hình.
  templatize  Biến workflow xuất từ ComfyUI (Workflow ▸ Export (API)) thành mẫu dùng lại được:
              tự thay seed, prompt, ảnh đầu vào, số khung, kích thước… bằng chỗ giữ chỗ __SEED__, __PROMPT__…
  validate    Đối chiếu mẫu với ComfyUI đang chạy: đủ node chưa, có file mô hình chưa, giấy phép mô hình có được phép không.
  run         Điền tham số, tải ảnh đầu vào lên, chạy (1 hoặc nhiều seed), tải kết quả về thư mục để postprocess.py xử lý,
              ghi provenance.json (mẫu, sha256, mô hình, seed, tham số, GPU, thời gian chạy).

Ví dụ:
  python comfy_local.py doctor
  python comfy_local.py templatize --workflow wan22_5b_api.json --name wan22_5b_i2v --out ../workflows/wan22_5b_i2v.template.json
  python comfy_local.py validate --template ../workflows/wan22_5b_i2v.template.json
  python comfy_local.py run --template ../workflows/wan22_5b_i2v.template.json --input-image ref/mob_x_idle.png \
         --set PROMPT="..." --seeds 3 --dest raw/mob_x/idle

Mã thoát: 0 = xong · 1 = lỗi đầu vào / ComfyUI / workflow · 2 = validate có lỗi · 130 = người dùng dừng (Ctrl+C).
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, parse, request

VERSION = "0.1.0"
TEMPLATE_SCHEMA = "asset-gen.comfy-template/1"
DEFAULT_URL = "http://127.0.0.1:8188"
HERE = Path(__file__).resolve().parent
DEFAULT_REGISTRY = HERE.parent / "workflows" / "registry.json"
DEFAULT_TEMPLATES = HERE.parent / "workflows"

PH_RE = re.compile(r"__([A-Z][A-Z0-9_]*?)__")
MODEL_EXTS = (".safetensors", ".sft", ".ckpt", ".pt", ".pth", ".gguf", ".bin", ".onnx")
SAVE_NODES = {"SaveImage", "SaveVideo", "SaveAnimatedWEBP", "SaveAnimatedPNG", "SaveWEBM", "VHS_VideoCombine"}
SEED_KEYS = ("seed", "noise_seed")
FRAME_KEYS = ("length", "num_frames", "frame_count", "video_frames")
TEXT_KEYS = ("text", "prompt", "text_g")
# Ô chọn file mô hình trong object_info → thư mục models/ tương ứng (để doctor liệt kê mô hình đã có)
LOADER_FIELDS = {
    ("UNETLoader", "unet_name"): "diffusion_models",
    ("UnetLoaderGGUF", "unet_name"): "diffusion_models (gguf)",
    ("CLIPLoader", "clip_name"): "text_encoders",
    ("DualCLIPLoader", "clip_name1"): "text_encoders",
    ("VAELoader", "vae_name"): "vae",
    ("CheckpointLoaderSimple", "ckpt_name"): "checkpoints",
    ("LoraLoaderModelOnly", "lora_name"): "loras",
    ("LoraLoader", "lora_name"): "loras",
    ("CLIPVisionLoader", "clip_name"): "clip_vision",
    ("ControlNetLoader", "control_net_name"): "controlnet",
}
# Gợi ý theo VRAM – ƯỚC TÍNH, phải xác nhận bằng chạy thử trên chính máy đó.
VRAM_GUIDE = [
    (0, 8, "Chưa đủ cho video AI. Dùng đường xuất Spine → postprocess (không cần GPU)."),
    (8, 12, "Wan 2.2 5B (offload sẵn của ComfyUI), khung ≤ 640px, ≤ 49 khung. Chạy chậm."),
    (12, 16, "Wan 2.2 5B thoải mái; 14B chỉ với bản GGUF lượng tử hoá (custom node ComfyUI-GGUF)."),
    (16, 24, "14B bản GGUF hoặc fp8, độ phân giải vừa; 5B ở 720p."),
    (24, 10 ** 6, "14B fp8/fp16, dùng được first-last-frame để khép vòng lặp."),
]


class ComfyError(Exception):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def format_comfy_error(payload) -> str:
    """Rút thông báo dễ đọc từ lỗi của /prompt (error + node_errors)."""
    if not isinstance(payload, dict):
        return str(payload)
    parts = []
    err = payload.get("error")
    if isinstance(err, dict):
        parts.append(err.get("message") or err.get("type") or "")
        if err.get("details"):
            parts.append(str(err["details"]))
    elif err:
        parts.append(str(err))
    for nid, ne in (payload.get("node_errors") or {}).items():
        for e in ne.get("errors", []):
            parts.append(f"node {nid} ({ne.get('class_type', '?')}): {e.get('message', '')} {e.get('details', '')}".strip())
    return " | ".join(p for p in parts if p) or json.dumps(payload, ensure_ascii=False)[:500]


# ───────────────────────────── HTTP ─────────────────────────────

class Comfy:
    def __init__(self, url: str = DEFAULT_URL, timeout: float = 60):
        self.url = url.rstrip("/")
        self.timeout = timeout

    def _req(self, method: str, path: str, data: bytes | None = None, headers: dict | None = None, raw: bool = False):
        req = request.Request(self.url + path, data=data, method=method, headers=headers or {})
        try:
            with request.urlopen(req, timeout=self.timeout) as r:
                body = r.read()
        except error.HTTPError as e:
            body = e.read()
            try:
                payload = json.loads(body)
            except Exception:
                payload = body.decode("utf-8", "replace")[:500]
            raise ComfyError(f"ComfyUI trả lỗi HTTP {e.code} ở {path}: {format_comfy_error(payload)}") from None
        except (error.URLError, ConnectionError, TimeoutError) as e:
            reason = getattr(e, "reason", e)
            raise ComfyError(f"Không kết nối được ComfyUI tại {self.url} ({reason}). ComfyUI đã mở chưa, đúng cổng chưa?") from None
        if raw:
            return body
        return json.loads(body) if body else None

    def get(self, path: str):
        return self._req("GET", path)

    def post_json(self, path: str, obj):
        return self._req("POST", path, json.dumps(obj).encode("utf-8"), {"Content-Type": "application/json"})

    def upload_image(self, path: Path) -> str:
        data = path.read_bytes()
        name = f"assetgen_{sha256_bytes(data)[:10]}_{path.name}"
        boundary = uuid.uuid4().hex
        parts = [
            (f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{name}"\r\n'
             f"Content-Type: application/octet-stream\r\n\r\n").encode("utf-8") + data + b"\r\n",
            f'--{boundary}\r\nContent-Disposition: form-data; name="overwrite"\r\n\r\ntrue\r\n'.encode(),
            f'--{boundary}\r\nContent-Disposition: form-data; name="type"\r\n\r\ninput\r\n'.encode(),
            f"--{boundary}--\r\n".encode(),
        ]
        res = self._req("POST", "/upload/image", b"".join(parts),
                        {"Content-Type": f"multipart/form-data; boundary={boundary}"})
        sub = (res or {}).get("subfolder") or ""
        return f"{sub}/{res['name']}" if sub else res["name"]

    def view(self, item: dict) -> bytes:
        q = parse.urlencode({"filename": item["filename"], "subfolder": item.get("subfolder", ""),
                             "type": item.get("type", "output")})
        return self._req("GET", f"/view?{q}", raw=True)

    def interrupt(self, prompt_id: str | None = None) -> None:
        try:
            self.post_json("/interrupt", {"prompt_id": prompt_id} if prompt_id else {})
            if prompt_id:
                self.post_json("/queue", {"delete": [prompt_id]})
        except ComfyError:
            pass


# ───────────────────────────── object_info ─────────────────────────────

def combo_options(spec) -> list | None:
    """Danh sách lựa chọn của một input dạng combo (hỗ trợ cả định dạng cũ lẫn 'COMBO' mới)."""
    if not isinstance(spec, list) or not spec:
        return None
    t = spec[0]
    if isinstance(t, list):
        return t
    if t == "COMBO" and len(spec) > 1 and isinstance(spec[1], dict):
        opts = spec[1].get("options")
        return opts if isinstance(opts, list) else None
    return None


def model_inventory(object_info: dict) -> dict[str, list[str]]:
    inv: dict[str, set] = {}
    for (node, field), folder in LOADER_FIELDS.items():
        spec = ((object_info.get(node) or {}).get("input") or {}).get("required", {}).get(field)
        opts = combo_options(spec)
        if opts:
            inv.setdefault(folder, set()).update(o for o in opts if isinstance(o, str))
    return {k: sorted(v) for k, v in inv.items()}


def is_model_file(v) -> bool:
    return isinstance(v, str) and v.lower().endswith(MODEL_EXTS)


def model_files_in(wf: dict) -> list[str]:
    out = []
    for node in wf.values():
        for v in (node.get("inputs") or {}).values():
            if is_model_file(v) and v not in out:
                out.append(v)
    return out


# ───────────────────────────── registry (giấy phép) ─────────────────────────────

def load_registry(path: Path | None) -> dict:
    p = Path(path) if path else DEFAULT_REGISTRY
    if not p.exists():
        return {"models": [], "loras": []}
    return json.loads(p.read_text("utf-8"))


def license_of(filename: str, registry: dict) -> tuple[str, str | None]:
    """(license_status, model_id). 'unlisted' nếu file không khớp mẫu nào trong registry."""
    base = Path(filename.replace("\\", "/")).name.lower()
    for m in list(registry.get("models", [])) + list(registry.get("loras", [])):
        for pat in m.get("files", []):
            if fnmatch.fnmatch(base, pat.lower()):
                return m.get("license_status", "check"), m.get("id")
    return "unlisted", None


# ───────────────────────────── template ─────────────────────────────

def _sorted_ids(wf: dict) -> list[str]:
    return sorted(wf, key=lambda k: (int(k) if str(k).isdigit() else 10 ** 9, str(k)))


def _trace_text(wf: dict, link, role: str, depth: int = 0):
    """Lần ngược từ input positive/negative của sampler tới node chứa ô text."""
    if depth > 8 or not isinstance(link, list) or not link:
        return None
    nid = str(link[0])
    node = wf.get(nid)
    if not node:
        return None
    inp = node.get("inputs", {})
    for key in TEXT_KEYS:
        if isinstance(inp.get(key), str):
            return nid, key
    for key in (role, "conditioning", "conditioning_1", "conditioning_to"):
        if isinstance(inp.get(key), list):
            return _trace_text(wf, inp[key], role, depth + 1)
    return None


def templatize(wf: dict) -> tuple[dict, dict, list[str]]:
    """Trả (workflow có chỗ giữ chỗ, giá trị mặc định = giá trị gốc, nhật ký thay đổi)."""
    if isinstance(wf, dict) and "nodes" in wf and "links" in wf:
        raise ComfyError("Đây là workflow dạng giao diện (UI). Trong ComfyUI chọn Workflow ▸ Export (API) rồi dùng file đó.")
    if not isinstance(wf, dict) or not wf or not all(isinstance(v, dict) and "class_type" in v for v in wf.values()):
        raise ComfyError("File không phải workflow ComfyUI dạng API (cần object {id: {class_type, inputs}}).")
    t = json.loads(json.dumps(wf))
    defaults: dict = {}
    log: list[str] = []

    def setph(nid, key, ph):
        old = t[nid]["inputs"].get(key)
        if isinstance(old, list) or old == f"__{ph}__":
            return
        if ph in defaults and defaults[ph] != old:
            log.append(f"  ! {ph}: node {nid}.{key} có giá trị gốc {old!r} khác {defaults[ph]!r} – dùng chung chỗ giữ chỗ")
        defaults.setdefault(ph, old)
        t[nid]["inputs"][key] = f"__{ph}__"
        log.append(f"  node {nid} ({t[nid]['class_type']}).{key} = {old!r}  →  __{ph}__")

    ids = _sorted_ids(t)
    img_n = 0
    for nid in ids:
        ct = t[nid]["class_type"]
        inp = t[nid].get("inputs", {})
        for k in SEED_KEYS:
            if isinstance(inp.get(k), int) and not isinstance(inp.get(k), bool):
                setph(nid, k, "SEED")
        if isinstance(inp.get("steps"), int) and any(k in inp for k in SEED_KEYS):
            setph(nid, "steps", "STEPS")
        for k in FRAME_KEYS:
            if isinstance(inp.get(k), int):
                setph(nid, k, "NUM_FRAMES")
        if ("Latent" in ct or "ToVideo" in ct) and isinstance(inp.get("width"), int):
            setph(nid, "width", "WIDTH")
            if isinstance(inp.get("height"), int):
                setph(nid, "height", "HEIGHT")
        if ct == "LoadImage" and isinstance(inp.get("image"), str):
            img_n += 1
            setph(nid, "image", "INPUT_IMAGE" if img_n == 1 else f"INPUT_IMAGE_{img_n}")
        if (ct in SAVE_NODES or "filename_prefix" in inp) and isinstance(inp.get("filename_prefix"), str):
            setph(nid, "filename_prefix", "PREFIX")
    for nid in ids:
        inp = t[nid].get("inputs", {})
        if not any(k in inp for k in SEED_KEYS):
            continue
        for role, ph in (("positive", "PROMPT"), ("negative", "NEGATIVE")):
            hit = _trace_text(t, inp.get(role), role)
            if hit:
                setph(hit[0], hit[1], ph)
    if "SEED" not in defaults:
        log.append("  ! Không tìm thấy ô seed – kiểm lại workflow (sampler tuỳ biến?)")
    if "PROMPT" not in defaults:
        log.append("  ! Không lần ra ô prompt từ sampler – sửa tay: đặt text = \"__PROMPT__\"")
    return t, defaults, log


def load_template(path: Path) -> tuple[dict, dict, bytes]:
    raw = Path(path).read_bytes()
    obj = json.loads(raw)
    if obj.get("_asset_gen", {}).get("schema") != TEMPLATE_SCHEMA or "workflow" not in obj:
        raise ComfyError(f"{path} không phải mẫu asset-gen ({TEMPLATE_SCHEMA}). Tạo bằng lệnh templatize.")
    return obj["workflow"], obj["_asset_gen"], raw


def fill(obj, params: dict):
    if isinstance(obj, dict):
        return {k: fill(v, params) for k, v in obj.items()}
    if isinstance(obj, list):
        return [fill(v, params) for v in obj]
    if isinstance(obj, str):
        m = PH_RE.fullmatch(obj)
        if m and m.group(1) in params:
            return params[m.group(1)]
        return PH_RE.sub(lambda mm: str(params[mm.group(1)]) if mm.group(1) in params else mm.group(0), obj)
    return obj


def leftover_placeholders(obj) -> set[str]:
    found: set[str] = set()
    if isinstance(obj, dict):
        for v in obj.values():
            found |= leftover_placeholders(v)
    elif isinstance(obj, list):
        for v in obj:
            found |= leftover_placeholders(v)
    elif isinstance(obj, str):
        found |= set(PH_RE.findall(obj))
    return found


def coerce(value: str, like):
    if isinstance(like, bool):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(like, int):
        return int(value)
    if isinstance(like, float):
        return float(value)
    if like is None:
        for f in (int, float):
            try:
                return f(value)
            except ValueError:
                pass
    return value


def check_rules(params: dict, rules: dict) -> list[str]:
    errs = []
    for key, rule in (rules or {}).items():
        v = params.get(key)
        if not isinstance(v, int):
            continue
        if rule == "4n+1" and (v - 1) % 4 != 0:
            lo = v - ((v - 1) % 4)
            errs.append(f"{key}={v} phải có dạng 4n+1 (vd. {lo} hoặc {lo + 4})")
        m = re.fullmatch(r"multiple_of_(\d+)", str(rule))
        if m and v % int(m.group(1)) != 0:
            k = int(m.group(1))
            errs.append(f"{key}={v} phải chia hết cho {k} (vd. {v - v % k} hoặc {v - v % k + k})")
    return errs


# ───────────────────────────── validate ─────────────────────────────

def validate(wf: dict, object_info: dict, registry: dict, allow_unlisted: bool = False) -> list[dict]:
    issues: list[dict] = []

    def add(level, msg, node=None):
        issues.append({"level": level, "node": node, "msg": msg})

    for nid in _sorted_ids(wf):
        node = wf[nid]
        ct = node.get("class_type")
        info = object_info.get(ct)
        if info is None:
            add("error", f"Thiếu node '{ct}' – cài custom node chứa node này hoặc cập nhật ComfyUI", nid)
            continue
        spec = info.get("input") or {}
        req = spec.get("required") or {}
        opt = spec.get("optional") or {}
        inputs = node.get("inputs") or {}
        for k in req:
            if k not in inputs:
                add("error", f"{ct} thiếu input bắt buộc '{k}' (workflow cũ hơn phiên bản ComfyUI?)", nid)
        for k, v in inputs.items():
            s = req.get(k, opt.get(k))
            if s is None:
                add("warn", f"{ct} có input lạ '{k}' – ComfyUI có thể bỏ qua", nid)
                continue
            if isinstance(v, list) or (isinstance(v, str) and PH_RE.fullmatch(v)):
                continue
            opts = combo_options(s)
            if opts is not None and v not in opts:
                if is_model_file(v):
                    add("error", f"Chưa có file mô hình '{v}' cho {ct}.{k} – tải về đúng thư mục trong ComfyUI/models/", nid)
                else:
                    add("error", f"Giá trị {v!r} không hợp lệ cho {ct}.{k}", nid)
    for f in model_files_in(wf):
        st, mid = license_of(f, registry)
        if st == "ok":
            continue
        if st == "unlisted":
            add("warn" if allow_unlisted else "error",
                f"Mô hình '{f}' chưa có trong workflows/registry.json – thêm vào kèm giấy phép đã kiểm trước khi dùng")
        else:
            add("error", f"Mô hình '{f}' ({mid}) có license_status='{st}' – chưa được phép dùng cho asset thương mại")
    return issues


# ───────────────────────────── run ─────────────────────────────

def history_status(entry: dict) -> str:
    st = entry.get("status") or {}
    s = st.get("status_str")
    if s in ("success", "error"):
        return s
    if st.get("completed") is True:
        return "success"
    if not st and entry.get("outputs"):
        return "success"
    return "running"


def history_error(entry: dict) -> str:
    msgs = (entry.get("status") or {}).get("messages") or []
    if any(isinstance(m, list) and m and m[0] == "execution_interrupted" for m in msgs):
        return "đã dừng theo yêu cầu"
    for m in msgs:
        if isinstance(m, list) and len(m) == 2 and m[0] == "execution_error":
            d = m[1] or {}
            return f"node {d.get('node_id')} ({d.get('node_type')}): {d.get('exception_message', '').strip()}"
    return "ComfyUI báo lỗi khi chạy (xem cửa sổ console của ComfyUI)"


def wait_for(c: Comfy, prompt_id: str, timeout: float, poll: float, log=print) -> dict:
    t0 = time.time()
    last_note = 0.0
    while True:
        h = c.get(f"/history/{prompt_id}") or {}
        if prompt_id in h:
            entry = h[prompt_id]
            st = history_status(entry)
            if st == "error":
                raise ComfyError(f"Chạy lỗi: {history_error(entry)}")
            if st == "success":
                return entry
        el = time.time() - t0
        if el > timeout:
            c.interrupt(prompt_id)
            raise ComfyError(f"Quá {timeout:.0f}s chưa xong – đã dừng job. Tăng --timeout hoặc giảm số khung/kích thước.")
        if el - last_note >= 30:
            last_note = el
            if el >= 30:
                log(f"    … đang chạy {el:.0f}s")
        time.sleep(poll)


def output_items(entry: dict):
    for nid, out in (entry.get("outputs") or {}).items():
        for key, val in (out or {}).items():
            if isinstance(val, list):
                for it in val:
                    if isinstance(it, dict) and "filename" in it and it.get("type", "output") == "output":
                        yield nid, key, it


def run_template(c: Comfy, template_path: Path, sets: dict[str, str], dest: Path, seeds: int = 1,
                 seed_list: list[int] | None = None, input_images: list[Path] | None = None,
                 timeout: float = 3600, poll: float = 2.0, overwrite: bool = False, tag: str | None = None,
                 registry: dict | None = None, skip_validate: bool = False, allow_unlisted: bool = False,
                 max_runs: int = 6, dry_run: bool = False, log=print) -> list[dict]:
    wf, meta, raw = load_template(template_path)
    name = meta.get("name") or Path(template_path).stem
    params = dict(meta.get("defaults") or {})
    for k, v in sets.items():
        key = k.strip().strip("_").upper()
        params[key] = coerce(v, params.get(key))
    errs = check_rules(params, meta.get("rules") or {})
    if errs:
        raise ComfyError("Tham số sai quy tắc của mẫu: " + "; ".join(errs))
    if not isinstance(params.get("SEED"), int):
        raise ComfyError("Mẫu không có __SEED__ dạng số – không thể cố định kết quả")
    run_seeds = seed_list or [params["SEED"] + i for i in range(seeds)]
    if len(run_seeds) > max_runs:
        raise ComfyError(f"{len(run_seeds)} lượt vượt giới hạn --max-runs {max_runs}")

    if dry_run:
        p = dict(params, SEED=run_seeds[0], PREFIX=f"assetgen/{tag or name}_{run_seeds[0]}")
        for i, img in enumerate(input_images or [], start=1):
            p["INPUT_IMAGE" if i == 1 else f"INPUT_IMAGE_{i}"] = img.name
        filled = fill(wf, p)
        left = leftover_placeholders(filled)
        if left:
            raise ComfyError(f"Còn chỗ giữ chỗ chưa có giá trị: {sorted(left)} – thêm --set KEY=...")
        log(json.dumps(filled, ensure_ascii=False, indent=2))
        return []

    object_info = c.get("/object_info")
    if not skip_validate:
        issues = validate(wf, object_info, registry or load_registry(None), allow_unlisted)
        bad = [i for i in issues if i["level"] == "error"]
        if bad:
            raise ComfyError("Mẫu chưa chạy được trên ComfyUI này:\n" +
                             "\n".join(f"  - {('node ' + i['node'] + ': ') if i['node'] else ''}{i['msg']}" for i in bad))
    stats = c.get("/system_stats") or {}
    devs = stats.get("devices") or []
    gpu = max(devs, key=lambda d: d.get("vram_total", 0)) if devs else {}
    for i, img in enumerate(input_images or [], start=1):
        if not img.exists():
            raise ComfyError(f"Không thấy ảnh đầu vào {img}")
        params["INPUT_IMAGE" if i == 1 else f"INPUT_IMAGE_{i}"] = c.upload_image(img)
        log(f"  đã tải lên {img.name}")

    client_id = uuid.uuid4().hex
    results = []
    for seed in run_seeds:
        p = dict(params, SEED=seed, PREFIX=f"assetgen/{tag or name}_{seed}")
        filled = fill(wf, p)
        left = leftover_placeholders(filled)
        if left:
            raise ComfyError(f"Còn chỗ giữ chỗ chưa có giá trị: {sorted(left)} – thêm --set KEY=...")
        out_dir = dest if len(run_seeds) == 1 else dest / f"seed_{seed}"
        if out_dir.exists() and any(out_dir.iterdir()) and not overwrite:
            raise ComfyError(f"{out_dir} đã có file – dùng --overwrite hoặc chọn --dest khác")
        out_dir.mkdir(parents=True, exist_ok=True)
        if overwrite:
            for f in out_dir.iterdir():
                if f.is_file():
                    f.unlink()
        log(f"▶ seed {seed} → {out_dir}")
        started = now_iso()
        t0 = time.time()
        res = c.post_json("/prompt", {"prompt": filled, "client_id": client_id}) or {}
        if res.get("node_errors"):
            raise ComfyError(f"Workflow bị từ chối: {format_comfy_error(res)}")
        pid = res.get("prompt_id")
        if not pid:
            raise ComfyError(f"ComfyUI không trả prompt_id: {res}")
        try:
            entry = wait_for(c, pid, timeout, poll, log)
        except KeyboardInterrupt:
            c.interrupt(pid)
            raise
        files = []
        for nid, key, it in output_items(entry):
            data = c.view(it)
            target = out_dir / Path(it["filename"]).name
            target.write_bytes(data)
            files.append({"file": target.name, "node": nid, "kind": key, "sha256": sha256_bytes(data)})
        if not files:
            raise ComfyError("Job xong nhưng không có file đầu ra – workflow thiếu node Save?")
        prov = {
            "tool": "comfy_local.py", "tool_version": VERSION,
            "template": name, "template_file": Path(template_path).name, "template_sha256": sha256_bytes(raw),
            "template_status": meta.get("status"),
            "seed": seed,
            "params": {k: v for k, v in p.items() if k != "PREFIX"},
            "models": model_files_in(filled),
            "comfy": {"url": c.url, "comfyui_version": (stats.get("system") or {}).get("comfyui_version"),
                      "gpu": gpu.get("name"), "vram_gb": round(gpu.get("vram_total", 0) / 1024 ** 3, 1)},
            "prompt_id": pid, "started": started, "finished": now_iso(),
            "duration_s": round(time.time() - t0, 1),
            "outputs": files,
        }
        (out_dir / "provenance.json").write_text(json.dumps(prov, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"  ✓ {len(files)} file, {prov['duration_s']}s")
        results.append({"seed": seed, "dir": str(out_dir), "files": len(files), "duration_s": prov["duration_s"]})
    return results


# ───────────────────────────── doctor ─────────────────────────────

def doctor(c: Comfy, templates_dir: Path, registry: dict) -> dict:
    stats = c.get("/system_stats") or {}
    oi = c.get("/object_info") or {}
    sysd = stats.get("system") or {}
    devs = stats.get("devices") or []
    gpu = max(devs, key=lambda d: d.get("vram_total", 0)) if devs else None
    vram = (gpu or {}).get("vram_total", 0) / 1024 ** 3
    ram = sysd.get("ram_total", 0) / 1024 ** 3
    gpu_type = ((gpu or {}).get("type") or "").lower()
    guide = next(msg for lo, hi, msg in VRAM_GUIDE if lo <= vram < hi)
    notes = []
    if gpu is None or gpu_type == "cpu":
        guide = VRAM_GUIDE[0][2]
        notes.append("ComfyUI đang chạy bằng CPU – không dùng được cho video.")
    if 0 < ram < 32:
        notes.append(f"RAM {ram:.0f} GB < 32 GB: offload mô hình lớn sẽ rất chậm hoặc tràn bộ nhớ.")
    inv = model_inventory(oi)
    have = {Path(f.replace('\\', '/')).name.lower() for files in inv.values() for f in files}
    templates = []
    for tp in sorted(Path(templates_dir).glob("*.template.json")):
        try:
            wf, meta, _ = load_template(tp)
        except (ComfyError, json.JSONDecodeError) as e:
            templates.append({"file": tp.name, "ready": False, "problems": [str(e)]})
            continue
        problems = [i["msg"] for i in validate(wf, oi, registry) if i["level"] == "error"]
        need = meta.get("min_vram_gb")
        if need and vram and vram < need:
            problems.append(f"cần ≥ {need} GB VRAM, máy có {vram:.1f} GB")
        templates.append({"file": tp.name, "status": meta.get("status"), "ready": not problems, "problems": problems,
                          "missing_models": [m for m in meta.get("required_models", [])
                                             if Path(m).name.lower() not in have]})
    return {
        "comfyui_version": sysd.get("comfyui_version"), "python": sysd.get("python_version"),
        "pytorch": sysd.get("pytorch_version"), "os": sysd.get("os"),
        "gpu": (gpu or {}).get("name"), "vram_gb": round(vram, 1), "ram_gb": round(ram, 1),
        "recommendation": guide, "notes": notes, "models": inv, "templates": templates,
    }


# ───────────────────────────── CLI ─────────────────────────────

def _parse_sets(items: list[str]) -> dict[str, str]:
    out = {}
    for it in items or []:
        if "=" not in it:
            raise ComfyError(f"--set cần dạng KEY=VALUE, nhận được {it!r}")
        k, v = it.split("=", 1)
        out[k] = v
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Điều khiển ComfyUI cục bộ cho asset-gen (không cần Cloud).")
    ap.add_argument("--url", default=DEFAULT_URL, help=f"địa chỉ ComfyUI (mặc định {DEFAULT_URL})")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor", help="kiểm máy và mô hình")
    d.add_argument("--templates", default=str(DEFAULT_TEMPLATES))
    d.add_argument("--registry")
    d.add_argument("--json", action="store_true")

    t = sub.add_parser("templatize", help="workflow Export (API) → mẫu asset-gen")
    t.add_argument("--workflow", required=True)
    t.add_argument("--out", required=True)
    t.add_argument("--name")
    t.add_argument("--description", default="")
    t.add_argument("--min-vram", type=float)
    t.add_argument("--rule", action="append", default=[], help="vd. NUM_FRAMES=4n+1, WIDTH=multiple_of_32")

    v = sub.add_parser("validate", help="đối chiếu mẫu với ComfyUI đang chạy")
    v.add_argument("--template", required=True)
    v.add_argument("--registry")
    v.add_argument("--allow-unlisted", action="store_true")

    r = sub.add_parser("run", help="chạy mẫu, tải kết quả về")
    r.add_argument("--template", required=True)
    r.add_argument("--dest", required=True, help="thư mục nhận kết quả (vd. raw/<monster_id>/<anim>)")
    r.add_argument("--set", action="append", default=[], help="KEY=VALUE, vd. PROMPT=\"...\" NUM_FRAMES=49")
    r.add_argument("--input-image", action="append", default=[], help="ảnh đầu vào (lặp lại cho ảnh thứ 2: khung cuối)")
    r.add_argument("--seeds", type=int, default=1, help="số biến thể, seed tăng dần từ SEED")
    r.add_argument("--seed-list", help="danh sách seed, vd. 11,42,77")
    r.add_argument("--timeout", type=float, default=3600)
    r.add_argument("--poll", type=float, default=2.0)
    r.add_argument("--max-runs", type=int, default=6)
    r.add_argument("--tag")
    r.add_argument("--overwrite", action="store_true")
    r.add_argument("--registry")
    r.add_argument("--allow-unlisted", action="store_true")
    r.add_argument("--skip-validate", action="store_true")
    r.add_argument("--dry-run", action="store_true", help="chỉ in workflow đã điền, không chạy")

    args = ap.parse_args(argv)
    c = Comfy(args.url)
    try:
        if args.cmd == "doctor":
            rep = doctor(c, Path(args.templates), load_registry(args.registry))
            if args.json:
                print(json.dumps(rep, ensure_ascii=False, indent=2))
                return 0
            print(f"ComfyUI {rep['comfyui_version']} · Python {rep['python']} · PyTorch {rep['pytorch']} · {rep['os']}")
            print(f"GPU: {rep['gpu']} · VRAM {rep['vram_gb']} GB · RAM {rep['ram_gb']} GB")
            print(f"Gợi ý: {rep['recommendation']}")
            for n in rep["notes"]:
                print(f"Lưu ý: {n}")
            print("Mô hình đã có:")
            for folder, files in rep["models"].items():
                print(f"  {folder}: {', '.join(files) if files else '(trống)'}")
            print("Mẫu workflow:")
            for tp in rep["templates"]:
                mark = "SẴN SÀNG" if tp["ready"] else "CHƯA"
                print(f"  [{mark}] {tp['file']} ({tp.get('status')})")
                for p in tp["problems"]:
                    print(f"      - {p}")
            return 0

        if args.cmd == "templatize":
            src = Path(args.workflow)
            raw = src.read_bytes()
            wf, defaults, log = templatize(json.loads(raw))
            rules = {}
            for rr in args.rule:
                k, _, val = rr.partition("=")
                rules[k.strip().upper()] = val.strip()
            meta = {
                "schema": TEMPLATE_SCHEMA, "name": args.name or src.stem, "description": args.description,
                "status": "untested", "created": now_iso(),
                "source": {"file": src.name, "sha256": sha256_bytes(raw)},
                "defaults": defaults, "rules": rules, "min_vram_gb": args.min_vram,
                "required_models": model_files_in(wf),
            }
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({"_asset_gen": meta, "workflow": wf}, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"Đã tạo mẫu {out}")
            print("\n".join(log) if log else "  (không thay gì)")
            print("Kiểm lại các dòng trên, rồi chạy: validate → run thử → đổi status thành \"tested\".")
            return 0

        if args.cmd == "validate":
            wf, meta, _ = load_template(Path(args.template))
            issues = validate(wf, c.get("/object_info") or {}, load_registry(args.registry), args.allow_unlisted)
            for i in issues:
                where = f"node {i['node']}: " if i["node"] else ""
                print(f"[{i['level'].upper()}] {where}{i['msg']}")
            n_err = sum(1 for i in issues if i["level"] == "error")
            print("OK – mẫu chạy được trên ComfyUI này." if n_err == 0 else f"{n_err} lỗi.")
            return 0 if n_err == 0 else 2

        if args.cmd == "run":
            seed_list = [int(s) for s in args.seed_list.split(",")] if args.seed_list else None
            res = run_template(
                c, Path(args.template), _parse_sets(args.set), Path(args.dest), seeds=args.seeds,
                seed_list=seed_list, input_images=[Path(p) for p in args.input_image], timeout=args.timeout,
                poll=args.poll, overwrite=args.overwrite, tag=args.tag, registry=load_registry(args.registry),
                skip_validate=args.skip_validate, allow_unlisted=args.allow_unlisted, max_runs=args.max_runs,
                dry_run=args.dry_run)
            if res:
                total = sum(r["duration_s"] for r in res)
                print(f"Xong {len(res)} lượt, tổng {total:.0f}s GPU.")
            return 0
    except ComfyError as e:
        print(f"LỖI: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Đã dừng.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
