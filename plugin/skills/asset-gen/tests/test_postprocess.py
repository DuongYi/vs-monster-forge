# -*- coding: utf-8 -*-
import json
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
sys.path.insert(0, str(HERE))

import postprocess as pp  # noqa: E402
import synth  # noqa: E402

TH = pp.load_thresholds(None)


def base_spec(**anims):
    return {
        "monster_id": "mob_test",
        "target_height_px": 160,
        "anchor": {"x": "feet", "y": "ground"},
        "padding_px": 4,
        "background": {"mode": "auto"},
        "animations": anims or {"idle": {"frames": 6, "fps": 10, "loop": True}},
    }


def run(tmp_path, spec, setup):
    raw = tmp_path / "raw"
    raw.mkdir()
    setup(raw)
    out = tmp_path / "out"
    return pp.build(spec, raw, out, TH, tmp_path), out


def check(res, scope, name):
    hits = [c for c in res["checks"] if c["scope"] == scope and c["check"] == name]
    assert hits, f"không có check {scope}.{name}"
    return hits[0]


def load_cells(out, man, anim):
    atlas = pp.load_still(out / man["atlas"]["file"])
    an = next(a for a in man["animations"] if a["name"] == anim)
    cells = []
    for f in an["frames"]:
        r = f["rect"]
        cells.append(atlas[r["y"]:r["y"] + r["h"], r["x"]:r["x"] + r["w"]])
    return cells


def test_background_removed_and_clean_edges(tmp_path):
    res, out = run(tmp_path, base_spec(), lambda raw: synth.write_frames(raw / "idle", 6))
    man = res["manifest"]
    assert res["status"] in ("pass", "warn")
    cells = load_cells(out, man, "idle")
    for c in cells:
        assert c[0, 0, 3] == 0 and c[-1, -1, 3] == 0
        edge = (c[..., 3] > 0.05) & (c[..., 3] < 0.95)
        g_excess = c[..., 1] - np.maximum(c[..., 0], c[..., 2])
        assert float(g_excess[edge].mean()) < 0.05, "mép còn ám xanh"
    assert check(res, "idle", "halo")["status"] == "pass"
    assert check(res, "idle", "key_residue")["status"] == "pass"
    assert (out / "preview" / "contact_sheet.png").exists()
    assert (out / "preview" / "idle.gif").exists()


def test_jitter_removed_when_locked(tmp_path):
    res, out = run(tmp_path, base_spec(), lambda raw: synth.write_frames(raw / "idle", 6, jitter=12, seed=3))
    cells = load_cells(out, res["manifest"], "idle")
    ms = [pp.measure_alpha(c[..., 3]) for c in cells]
    fx = [m.feet_x for m in ms]
    gy = [m.ground_y for m in ms]
    assert max(fx) - min(fx) <= 1.0
    assert max(gy) - min(gy) <= 1.0
    assert check(res, "idle", "anchor_jitter_px")["status"] == "pass"


def test_target_height_and_pivot(tmp_path):
    res, out = run(tmp_path, base_spec(), lambda raw: synth.write_frames(raw / "idle", 6))
    man = res["manifest"]
    cells = load_cells(out, man, "idle")
    hs = [pp.measure_alpha(c[..., 3]).height for c in cells]
    assert abs(float(np.median(hs)) - 160) <= 2
    m0 = pp.measure_alpha(cells[0][..., 3])
    assert abs(m0.ground_y - man["pivot"]["y_px"]) <= 1.0
    assert abs(m0.feet_x - man["pivot"]["x_px"]) <= 1.0
    assert 0 < man["pivot"]["y_norm"] < 0.2  # neo ở chân → gần đáy ô (Unity gốc dưới)


def test_none_mode_keeps_lunge(tmp_path):
    spec = base_spec(idle={"frames": 4, "fps": 10, "loop": True},
                     attack={"frames": 6, "fps": 12, "loop": False, "stabilize": "none"})

    def setup(raw):
        synth.write_frames(raw / "idle", 4)
        synth.write_frames(raw / "attack", 6, lunge_px=12, size=(640, 512))

    res, out = run(tmp_path, spec, setup)
    cells = load_cells(out, res["manifest"], "attack")
    fx = [pp.measure_alpha(c[..., 3]).feet_x for c in cells]
    assert all(b > a for a, b in zip(fx, fx[1:])), "chuyển động lao tới phải được giữ"
    s = res["manifest"]["animations"][1]["scale"]
    assert abs((fx[-1] - fx[0]) - 60 * s) <= 2.0


