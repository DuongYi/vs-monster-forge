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
import base64
import fnmatch
import hashlib
import json
import os
import re
import socket
import ssl
import struct
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, parse, request

VERSION = "0.2.0"
TEMPLATE_SCHEMA = "asset-gen.comfy-template/1"
DEFAULT_URL = "http://127.0.0.1:8188"
# ComfyUI Portable/cài tay mặc định cổng 8188; ComfyUI Desktop mặc định cổng 8000.
LOCAL_CANDIDATES = ("http://127.0.0.1:8188", "http://127.0.0.1:8000")
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


def find_comfy(candidates=LOCAL_CANDIDATES, timeout: float = 3) -> str | None:
    """Trả địa chỉ đầu tiên trong danh sách có ComfyUI đang trả lời, hoặc None."""
    for url in candidates:
        try:
            Comfy(url, timeout=timeout).get("/system_stats")
            return url.rstrip("/")
        except (ComfyError, ValueError):
            continue
    return None


# ───────────────────────────── WebSocket (tiến độ trực tiếp) ─────────────────────────────

WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class ComfyWS:
    """Client WebSocket tối giản (RFC 6455, chỉ thư viện chuẩn) để nhận sự kiện tiến độ từ ComfyUI /ws."""

    def __init__(self, base_url: str, client_id: str, timeout: float = 10):
        u = parse.urlparse(base_url)
        secure = u.scheme == "https"
        host = u.hostname or "127.0.0.1"
        port = u.port or (443 if secure else 80)
        path = (u.path.rstrip("/") or "") + f"/ws?clientId={parse.quote(client_id)}"
        sock = socket.create_connection((host, port), timeout=timeout)
        if secure:
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
        key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                      f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk or len(buf) > 65536:
                sock.close()
                raise ComfyError("ComfyUI đóng kết nối WebSocket khi bắt tay")
            buf += chunk
        head, _, rest = buf.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        if " 101 " not in lines[0] + " ":
            sock.close()
            raise ComfyError(f"ComfyUI không mở WebSocket ({lines[0]})")
        headers = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:])}
        expect = base64.b64encode(hashlib.sha1(key.encode() + WS_GUID).digest()).decode()
        if headers.get("sec-websocket-accept") != expect:
            sock.close()
            raise ComfyError("Bắt tay WebSocket sai khoá")
        sock.settimeout(None)
        self.sock, self._buf = sock, rest
        self._send_lock = threading.Lock()
        self.closed = False

    def _recv_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise EOFError
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _send(self, op: int, payload: bytes = b"") -> None:
        mask = os.urandom(4)
        n = len(payload)
        head = bytes([0x80 | op])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        body = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        with self._send_lock:
            self.sock.sendall(head + mask + body)

    def recv(self) -> tuple[int, bytes]:
        """Trả (opcode, dữ liệu) của một thông điệp hoàn chỉnh: 1 = chữ (JSON), 2 = nhị phân (ảnh xem trước)."""
        op_msg, data = None, b""
        while True:
            b1, b2 = self._recv_exact(2)
            fin, op = b1 & 0x80, b1 & 0x0F
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if b2 & 0x80 else None
            payload = self._recv_exact(n)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if op == 0x9:          # ping → pong
                self._send(0xA, payload)
                continue
            if op == 0xA:
                continue
            if op == 0x8:          # đóng
                raise EOFError
            if op in (0x1, 0x2):
                op_msg, data = op, payload
            elif op == 0x0:
                data += payload
            if fin and op_msg is not None:
                return op_msg, data

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self._send(0x8, b"\x03\xe8")
        except OSError:
            pass
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


def extract_preview(data: bytes) -> tuple[bytes, str] | None:
    """Ảnh xem trước trong thông điệp nhị phân của ComfyUI: 4 byte loại sự kiện, (4 byte định dạng /
    metadata), rồi tới dữ liệu ảnh. Tìm chữ ký ảnh để chịu được các phiên bản đóng gói khác nhau."""
    if len(data) < 12:
        return None
    window = data[4:8192]
    for sig, mime in ((b"\xff\xd8\xff", "image/jpeg"), (b"\x89PNG\r\n\x1a\n", "image/png")):
        i = window.find(sig)
        if i != -1:
            return data[4 + i:], mime
    i = window.find(b"RIFF")
    if i != -1 and window[i + 8:i + 12] == b"WEBP":
        return data[4 + i:], "image/webp"
    return None


# Nhãn tiếng Việt cho từng loại node (theo tên lớp), để người không rành ComfyUI vẫn hiểu máy đang làm gì.
PHASE_RULES = [
    (re.compile(r"^LoadImage"), "Nạp ảnh chuẩn"),
    (re.compile(r"Loader|^Load"), "Nạp mô hình"),
    (re.compile(r"^CLIPTextEncode|TextEncode"), "Đọc mô tả động tác"),
    (re.compile(r"ModelSampling"), "Cấu hình mô hình"),
    (re.compile(r"Sampler"), "Sinh khung"),
    (re.compile(r"ToVideo|Latent"), "Chuẩn bị khung"),
    (re.compile(r"VAEDecode|Decode"), "Giải mã khung hình"),
    (re.compile(r"^Save|VideoCombine|Preview"), "Lưu kết quả"),
]


