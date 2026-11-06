# -*- coding: utf-8 -*-
"""Test Xưởng Quái qua HTTP, với ComfyUI giả lập."""
import io
import json
import sys
import threading
import time
from pathlib import Path
from urllib import error, request

import pytest
from PIL import Image

APP = Path(__file__).resolve().parent.parent
ROOT = APP.parent
sys.path.insert(0, str(APP))
sys.path.insert(0, str(ROOT / "plugin" / "skills" / "asset-gen" / "tests"))

import core  # noqa: E402
import server  # noqa: E402
import synth  # noqa: E402
from fake_comfy import FakeComfy  # noqa: E402

SPINE = {"skeleton": {"spine": "4.2.18"}, "animations": {
    "idle": {"bones": {"b": {"translate": [{"time": 0.5}]}}},
    "attack": {"bones": {"b": {"rotate": [{"time": 0.6667}]}}, "events": [{"time": 0.4167, "name": "hit"}]},
}}


class Client:
    def __init__(self, base):
        self.base = base

    def call(self, method, path, body=None, raw=None, headers=None, expect=200):
        h = {"X-Xuong": "1"}
        h.update(headers or {})
        data = None
        if raw is not None:
            data = raw
            h["Content-Type"] = "application/octet-stream"
        elif body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        req = request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with request.urlopen(req, timeout=60) as r:
                code, payload = r.status, r.read()
        except error.HTTPError as e:
            code, payload = e.code, e.read()
        assert code == expect, f"{method} {path} → {code}: {payload[:300]!r}"
        try:
            return json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return payload

    def wait(self, monster, timeout=90):
        t0 = time.time()
        while time.time() - t0 < timeout:
            jobs = self.call("GET", f"/api/jobs?monster={monster}")
            if not any(j["status"] in ("queued", "running") for j in jobs):
                return jobs
            time.sleep(0.1)
        raise AssertionError("việc chạy quá lâu")


@pytest.fixture()
def env(tmp_path):
    with FakeComfy(frame_size=384) as fc:
        settings = core.Settings(tmp_path / "settings.json")
        settings.update({"comfy_url": fc.url, "reviewer": "Hoạ sĩ A"})
        app = core.App(settings=settings, workspace=tmp_path / "ws")
        app.poll = 0.02
        srv = server.serve(app, "127.0.0.1", 0)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            yield Client(f"http://127.0.0.1:{srv.server_address[1]}"), app, fc, tmp_path
        finally:
            srv.shutdown()
            srv.server_close()


def png_bytes(arr) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "PNG")
    return buf.getvalue()


