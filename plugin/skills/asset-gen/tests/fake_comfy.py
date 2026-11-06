# -*- coding: utf-8 -*-
"""ComfyUI giả lập (chỉ các endpoint comfy_local.py dùng) để test không cần GPU."""
from __future__ import annotations

import io
import json
import math
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from PIL import Image

import synth

GB = 1024 ** 3


class FakeState:
    def __init__(self, vram_gb=16, ram_gb=64, unet=("wan2.2_ti2v_5B_fp16.safetensors",),
                 clip=("umt5_xxl_fp8_e4m3fn_scaled.safetensors",), vae=("wan2.2_vae.safetensors",),
                 extra_nodes=None, fail_exec=False, never_finish=False, delay_polls=1, frame_size=512):
        self.vram_gb, self.ram_gb = vram_gb, ram_gb
        self.unet, self.clip, self.vae = list(unet), list(clip), list(vae)
        self.extra_nodes = extra_nodes or {}
        self.fail_exec, self.never_finish, self.delay_polls = fail_exec, never_finish, delay_polls
        self.frame_size = frame_size
        self.uploads: dict[str, bytes] = {"example.png": b""}
        self.files: dict[tuple[str, str], bytes] = {}
        self.jobs: dict[str, dict] = {}
        self.interrupted: list = []
        self.deleted: list = []
        self.prompts: list[dict] = []

    def object_info(self):
        oi = {
            "UNETLoader": {"input": {"required": {"unet_name": [self.unet], "weight_dtype": [["default", "fp8_e4m3fn"]]}}},
            "CLIPLoader": {"input": {"required": {"clip_name": [self.clip], "type": [["stable_diffusion", "wan"]]},
                                     "optional": {"device": [["default", "cpu"]]}}},
            "VAELoader": {"input": {"required": {"vae_name": [self.vae]}}},
            "ModelSamplingSD3": {"input": {"required": {"model": ["MODEL"], "shift": ["FLOAT", {"default": 3.0}]}}},
            "CLIPTextEncode": {"input": {"required": {"text": ["STRING", {"multiline": True}], "clip": ["CLIP"]}}},
            "LoadImage": {"input": {"required": {"image": [sorted(self.uploads), {"image_upload": True}]}}},
            "Wan22ImageToVideoLatent": {"input": {"required": {"vae": ["VAE"], "width": ["INT", {}], "height": ["INT", {}],
                                                               "length": ["INT", {}], "batch_size": ["INT", {}]},
                                                  "optional": {"start_image": ["IMAGE"]}}},
            "KSampler": {"input": {"required": {
                "model": ["MODEL"], "seed": ["INT", {}], "steps": ["INT", {}], "cfg": ["FLOAT", {}],
                "sampler_name": [["euler", "uni_pc"]],
                "scheduler": ["COMBO", {"options": ["simple", "karras"]}],   # định dạng combo kiểu mới
                "positive": ["CONDITIONING"], "negative": ["CONDITIONING"], "latent_image": ["LATENT"],
                "denoise": ["FLOAT", {}]}}},
            "VAEDecode": {"input": {"required": {"samples": ["LATENT"], "vae": ["VAE"]}}},
            "SaveImage": {"input": {"required": {"images": ["IMAGE"], "filename_prefix": ["STRING", {}]}}},
        }
        oi.update(self.extra_nodes)
        return oi

    def render(self, wf: dict) -> list[dict]:
        n, prefix, seed = 9, "out", 0
        for node in wf.values():
            inp = node["inputs"]
            if "length" in inp:
                n = int(inp["length"])
            if node["class_type"] == "SaveImage":
                prefix = inp["filename_prefix"]
            if "seed" in inp:
                seed = int(inp["seed"])
        sub, _, base = prefix.rpartition("/")
        items = []
        for i in range(n):
            phase = 2 * math.pi * i / max(1, n - 1)  # khung cuối = khung đầu → vòng khép
            img = synth.draw_monster(phase, offset=(seed % 7, 0), size=(self.frame_size, self.frame_size))
            buf = io.BytesIO()
            Image.fromarray(img).save(buf, "PNG")
            fn = f"{base}_{i + 1:05d}_.png"
            self.files[(sub, fn)] = buf.getvalue()
            items.append({"filename": fn, "subfolder": sub, "type": "output"})
        items.append({"filename": "preview.png", "subfolder": "", "type": "temp"})  # phải bị bỏ qua
        return items


