#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Xưởng Quái – máy chủ web cục bộ. Chỉ dùng thư viện chuẩn Python (cộng numpy/opencv/pillow/scipy của gói).

  python app/server.py                 mở http://127.0.0.1:7865 và bật trình duyệt
  python app/server.py --port 8000 --no-browser --workspace D:/xuong-quai

Mặc định chỉ nghe trên 127.0.0.1 (chỉ máy này truy cập được).
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import re
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"
RAW_SENT = object()  # route đã tự gửi phản hồi (ảnh), không gói JSON
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("image/gif", ".gif")


def make_handler(app: core.App, allowed_hosts: set[str]):
    routes: list[tuple[str, re.Pattern, callable]] = []

    def route(method: str, pattern: str):
        def deco(fn):
            routes.append((method, re.compile("^" + pattern + "$"), fn))
            return fn
        return deco

    ws, jobs = app.ws, app.jobs

    # ── API ──
    @route("GET", r"/api/status")
    def status(h, q):
        return {"version": core.APP_VERSION, "workspace": str(ws.root.resolve()),
                "settings": app.settings.data, "doctor": app.doctor(refresh=q.get("refresh") == "1")}

    @route("PUT", r"/api/settings")
    def put_settings(h, q):
        data = app.settings.update(h.json())
        app.doctor(refresh=True)
        return data

    @route("GET", r"/api/presets")
    def presets(h, q):
        return ws.presets()

    @route("GET", r"/api/templates")
    def templates(h, q):
        return app.templates()

    @route("GET", r"/api/monsters")
    def monsters(h, q):
        return ws.list()

    @route("POST", r"/api/monsters")
    def create(h, q):
        b = h.json()
        spec = ws.create(b.get("monster_id", ""), b.get("display_name", ""), b.get("preset"), b.get("spine_json"),
                         float(b.get("fps") or 12), int(b["target_height"]) if b.get("target_height") else None)
        return {"id": spec["monster_id"]}

    @route("GET", r"/api/monsters/(?P<mid>[a-z0-9_]+)")
    def monster(h, q, mid):
        return ws.state(mid, jobs)

    @route("DELETE", r"/api/monsters/(?P<mid>[a-z0-9_]+)")
    def delete_monster(h, q, mid):
        if jobs.active(mid):
            raise core.AppError("Quái này đang có việc chạy")
        ws.delete(mid)
        return {"ok": True}

    @route("PUT", r"/api/monsters/(?P<mid>[a-z0-9_]+)/spec")
    def put_spec(h, q, mid):
        return ws.save_spec(mid, h.json().get("spec"))

    @route("POST", r"/api/monsters/(?P<mid>[a-z0-9_]+)/ref")
    def upload_ref(h, q, mid):
        return {"ref": ws.set_ref(mid, q.get("name", "ref.png"), h.body())}

    @route("POST", r"/api/monsters/(?P<mid>[a-z0-9_]+)/anims/(?P<anim>[A-Za-z0-9_\-]+)/frames")
    def upload_frame(h, q, mid, anim):
        n = ws.import_frame(mid, anim, q.get("name", ""), h.body(), q.get("first") == "1")
        return {"count": n}

    @route("POST", r"/api/monsters/(?P<mid>[a-z0-9_]+)/anims/(?P<anim>[A-Za-z0-9_\-]+)/clear")
    def clear(h, q, mid, anim):
        ws.clear_raw(mid, anim)
        return {"ok": True}

    @route("POST", r"/api/monsters/(?P<mid>[a-z0-9_]+)/anims/(?P<anim>[A-Za-z0-9_\-]+)/generate")
    def generate(h, q, mid, anim):
        b = h.json()
        seed = b.get("seed")
        return app.start_generate(mid, anim, b.get("template", ""), b.get("motion", ""), b.get("count", 1),
                                  int(seed) if seed not in (None, "") else None, b.get("sets") or {})

    @route("POST", r"/api/monsters/(?P<mid>[a-z0-9_]+)/anims/(?P<anim>[A-Za-z0-9_\-]+)/choose")
    def choose(h, q, mid, anim):
        ws.choose_variant(mid, anim, int(h.json().get("seed")))
        return {"ok": True}

    @route("DELETE", r"/api/monsters/(?P<mid>[a-z0-9_]+)/anims/(?P<anim>[A-Za-z0-9_\-]+)/variants/(?P<seed>\d+)")
    def del_variant(h, q, mid, anim, seed):
        ws.delete_variant(mid, anim, int(seed))
        return {"ok": True}

    @route("POST", r"/api/monsters/(?P<mid>[a-z0-9_]+)/process")
    def process(h, q, mid):
        return app.start_process(mid)

    @route("POST", r"/api/monsters/(?P<mid>[a-z0-9_]+)/approve")
    def approve(h, q, mid):
        b = h.json()
        return ws.approve(mid, app.settings.get("reviewer"), b.get("note", ""), bool(b.get("force")))

    @route("POST", r"/api/monsters/(?P<mid>[a-z0-9_]+)/unapprove")
    def unapprove(h, q, mid):
        ws.unapprove(mid)
        return {"ok": True}

    @route("GET", r"/api/jobs")
    def list_jobs(h, q):
        return jobs.list(q.get("monster"))

    @route("GET", r"/api/jobs/(?P<jid>[a-f0-9]+)/preview")
    def job_preview(h, q, jid):
        prev = jobs.preview(jid)
        if not prev:
            raise core.AppError("Việc này chưa có ảnh xem trước")
        data, mime = prev
        h.send_response(200)
        h.send_header("Content-Type", mime)
        h.send_header("Content-Length", str(len(data)))
        h.send_header("Cache-Control", "max-age=3600")  # URL có ?v=… đổi theo mỗi ảnh mới
        h.end_headers()
        h.wfile.write(data)
        return RAW_SENT

    @route("POST", r"/api/jobs/(?P<jid>[a-f0-9]+)/cancel")
    def cancel_job(h, q, jid):
        return jobs.cancel(jid)

    class Handler(BaseHTTPRequestHandler):
        server_version = "XuongQuai/" + core.APP_VERSION

        def log_message(self, fmt, *args):
            pass

        # — tiện ích —
        def body(self) -> bytes:
            n = int(self.headers.get("Content-Length") or 0)
            if n > core.MAX_UPLOAD:
                raise core.AppError("Dữ liệu gửi lên quá lớn")
            return self.rfile.read(n) if n else b""

        def json(self) -> dict:
            raw = self.body()
            try:
                data = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                raise core.AppError("Dữ liệu gửi lên không phải JSON")
            if not isinstance(data, dict):
                raise core.AppError("Dữ liệu gửi lên phải là object JSON")
            return data

        def send_json(self, code: int, obj) -> None:
            data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def send_file(self, p: Path, cache: bool = False) -> None:
            if not p.is_file():
                return self.send_json(404, {"error": "Không có file"})
            data = p.read_bytes()
            ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/json", "text/javascript"):
                ctype += "; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "max-age=31536000" if cache else "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def host_ok(self) -> bool:
            if allowed_hosts is None:  # chế độ mở cho mạng nội bộ
                return True
            raw = (self.headers.get("Host") or "").lower()
            host = raw.split("]")[0] + "]" if raw.startswith("[") else raw.split(":")[0]
            return host in allowed_hosts

        # — điều phối —
        def dispatch(self, method: str):
            if not self.host_ok():
                return self.send_json(403, {"error": "Host không được phép"})
            u = urlparse(self.path)
            path = unquote(u.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if method != "GET" and self.headers.get("X-Xuong") != "1":
                # chặn trang web lạ gửi lệnh tới máy chủ cục bộ (trình duyệt không tự gắn header này khi gọi chéo)
                return self.send_json(403, {"error": "Thiếu header X-Xuong"})
            try:
                if method == "GET" and (path == "/" or path == "/index.html"):
                    return self.send_file(STATIC / "index.html")
                if method == "GET" and path.startswith("/static/"):
                    p = (STATIC / path[len("/static/"):]).resolve()
                    if STATIC.resolve() not in p.parents:
                        return self.send_json(404, {"error": "Không có file"})
                    return self.send_file(p)
                if method == "GET" and path.startswith("/files/"):
                    _, _, mid, rel = path.split("/", 3) if path.count("/") >= 3 else ("", "", "", "")
                    return self.send_file(ws.file_in(mid, rel), cache=True)
                for m, rx, fn in routes:
                    if m != method:
                        continue
                    mt = rx.match(path)
                    if mt:
                        out = fn(self, q, **mt.groupdict())
                        return None if out is RAW_SENT else self.send_json(200, out)
                return self.send_json(404, {"error": f"Không có đường dẫn {path}"})
            except core.AppError as e:
                return self.send_json(400, {"error": str(e)})
            except (core.cl.ComfyError, core.pp.InputError) as e:
                return self.send_json(400, {"error": str(e)})
            except (BrokenPipeError, ConnectionResetError):
                return None
            except Exception as e:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                return self.send_json(500, {"error": f"Lỗi máy chủ: {e}"})

        def do_GET(self):
            self.dispatch("GET")

        def do_POST(self):
            self.dispatch("POST")

        def do_PUT(self):
            self.dispatch("PUT")

        def do_DELETE(self):
            self.dispatch("DELETE")

    return Handler


def serve(app: core.App, host: str = "127.0.0.1", port: int = 7865, tries: int = 10) -> ThreadingHTTPServer:
    allowed = None if host in ("0.0.0.0", "::", "") else {"127.0.0.1", "localhost", "[::1]", host.lower()}
    last = None
    for p in range(port, port + tries):
        try:
            srv = ThreadingHTTPServer((host, p), make_handler(app, allowed))
            srv.daemon_threads = True
            return srv
        except OSError as e:
            last = e
    raise SystemExit(f"Không mở được cổng {port}–{port + tries - 1}: {last}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Xưởng Quái – công cụ làm quái FBF chạy trên máy")
    ap.add_argument("--host", default="127.0.0.1", help="mặc định chỉ máy này truy cập được")
    ap.add_argument("--port", type=int, default=7865)
    ap.add_argument("--workspace", help="thư mục lưu dữ liệu (mặc định: workspace/ cạnh ứng dụng, hoặc theo settings.json)")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args(argv)
    app = core.App(workspace=Path(args.workspace) if args.workspace else None)
    srv = serve(app, args.host, args.port)
    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '') else args.host}:{srv.server_address[1]}"
    print(f"Xưởng Quái {core.APP_VERSION} đang chạy tại {url}")
    print(f"Dữ liệu: {app.ws.root.resolve()}")
    print("Đóng cửa sổ này (hoặc Ctrl+C) để tắt.")
    if args.host not in ("127.0.0.1", "localhost"):
        print("CẢNH BÁO: đang mở cho máy khác trong mạng truy cập. Không có đăng nhập – chỉ dùng trong mạng nội bộ tin cậy.")
    threading.Thread(target=app.doctor, daemon=True).start()
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