def test_full_flow_spine_ai_process_approve(env):
    c, app, fc, tmp = env
    st = c.call("GET", "/api/status?refresh=1")
    assert st["doctor"]["connected"] and st["doctor"]["vram_gb"] == 16.0

    # 1) tạo quái từ Spine JSON
    r = c.call("POST", "/api/monsters", {"monster_id": "mob_lang_dau_01", "display_name": "Lang Đầu Binh",
                                         "spine_json": json.dumps(SPINE), "fps": 12, "target_height": 160})
    mid = r["id"]
    m = c.call("GET", f"/api/monsters/{mid}")
    assert [a["name"] for a in m["anims"]] == ["idle", "attack"]
    atk = m["anims"][1]
    assert atk["frames"] == 8 and atk["events"] == [{"name": "hit", "frame": 5}]

    # 2) ảnh chuẩn
    c.call("POST", f"/api/monsters/{mid}/ref?name=idle.png", raw=png_bytes(synth.draw_monster(0.0)))
    m = c.call("GET", f"/api/monsters/{mid}")
    assert m["ref"].startswith(f"/files/{mid}/ref/ref.png")
    assert m["spec"]["style_reference"] == "ref/ref.png"

    # 3) nhập idle từ Spine (PNG có alpha)
    d = tmp / "spine_idle"
    synth.write_rgba_frames(d, 6)
    for i, f in enumerate(sorted(d.glob("*.png"))):
        c.call("POST", f"/api/monsters/{mid}/anims/idle/frames?name={f.name}&first={1 if i == 0 else 0}", raw=f.read_bytes())
    m = c.call("GET", f"/api/monsters/{mid}")
    src = m["anims"][0]["source"]
    assert src["origin"] == "spine" and src["count"] == 6 and src["preview"]
    assert isinstance(c.call("GET", src["preview"]), bytes)

    # 4) sinh 2 biến thể attack bằng AI
    job = c.call("POST", f"/api/monsters/{mid}/anims/attack/generate",
                 {"template": "wan22_5b_i2v.template.json", "motion": "lunges and bites", "count": 2, "sets": {"NUM_FRAMES": "13"}})
    assert job["lane"] == "gpu"
    jobs = c.wait(mid)
    assert jobs[0]["status"] == "done", jobs[0]
    assert fc.state.prompts[-1]["5"]["inputs"]["text"].startswith("lunges and bites, 2D game monster")
    m = c.call("GET", f"/api/monsters/{mid}")
    vs = m["anims"][1]["variants"]
    assert [v["seed"] for v in vs] == [1001, 1002]
    assert all(v["qa"] in ("pass", "warn") and v["preview"] for v in vs)

    # 5) chọn biến thể, hậu xử lý
    c.call("POST", f"/api/monsters/{mid}/anims/attack/choose", {"seed": 1002})
    m = c.call("GET", f"/api/monsters/{mid}")
    assert m["anims"][1]["source"]["origin"] == "ai" and m["anims"][1]["source"]["seed"] == 1002
    assert [v["in_use"] for v in m["anims"][1]["variants"]] == [False, True]
    c.call("POST", f"/api/monsters/{mid}/process", {})
    jobs = c.wait(mid)
    assert jobs[0]["status"] == "done", jobs[0]
    m = c.call("GET", f"/api/monsters/{mid}")
    res = m["result"]
    assert res["status"] in ("pass", "warn") and res["stale"] == []
    assert isinstance(c.call("GET", res["contact_sheet"]), bytes)
    assert set(res["gifs"]) == {"idle", "attack"}

    # 6) duyệt & xuất
    ap = c.call("POST", f"/api/monsters/{mid}/approve", {"note": "ok"})
    assert ap["approved_by"] == "Hoạ sĩ A" and ap["qa_status"] == res["status"]
    exp = tmp / "ws" / "export" / mid
    for f in (f"{mid}.png", f"{mid}.fbf.json", "approval.json", "contact_sheet.png", "qa_report.md"):
        assert (exp / f).exists()
    lst = c.call("GET", "/api/monsters")
    assert lst[0]["approved"] and lst[0]["ready"] == 2

    # 7) sửa spec → kết quả cũ → không duyệt lại được tới khi chạy lại
    spec = m["spec"]
    spec["target_height_px"] = 150
    c.call("PUT", f"/api/monsters/{mid}/spec", {"spec": spec})
    m = c.call("GET", f"/api/monsters/{mid}")
    assert "spec đã đổi" in m["result"]["stale"]
    err = c.call("POST", f"/api/monsters/{mid}/approve", {}, expect=400)
    assert "đã cũ" in err["error"]


def test_create_from_preset_and_validation_errors(env):
    c, *_ = env
    c.call("POST", "/api/monsters", {"monster_id": "mob_moi", "preset": "mob_thuong"})
    m = c.call("GET", "/api/monsters/mob_moi")
    assert [a["name"] for a in m["anims"]] == ["idle", "run", "attack", "hurt", "die"]
    assert "Đã có quái" in c.call("POST", "/api/monsters", {"monster_id": "mob_moi", "preset": "mob_thuong"}, expect=400)["error"]
    assert "Mã quái" in c.call("POST", "/api/monsters", {"monster_id": "Mob X", "preset": "mob_thuong"}, expect=400)["error"]
    assert "Spine" in c.call("POST", "/api/monsters", {"monster_id": "mob_b", "spine_json": "{nope"}, expect=400)["error"]
    # sinh AI khi chưa có ảnh chuẩn
    e = c.call("POST", "/api/monsters/mob_moi/anims/idle/generate", {"template": "wan22_5b_i2v.template.json"}, expect=400)
    assert "ảnh chuẩn" in e["error"]
    # hậu xử lý khi thiếu khung
    c.call("POST", "/api/monsters/mob_moi/process", {})
    jobs = c.wait("mob_moi")
    assert jobs[0]["status"] == "error" and "Chưa có khung hình" in jobs[0]["error"]
    # spec sai bị từ chối
    bad = c.call("GET", "/api/monsters/mob_moi")["spec"]
    bad["animations"]["idle"]["fps"] = 0
    assert "fps" in c.call("PUT", "/api/monsters/mob_moi/spec", {"spec": bad}, expect=400)["error"]


def test_generation_error_is_reported(env):
    c, app, fc, _ = env
    fc.state.fail_exec = True
    c.call("POST", "/api/monsters", {"monster_id": "mob_e", "preset": "mob_thuong"})
    c.call("POST", "/api/monsters/mob_e/ref?name=r.png", raw=png_bytes(synth.draw_monster(0.0)))
    c.call("POST", "/api/monsters/mob_e/anims/idle/generate", {"template": "wan22_5b_i2v.template.json", "count": 1, "sets": {"NUM_FRAMES": "9"}})
    jobs = c.wait("mob_e")
    assert jobs[0]["status"] == "error" and "CUDA out of memory" in jobs[0]["error"]