def make_handler(state: FakeState):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, obj=None, raw=None, ctype="application/json"):
            body = raw if raw is not None else json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            return self.rfile.read(int(self.headers.get("Content-Length", 0)))

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/system_stats":
                return self._send(200, {"system": {"os": "nt", "ram_total": state.ram_gb * GB, "comfyui_version": "0.3.99",
                                                   "python_version": "3.12.7", "pytorch_version": "2.9.0+cu128"},
                                        "devices": [{"name": "cuda:0 NVIDIA GeForce RTX 4060 Ti", "type": "cuda",
                                                     "vram_total": state.vram_gb * GB}]})
            if u.path == "/object_info":
                return self._send(200, state.object_info())
            if u.path.startswith("/history/"):
                pid = u.path.rsplit("/", 1)[1]
                job = state.jobs.get(pid)
                if not job:
                    return self._send(200, {})
                job["polls"] += 1
                if job.get("interrupted"):  # ComfyUI thật: job bị dừng → status error + execution_interrupted
                    return self._send(200, {pid: {"outputs": {}, "status": {"status_str": "error", "completed": False,
                                          "messages": [["execution_interrupted", {"prompt_id": pid, "node_id": "9"}]]}}})
                if state.never_finish or job["polls"] <= state.delay_polls:
                    return self._send(200, {})
                if state.fail_exec:
                    return self._send(200, {pid: {"outputs": {}, "status": {"status_str": "error", "completed": False,
                                          "messages": [["execution_error", {"node_id": "9", "node_type": "KSampler",
                                                                            "exception_message": "CUDA out of memory"}]]}}})
                return self._send(200, {pid: {"outputs": {"11": {"images": job["items"]}},
                                              "status": {"status_str": "success", "completed": True, "messages": []}}})
            if u.path == "/view":
                q = parse_qs(u.query)
                key = (q.get("subfolder", [""])[0], q["filename"][0])
                if key not in state.files:
                    return self._send(404, {"error": "not found"})
                return self._send(200, raw=state.files[key], ctype="image/png")
            return self._send(404, {"error": "no route"})

        def do_POST(self):
            u = urlparse(self.path)
            body = self._body()
            if u.path == "/upload/image":
                ctype = self.headers.get("Content-Type", "")
                boundary = ctype.split("boundary=")[1].encode()
                for part in body.split(b"--" + boundary):
                    if b'name="image"' in part:
                        head, _, data = part.partition(b"\r\n\r\n")
                        name = head.split(b'filename="')[1].split(b'"')[0].decode()
                        state.uploads[name] = data.rstrip(b"\r\n")
                        return self._send(200, {"name": name, "subfolder": "", "type": "input"})
                return self._send(400, {"error": "no image"})
            if u.path == "/prompt":
                req = json.loads(body)
                wf = req["prompt"]
                state.prompts.append(wf)
                oi = state.object_info()
                for nid, node in wf.items():
                    if node["class_type"] not in oi:
                        return self._send(400, {"error": {"type": "invalid_prompt",
                                                          "message": f"Cannot execute because node {node['class_type']} does not exist."},
                                                "node_errors": {}})
                    if node["class_type"] == "LoadImage" and node["inputs"]["image"] not in state.uploads:
                        return self._send(400, {"error": {"type": "prompt_outputs_failed_validation", "message": "Prompt outputs failed validation"},
                                                "node_errors": {nid: {"class_type": "LoadImage", "errors": [
                                                    {"message": "Invalid image file", "details": node["inputs"]["image"]}]}}})
                pid = uuid.uuid4().hex
                state.jobs[pid] = {"polls": 0, "items": state.render(wf)}
                return self._send(200, {"prompt_id": pid, "number": len(state.jobs), "node_errors": {}})
            if u.path == "/interrupt":
                req = json.loads(body or b"{}")
                state.interrupted.append(req)
                for pid, job in state.jobs.items():
                    if not req.get("prompt_id") or req["prompt_id"] == pid:
                        job["interrupted"] = True
                return self._send(200, {})
            if u.path == "/queue":
                state.deleted.append(json.loads(body or b"{}"))
                return self._send(200, {})
            return self._send(404, {"error": "no route"})

    return H


class FakeComfy:
    def __init__(self, **kw):
        self.state = FakeState(**kw)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.state))
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.server.shutdown()
        self.server.server_close()
