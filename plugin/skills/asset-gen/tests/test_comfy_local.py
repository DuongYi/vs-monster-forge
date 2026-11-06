# -*- coding: utf-8 -*-
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
sys.path.insert(0, str(SKILL / "scripts"))
sys.path.insert(0, str(HERE))

import comfy_local as cl  # noqa: E402
import postprocess as pp  # noqa: E402
import synth  # noqa: E402
from fake_comfy import FakeComfy  # noqa: E402

TEMPLATE = SKILL / "workflows" / "wan22_5b_i2v.template.json"
REGISTRY = cl.load_registry(None)


def ref_image(tmp_path) -> Path:
    p = tmp_path / "mob_idle.png"
    Image.fromarray(synth.draw_monster(0.0)).save(p)
    return p


def exported_workflow() -> dict:
    """Giả lập file người dùng Export (API) từ ComfyUI: giá trị thật, không có chỗ giữ chỗ."""
    wf, meta, _ = cl.load_template(TEMPLATE)
    return cl.fill(wf, meta["defaults"])


# ── templatize ──

def test_templatize_finds_all_slots():
    t, defaults, log = cl.templatize(exported_workflow())
    assert set(defaults) == {"SEED", "STEPS", "NUM_FRAMES", "WIDTH", "HEIGHT", "INPUT_IMAGE", "PREFIX", "PROMPT", "NEGATIVE"}
    assert t["9"]["inputs"]["seed"] == "__SEED__" and defaults["SEED"] == 1001
    assert t["5"]["inputs"]["text"] == "__PROMPT__" and t["6"]["inputs"]["text"] == "__NEGATIVE__"
    assert t["8"]["inputs"]["length"] == "__NUM_FRAMES__" and defaults["NUM_FRAMES"] == 49
    assert t["1"]["inputs"]["unet_name"] == "wan2.2_ti2v_5B_fp16.safetensors"  # mô hình giữ nguyên
    assert not any("!" in line for line in log)


def test_templatize_traces_prompt_through_conditioning_node():
    wf = exported_workflow()
    # chèn node trung gian kiểu WanImageToVideo: nhận positive/negative rồi trả ra cho sampler
    wf["20"] = {"class_type": "WanImageToVideo", "inputs": {"positive": ["5", 0], "negative": ["6", 0], "vae": ["3", 0]}}
    wf["9"]["inputs"]["positive"] = ["20", 0]
    wf["9"]["inputs"]["negative"] = ["20", 1]
    t, defaults, _ = cl.templatize(wf)
    assert t["5"]["inputs"]["text"] == "__PROMPT__"
    assert t["6"]["inputs"]["text"] == "__NEGATIVE__"


def test_templatize_second_image_for_last_frame():
    wf = exported_workflow()
    wf["21"] = {"class_type": "LoadImage", "inputs": {"image": "end.png"}}
    t, defaults, _ = cl.templatize(wf)
    assert t["21"]["inputs"]["image"] == "__INPUT_IMAGE_2__" and defaults["INPUT_IMAGE_2"] == "end.png"


def test_templatize_rejects_ui_format():
    with pytest.raises(cl.ComfyError, match="Export \\(API\\)"):
        cl.templatize({"nodes": [], "links": []})


def test_templatize_cli_roundtrip(tmp_path):
    src = tmp_path / "exported.json"
    src.write_text(json.dumps(exported_workflow()), encoding="utf-8")
    out = tmp_path / "x.template.json"
    assert cl.main(["templatize", "--workflow", str(src), "--out", str(out), "--name", "x",
                    "--rule", "NUM_FRAMES=4n+1", "--min-vram", "8"]) == 0
    wf, meta, _ = cl.load_template(out)
    assert meta["status"] == "untested" and meta["rules"] == {"NUM_FRAMES": "4n+1"}
    assert "wan2.2_vae.safetensors" in meta["required_models"]
    assert len(meta["source"]["sha256"]) == 64


# ── validate ──

def test_validate_ok_on_matching_comfy():
    with FakeComfy() as fc:
        wf, _, _ = cl.load_template(TEMPLATE)
        issues = cl.validate(wf, cl.Comfy(fc.url).get("/object_info"), REGISTRY)
        assert [i for i in issues if i["level"] == "error"] == []