def test_global_scale_flags_mismatched_source(tmp_path):
    spec = base_spec(idle={"frames": 4, "fps": 10, "loop": True},
                     hurt={"frames": 3, "fps": 12, "loop": False})

    def setup(raw):
        synth.write_frames(raw / "idle", 4)
        synth.write_frames(raw / "hurt", 3, scale=0.6)  # sinh khác cỡ

    res, _ = run(tmp_path, spec, setup)
    assert check(res, "mob_test", "scale_spread")["status"] in ("warn", "fail")

    spec["scale_mode"] = "first_frame"
    (tmp_path / "b").mkdir()
    res2, out2 = run(tmp_path / "b", spec, setup)
    assert check(res2, "mob_test", "scale_spread")["status"] == "pass"
    h_idle = pp.measure_alpha(load_cells(out2, res2["manifest"], "idle")[0][..., 3]).height
    h_hurt = pp.measure_alpha(load_cells(out2, res2["manifest"], "hurt")[0][..., 3]).height
    assert abs(h_idle - h_hurt) <= 2


def test_frame_count_mismatch_fails(tmp_path):
    res, _ = run(tmp_path, base_spec(), lambda raw: synth.write_frames(raw / "idle", 5))
    assert check(res, "idle", "frame_count")["status"] == "fail"
    assert res["status"] == "fail"


def test_color_drift_detected(tmp_path):
    res, _ = run(tmp_path, base_spec(), lambda raw: synth.write_frames(raw / "idle", 6, swap_at=3))
    c = check(res, "idle", "color_drift")
    assert c["status"] in ("warn", "fail")
    assert "khung 3" in c["msg"]


def test_loop_seam(tmp_path):
    good, _ = run(tmp_path, base_spec(), lambda raw: synth.write_frames(raw / "idle", 6))
    assert check(good, "idle", "loop_seam")["status"] == "pass"
    (tmp_path / "b").mkdir()
    # anim dừng ở nửa chu kỳ rồi "giật" về khung đầu
    bad, _ = run(tmp_path / "b", base_spec(), lambda raw: synth.write_frames(raw / "idle", 6, period=12))
    assert check(bad, "idle", "loop_seam")["status"] == "fail"
    (tmp_path / "c").mkdir()
    sweep, _ = run(tmp_path / "c", base_spec(), lambda raw: synth.write_frames(raw / "idle", 6, tail_sweep=True))
    assert check(sweep, "idle", "loop_seam")["status"] in ("warn", "fail")


def test_timing_and_events(tmp_path):
    spec = base_spec(attack={"frames": 6, "fps": 12, "loop": False, "target_duration_ms": 800,
                             "events": {"hit": 3}, "target_events_ms": {"hit": 400}})
    res, _ = run(tmp_path, spec, lambda raw: synth.write_frames(raw / "attack", 6))
    assert check(res, "attack", "duration")["status"] == "fail"      # 500ms vs 800ms
    assert check(res, "attack", "event:hit")["status"] == "fail"     # 250ms vs 400ms
    ev = res["manifest"]["animations"][0]["events"][0]
    assert ev == {"name": "hit", "frame": 3, "time_ms": 250.0, "target_ms": 400.0}

    (tmp_path / "b").mkdir()
    spec["animations"]["attack"].update({"target_duration_ms": 520, "target_events_ms": {"hit": 260}})
    ok, _ = run(tmp_path / "b", spec, lambda raw: synth.write_frames(raw / "attack", 6))
    assert check(ok, "attack", "duration")["status"] == "pass"
    assert check(ok, "attack", "event:hit")["status"] == "pass"


def test_nonuniform_background_rejected(tmp_path):
    def setup(raw):
        d = raw / "idle"
        d.mkdir()
        from PIL import Image
        for i in range(6):
            img = synth.draw_monster(i)
            grad = np.linspace(0, 200, img.shape[1]).astype(np.uint8)
            img[:20, :, 0] = grad
            img[-20:, :, 2] = grad[::-1]
            Image.fromarray(img).save(d / f"{i:04d}.png")
    with pytest.raises(pp.InputError, match="không phẳng"):
        run(tmp_path, base_spec(), setup)