def phase_label(class_type: str) -> str:
    for rx, label in PHASE_RULES:
        if rx.search(class_type or ""):
            return label
    return class_type or "?"


class ProgressTracker:
    """Gom sự kiện WebSocket thành trạng thái dễ hiển thị: node đang chạy, bước x/y, thời gian còn lại, từng node."""

    def __init__(self, wf: dict):
        self.nodes = {}
        for nid in _sorted_ids(wf):
            n = wf[nid]
            ct = n.get("class_type", "")
            self.nodes[str(nid)] = {"id": str(nid), "class": ct, "title": (n.get("_meta") or {}).get("title") or ct,
                                    "label": phase_label(ct), "state": "pending", "t0": None, "time_s": None}
        self.order: list[str] = []
        self.current: str | None = None
        self.step = self.steps = None
        self.step_t0 = None
        self.status = "queued"
        self.queue_remaining = None
        self.t_start = time.time()
        self.t_exec = None
        self.previews = 0
        self.error = None
        self.lock = threading.Lock()

    def _touch(self, nid: str) -> dict | None:
        n = self.nodes.get(str(nid))
        if n is not None and n["id"] not in self.order:
            self.order.append(n["id"])
        return n

    def _close_current(self, state: str = "done") -> None:
        n = self.nodes.get(self.current) if self.current else None
        if n and n["state"] == "running":
            n["state"] = state
            n["time_s"] = round(time.time() - n["t0"], 1) if n["t0"] else None
        self.current = None
        self.step = self.steps = self.step_t0 = None

    def _start(self, nid: str) -> None:
        if self.current == str(nid):
            return
        self._close_current()
        n = self._touch(nid)
        if n:
            n["state"], n["t0"] = "running", time.time()
            self.current = n["id"]
        self.status = "running"

    def handle(self, msg: dict) -> bool:
        """Cập nhật theo một thông điệp JSON; trả True nếu trạng thái hiển thị đổi."""
        t = msg.get("type")
        d = msg.get("data") or {}
        with self.lock:
            if t == "status":
                q = ((d.get("status") or {}).get("exec_info") or {}).get("queue_remaining")
                changed = q != self.queue_remaining
                self.queue_remaining = q
                return changed and self.status == "queued"
            if t == "execution_start":
                self.status, self.t_exec = "running", time.time()
                return True
            if t == "execution_cached":
                for nid in d.get("nodes") or []:
                    n = self._touch(nid)
                    if n:
                        n["state"] = "cached"
                return True
            if t == "executing":
                nid = d.get("node")
                if nid is None:
                    self._close_current()
                    return True
                self._start(nid)
                return True
            if t == "progress":
                if d.get("node") is not None and str(d["node"]) != self.current:
                    self._start(d["node"])
                v, m = d.get("value"), d.get("max")
                if isinstance(v, (int, float)) and isinstance(m, (int, float)) and m > 0:
                    if self.step is None or v < self.step:
                        self.step_t0 = time.time()
                    self.step, self.steps = v, m
                return True
            if t == "progress_state":
                for nid, s in (d.get("nodes") or {}).items():
                    st = (s or {}).get("state")
                    if st == "running":
                        self._start(nid)
                        v, m = s.get("value"), s.get("max")
                        if isinstance(v, (int, float)) and isinstance(m, (int, float)) and m > 1:
                            if self.step is None or v < self.step:
                                self.step_t0 = time.time()
                            self.step, self.steps = v, m
                    elif st == "finished":
                        n = self._touch(nid)
                        if n and n["state"] in ("pending", "running"):
                            if self.current == n["id"]:
                                self._close_current()
                            else:
                                n["state"] = "done"
                return True
            if t == "executed":
                nid = str(d.get("node"))
                if nid == self.current:
                    self._close_current()
                else:
                    n = self._touch(nid)
                    if n and n["state"] in ("pending", "running"):
                        n["state"] = "done"
                return True
            if t == "execution_success":
                self._close_current()
                self.status = "done"
                return True
            if t == "execution_error":
                n = self._touch(d.get("node_id"))
                if n:
                    n["state"] = "error"
                self.current = None
                self.status = "error"
                self.error = (d.get("exception_message") or "").strip() or None
                return True
            if t == "execution_interrupted":
                self._close_current("interrupted")
                self.status = "interrupted"
                return True
        return False

    def got_preview(self) -> None:
        with self.lock:
            self.previews += 1

    def finish(self) -> None:
        with self.lock:
            self._close_current()
            for n in self.nodes.values():
                if n["state"] == "running":
                    n["state"] = "done"
            if self.status not in ("error", "interrupted"):
                self.status = "done"

    def snapshot(self) -> dict:
        with self.lock:
            now = time.time()
            cur = self.nodes.get(self.current) if self.current else None
            eta = None
            if self.step and self.steps and self.step_t0 and self.step > 1:
                eta = (now - self.step_t0) / (self.step - 1) * (self.steps - self.step)
            if self.status == "queued":
                phase = "Đang chờ ComfyUI" + (f" ({self.queue_remaining} việc trong hàng đợi)" if self.queue_remaining else "")
            else:
                phase = {"done": "Xong", "error": "Lỗi", "interrupted": "Đã dừng"}.get(self.status) or (
                    cur["label"] if cur else "Đang chạy")
            done = sum(1 for n in self.nodes.values() if n["state"] in ("done", "cached"))
            ordered = [self.nodes[i] for i in self.order] + [n for i, n in self.nodes.items() if i not in self.order]
            return {
                "status": self.status, "phase": phase,
                "node": ({"id": cur["id"], "class": cur["class"], "title": cur["title"]} if cur else None),
                "step": self.step, "steps": self.steps, "eta_s": round(eta) if eta is not None else None,
                "done_nodes": done, "total_nodes": len(self.nodes),
                "elapsed_s": round(now - self.t_start, 1), "previews": self.previews, "error": self.error,
                "nodes": [{k: n[k] for k in ("id", "class", "title", "label", "state", "time_s")} for n in ordered],
            }