def test_comfy_offline_does_not_break_app(env):
    c, app, *_ = env
    c.call("PUT", "/api/settings", {"comfy_url": "http://127.0.0.1:9"})
    st = c.call("GET", "/api/status?refresh=1")
    assert st["doctor"]["connected"] is False and "Không kết nối được" in st["doctor"]["error"]
    assert c.call("GET", "/api/templates")[0]["file"] == "wan22_5b_i2v.template.json"


def test_security_guards(env):
    c, *_ = env
    c.call("POST", "/api/monsters", {"monster_id": "mob_s", "preset": "mob_thuong"})
    # thiếu header chống gọi chéo
    req = request.Request(c.base + "/api/monsters", data=b"{}", method="POST", headers={"Content-Type": "application/json"})
    with pytest.raises(error.HTTPError) as ei:
        request.urlopen(req, timeout=10)
    assert ei.value.code == 403
    # Host lạ (DNS rebinding)
    c.call("GET", "/api/monsters", headers={"Host": "evil.example:7865"}, expect=403)
    # thoát khỏi thư mục quái
    c.call("GET", "/files/mob_s/../../settings.json", expect=400)
    c.call("GET", "/files/mob_s/..%2f..%2fsettings.json", expect=400)
    # tên file độc hại khi nhập khung
    r = c.call("POST", "/api/monsters/mob_s/anims/idle/frames?name=..%2F..%2Fevil.png&first=1", raw=b"x")
    assert r["count"] == 1
    _, app, _, tmp = env
    assert (tmp / "ws" / "monsters" / "mob_s" / "raw" / "idle" / "evil.png").exists()
    assert not (tmp / "ws" / "monsters" / "evil.png").exists()


def test_cancel_queued_job(env):
    c, app, fc, _ = env
    fc.state.never_finish = True
    c.call("POST", "/api/monsters", {"monster_id": "mob_c", "preset": "mob_thuong"})
    c.call("POST", "/api/monsters/mob_c/ref?name=r.png", raw=png_bytes(synth.draw_monster(0.0)))
    j1 = c.call("POST", "/api/monsters/mob_c/anims/idle/generate", {"template": "wan22_5b_i2v.template.json", "count": 1, "sets": {"NUM_FRAMES": "9"}})
    j2 = c.call("POST", "/api/monsters/mob_c/anims/run/generate", {"template": "wan22_5b_i2v.template.json", "count": 1, "sets": {"NUM_FRAMES": "9"}})
    assert c.call("POST", f"/api/jobs/{j2['id']}/cancel", {})["status"] == "cancelled"
    time.sleep(0.3)
    r = c.call("POST", f"/api/jobs/{j1['id']}/cancel", {})
    assert r.get("cancel_requested")
    jobs = c.wait("mob_c")
    assert {j["status"] for j in jobs} == {"cancelled"}
    assert fc.state.interrupted


def test_static_ui_served(env):
    c, *_ = env
    html = c.call("GET", "/")
    assert b"X\xc6\xb0\xe1\xbb\x9fng Qu\xc3\xa1i" in html  # "Xưởng Quái"
    assert isinstance(c.call("GET", "/static/app.js"), bytes)
    c.call("GET", "/static/../core.py", expect=404)


def test_live_progress_and_preview_during_generation(env):
    c, app, fc, _ = env
    fc.state.steps, fc.state.step_delay = 12, 0.08
    c.call("POST", "/api/monsters", {"monster_id": "mob_live", "preset": "mob_thuong"})
    c.call("POST", "/api/monsters/mob_live/ref?name=r.png", raw=png_bytes(synth.draw_monster(0.0)))
    job = c.call("POST", "/api/monsters/mob_live/anims/idle/generate",
                 {"template": "wan22_5b_i2v.template.json", "count": 2, "sets": {"NUM_FRAMES": "9"}})
    seen = None
    t0 = time.time()
    while time.time() - t0 < 30:  # bắt được lúc đang ở bước sinh khung
        j = next(x for x in c.call("GET", "/api/jobs?monster=mob_live") if x["id"] == job["id"])
        p = j.get("progress") or {}
        if j["status"] == "running" and p.get("steps") and p.get("step", 0) >= 7:
            seen = j
            break
        time.sleep(0.03)
    assert seen, "không bắt được tiến độ giữa chừng"
    p = seen["progress"]
    assert p["phase"] == "Sinh khung" and p["steps"] == 12 and p["variant"] == 1 and p["variants"] == 2
    assert p["node"]["class"] == "KSampler" and p["total_nodes"] == 11
    assert seen["has_preview"] and "_preview" not in seen
    img = c.call("GET", f"/api/jobs/{seen['id']}/preview?v={seen['preview_v']}")
    assert isinstance(img, bytes) and img.startswith(b"\x89PNG")
    jobs = c.wait("mob_live")
    final = next(x for x in jobs if x["id"] == job["id"])
    assert final["status"] == "done" and final["progress"]["variant"] == 2 and final["progress"]["phase"] == "Xong"
    c.call("GET", "/api/jobs/000000000000/preview", expect=400)