def test_rgba_input_uses_existing_alpha(tmp_path):
    res, out = run(tmp_path, base_spec(), lambda raw: synth.write_rgba_frames(raw / "idle", 6))
    an = res["manifest"]["animations"][0]
    assert an["background"]["mode"] == "alpha"
    assert res["status"] in ("pass", "warn")


def test_video_loop_window_detected(tmp_path):
    vid = tmp_path / "raw" / "idle.mp4"
    if not synth.write_video(vid, n=50, period=20):
        pytest.skip("môi trường không ghi được mp4")
    spec = base_spec(idle={"frames": 8, "fps": 10, "loop": True})
    res = pp.build(spec, tmp_path / "raw", tmp_path / "out", TH, tmp_path)
    sel = res["manifest"]["animations"][0]["selection"]
    assert sel["method"] == "auto_loop"
    assert abs(sel["window"][1] - 20) <= 1
    assert check(res, "idle", "loop_seam")["status"] == "pass"
    assert len(res["manifest"]["animations"][0]["frames"]) == 8


def test_pick_overrides_selection(tmp_path):
    spec = base_spec(idle={"frames": 3, "fps": 10, "loop": False, "pick": [0, 4, 9]})
    res, _ = run(tmp_path, spec, lambda raw: synth.write_frames(raw / "idle", 10))
    srcs = [f["source"]["index"] for f in res["manifest"]["animations"][0]["frames"]]
    assert srcs == [0, 4, 9]


def test_check_roundtrip_and_edit_detection(tmp_path):
    res, out = run(tmp_path, base_spec(), lambda raw: synth.write_frames(raw / "idle", 6))
    man_path = out / "mob_test.fbf.json"
    again = pp.check_manifest(man_path)
    assert again["status"] == res["status"]
    assert again["atlas_edited"] is False
    # hoạ sĩ sửa tay: xoá trắng khung 2 → QA phải bắt
    from PIL import Image
    atlas = np.array(Image.open(out / "mob_test.png"))
    r = res["manifest"]["animations"][0]["frames"][2]["rect"]
    atlas[r["y"]:r["y"] + r["h"], r["x"]:r["x"] + r["w"], 3] = 0
    Image.fromarray(atlas, "RGBA").save(out / "mob_test.png")
    edited = pp.check_manifest(man_path)
    assert edited["atlas_edited"] is True
    assert edited["status"] == "fail"


def test_provenance_carried(tmp_path):
    def setup(raw):
        synth.write_frames(raw / "idle", 6)
        (raw / "provenance.json").write_text(json.dumps({
            "workflow": "monster_anim_i2v.json", "model": "wan2.2-i2v-a14b", "lora": "dpvs_style_v1",
            "seed": 123456}), encoding="utf-8")
    res, _ = run(tmp_path, base_spec(), setup)
    prov = res["manifest"]["provenance"]
    assert prov["seed"] == 123456 and prov["lora"] == "dpvs_style_v1"
    assert len(prov["sources"]) == 6 and all(len(s["sha256"]) == 64 for s in prov["sources"])
    assert len(res["manifest"]["postprocess"]["spec_sha256"]) == 64


def test_invalid_spec_rejected():
    with pytest.raises(pp.InputError):
        pp.validate_spec({"monster_id": "Bad Id", "target_height_px": 0, "animations": {}})


def test_cli_exit_codes(tmp_path):
    synth.write_frames(tmp_path / "raw" / "idle", 5)  # thiếu 1 khung → fail
    spec_p = tmp_path / "spec.json"
    spec_p.write_text(json.dumps(base_spec()), encoding="utf-8")
    code = pp.main(["run", "--spec", str(spec_p), "--input", str(tmp_path / "raw"), "--out", str(tmp_path / "out")])
    assert code == 2
    code = pp.main(["run", "--spec", str(spec_p), "--input", str(tmp_path / "missing"), "--out", str(tmp_path / "o2")])
    assert code == 1