def watch_progress(c: "Comfy", client_id: str, tracker: ProgressTracker, on_update, log=print):
    """Mở WebSocket và đọc sự kiện ở luồng nền. Trả đối tượng ComfyWS (để đóng) hoặc None nếu không kết nối được."""
    try:
        ws = ComfyWS(c.url, client_id, timeout=min(10, c.timeout))
    except (OSError, ComfyError) as e:
        log(f"  (không theo dõi chi tiết được, vẫn chạy bình thường: {e})")
        return None

    def loop():
        while not ws.closed:
            try:
                op, data = ws.recv()
            except (EOFError, OSError, ValueError):
                return
            try:
                if op == 1:
                    msg = json.loads(data.decode("utf-8", "replace"))
                    if isinstance(msg, dict) and tracker.handle(msg):
                        on_update(tracker.snapshot(), None)
                elif op == 2:
                    prev = extract_preview(data)
                    if prev:
                        tracker.got_preview()
                        on_update(tracker.snapshot(), prev)
            except Exception:  # noqa: BLE001 – lỗi hiển thị không được làm hỏng việc sinh
                continue

    threading.Thread(target=loop, daemon=True, name="comfy-ws").start()
    return ws


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
                 max_runs: int = 6, dry_run: bool = False, log=print, progress=None) -> list[dict]:
    """progress(snapshot: dict, preview: (bytes, mime) | None) – gọi khi tiến độ đổi (cần ComfyUI mở WebSocket /ws).
    snapshot có thêm 'seed', 'run' (thứ tự lượt) và 'runs' (tổng lượt)."""
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

    results = []
    for run_i, seed in enumerate(run_seeds, start=1):
        client_id = uuid.uuid4().hex  # mỗi lượt một client_id: sự kiện WebSocket chỉ thuộc về lượt này
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
        tracker = ProgressTracker(filled)
        ws = None
        if progress is not None:
            def emit(snap, prev, _seed=seed, _i=run_i):
                try:
                    progress(dict(snap, seed=_seed, run=_i, runs=len(run_seeds)), prev)
                except Exception:  # noqa: BLE001 – lỗi hiển thị không được làm hỏng việc sinh
                    pass
            ws = watch_progress(c, client_id, tracker, emit, log)
            emit(tracker.snapshot(), None)
        try:
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
            tracker.finish()
            if progress is not None:
                emit(tracker.snapshot(), None)
        except ComfyError as e:
            if progress is not None:
                with tracker.lock:
                    if tracker.status not in ("error", "interrupted"):
                        tracker.status = "interrupted" if "dừng" in str(e) else "error"
                    tracker.error = tracker.error or str(e)
                emit(tracker.snapshot(), None)
            raise
        finally:
            if ws is not None:
                ws.close()
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

def cli_progress(out=print):
    """In tiến độ gọn ra console: mỗi lần đổi bước, và mỗi 25% trong bước sinh khung."""
    last = {"key": None}

    def cb(snap: dict, prev) -> None:
        if prev is not None:
            return
        quarter = int(4 * snap["step"] / snap["steps"]) if snap.get("step") and snap.get("steps") else None
        key = (snap.get("run"), snap["phase"], quarter)
        if key == last["key"]:
            return
        last["key"] = key
        line = f"    [{snap.get('run', 1)}/{snap.get('runs', 1)}] {snap['phase']}"
        if snap.get("steps"):
            line += f": bước {snap['step']}/{snap['steps']}"
            if snap.get("eta_s") is not None:
                line += f", còn khoảng {snap['eta_s']}s"
        out(line)

    return cb


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
    ap.add_argument("--url", default=None,
                    help="địa chỉ ComfyUI (mặc định tự dò 127.0.0.1:8188 rồi 127.0.0.1:8000)")
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
    url = args.url
    needs_server = args.cmd in ("doctor", "validate") or (args.cmd == "run" and not args.dry_run)
    if url is None:
        url = (find_comfy() if needs_server else None) or DEFAULT_URL
    c = Comfy(url)
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
                dry_run=args.dry_run, progress=None if args.dry_run else cli_progress())
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