def test_validate_reports_missing_node_model_and_license():
    with FakeComfy(unet=("other.safetensors",)) as fc:
        wf, _, _ = cl.load_template(TEMPLATE)
        wf = json.loads(json.dumps(wf))
        wf["30"] = {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": "x.gguf"}}
        wf["31"] = {"class_type": "UNETLoader", "inputs": {"unet_name": "flux2_dev_fp8.safetensors", "weight_dtype": "default"}}
        issues = cl.validate(wf, cl.Comfy(fc.url).get("/object_info"), REGISTRY)
        msgs = " | ".join(i["msg"] for i in issues if i["level"] == "error")
        assert "Thiếu node 'UnetLoaderGGUF'" in msgs
        assert "Chưa có file mô hình 'wan2.2_ti2v_5B_fp16.safetensors'" in msgs
        assert "license_status='avoid'" in msgs          # flux2 dev bị chặn
        assert "x.gguf' chưa có trong" in msgs            # file lạ bị chặn


def test_validate_unlisted_can_be_warning():
    wf = {"1": {"class_type": "UNETLoader", "inputs": {"unet_name": "mystery.safetensors", "weight_dtype": "default"}}}
    oi = {"UNETLoader": {"input": {"required": {"unet_name": [["mystery.safetensors"]], "weight_dtype": [["default"]]}}}}
    assert cl.validate(wf, oi, REGISTRY)[0]["level"] == "error"
    assert cl.validate(wf, oi, REGISTRY, allow_unlisted=True)[0]["level"] == "warn"


# ── doctor ──

def test_doctor_reports_machine_and_ready_templates():
    with FakeComfy(vram_gb=16, ram_gb=24) as fc:
        rep = cl.doctor(cl.Comfy(fc.url), SKILL / "workflows", REGISTRY)
    assert rep["gpu"].endswith("RTX 4060 Ti") and rep["vram_gb"] == 16.0
    assert "14B" in rep["recommendation"]
    assert any("RAM 24 GB" in n for n in rep["notes"])
    assert "wan2.2_vae.safetensors" in rep["models"]["vae"]
    tpl = next(t for t in rep["templates"] if t["file"] == TEMPLATE.name)
    assert tpl["ready"] and tpl["missing_models"] == []


def test_doctor_low_vram_and_missing_models():
    with FakeComfy(vram_gb=6, unet=()) as fc:
        rep = cl.doctor(cl.Comfy(fc.url), SKILL / "workflows", REGISTRY)
    assert "Spine" in rep["recommendation"]
    tpl = next(t for t in rep["templates"] if t["file"] == TEMPLATE.name)
    assert not tpl["ready"]
    assert "wan2.2_ti2v_5B_fp16.safetensors" in tpl["missing_models"]
    assert any("8 GB VRAM" in p for p in tpl["problems"])


# ── run ──

def test_run_then_postprocess_end_to_end(tmp_path):
    raw = tmp_path / "raw" / "mob_x"
    with FakeComfy() as fc:
        res = cl.run_template(cl.Comfy(fc.url), TEMPLATE, {"NUM_FRAMES": "13", "PROMPT": "idle breathing"},
                              raw / "idle", input_images=[ref_image(tmp_path)], poll=0.01, log=lambda *a: None)
        sent = fc.state.prompts[-1]
    assert sent["5"]["inputs"]["text"] == "idle breathing"
    assert sent["8"]["inputs"]["length"] == 13 and isinstance(sent["8"]["inputs"]["length"], int)
    assert sent["7"]["inputs"]["image"].startswith("assetgen_")
    assert res[0]["files"] == 13                                   # ảnh "temp" bị bỏ qua
    prov = json.loads((raw / "idle" / "provenance.json").read_text("utf-8"))
    assert prov["seed"] == 1001 and prov["template"] == "wan22_5b_i2v" and len(prov["template_sha256"]) == 64
    assert prov["comfy"]["gpu"].endswith("RTX 4060 Ti") and prov["params"]["PROMPT"] == "idle breathing"
    assert "wan2.2_ti2v_5B_fp16.safetensors" in prov["models"]

    spec = {"monster_id": "mob_x", "target_height_px": 160,
            "animations": {"idle": {"frames": 6, "fps": 10, "loop": True}}}
    out = pp.build(spec, raw, tmp_path / "out", pp.load_thresholds(None), tmp_path)
    assert out["status"] in ("pass", "warn")
    assert out["manifest"]["animations"][0]["provenance"]["seed"] == 1001


def test_run_multiple_seeds_into_variant_folders(tmp_path):
    with FakeComfy() as fc:
        res = cl.run_template(cl.Comfy(fc.url), TEMPLATE, {"NUM_FRAMES": "5", "SEED": "40"}, tmp_path / "attack",
                              seeds=3, input_images=[ref_image(tmp_path)], poll=0.01, log=lambda *a: None)
    assert [r["seed"] for r in res] == [40, 41, 42]
    for s in (40, 41, 42):
        d = tmp_path / "attack" / f"seed_{s}"
        assert len(list(d.glob("*.png"))) == 5
        assert json.loads((d / "provenance.json").read_text("utf-8"))["seed"] == s


def test_run_rejects_bad_frame_count_and_too_many_runs(tmp_path):
    with pytest.raises(cl.ComfyError, match="4n\\+1"):
        cl.run_template(cl.Comfy("http://127.0.0.1:9"), TEMPLATE, {"NUM_FRAMES": "50"}, tmp_path / "x", log=lambda *a: None)
    with pytest.raises(cl.ComfyError, match="max-runs"):
        cl.run_template(cl.Comfy("http://127.0.0.1:9"), TEMPLATE, {}, tmp_path / "x", seeds=10, log=lambda *a: None)


def test_run_reports_execution_error(tmp_path):
    with FakeComfy(fail_exec=True) as fc:
        with pytest.raises(cl.ComfyError, match="CUDA out of memory"):
            cl.run_template(cl.Comfy(fc.url), TEMPLATE, {"NUM_FRAMES": "5"}, tmp_path / "x",
                            input_images=[ref_image(tmp_path)], poll=0.01, log=lambda *a: None)


def test_run_timeout_interrupts_job(tmp_path):
    with FakeComfy(never_finish=True) as fc:
        with pytest.raises(cl.ComfyError, match="Quá"):
            cl.run_template(cl.Comfy(fc.url), TEMPLATE, {"NUM_FRAMES": "5"}, tmp_path / "x",
                            input_images=[ref_image(tmp_path)], poll=0.01, timeout=0.2, log=lambda *a: None)
        assert fc.state.interrupted and fc.state.deleted


def test_run_refuses_unvalidated_template(tmp_path):
    with FakeComfy(vae=()) as fc:
        with pytest.raises(cl.ComfyError, match="wan2.2_vae.safetensors"):
            cl.run_template(cl.Comfy(fc.url), TEMPLATE, {"NUM_FRAMES": "5"}, tmp_path / "x",
                            input_images=[ref_image(tmp_path)], poll=0.01, log=lambda *a: None)


def test_run_protects_existing_output(tmp_path):
    dest = tmp_path / "idle"
    dest.mkdir()
    (dest / "old.png").write_bytes(b"x")
    with FakeComfy() as fc:
        with pytest.raises(cl.ComfyError, match="--overwrite"):
            cl.run_template(cl.Comfy(fc.url), TEMPLATE, {"NUM_FRAMES": "5"}, dest,
                            input_images=[ref_image(tmp_path)], poll=0.01, log=lambda *a: None)
        cl.run_template(cl.Comfy(fc.url), TEMPLATE, {"NUM_FRAMES": "5"}, dest, overwrite=True,
                        input_images=[ref_image(tmp_path)], poll=0.01, log=lambda *a: None)
    assert not (dest / "old.png").exists()


def test_dry_run_needs_no_server(tmp_path, capsys):
    code = cl.main(["--url", "http://127.0.0.1:9", "run", "--template", str(TEMPLATE), "--dest", str(tmp_path / "x"),
                    "--set", "NUM_FRAMES=17", "--dry-run"])
    assert code == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["8"]["inputs"]["length"] == 17


def test_connection_error_is_friendly(capsys):
    assert cl.main(["--url", "http://127.0.0.1:9", "doctor"]) == 1
    assert "Không kết nối được ComfyUI" in capsys.readouterr().err


def test_combo_options_both_formats():
    assert cl.combo_options([["a", "b"]]) == ["a", "b"]
    assert cl.combo_options(["COMBO", {"options": ["x"]}]) == ["x"]
    assert cl.combo_options(["INT", {}]) is None
