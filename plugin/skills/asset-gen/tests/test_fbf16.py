# -*- coding: utf-8 -*-
"""Test công cụ định dạng hoạ sĩ 16 khung (scripts/fbf16) trên hình giả lập – không cần ảnh quái thật."""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
FBF16 = SKILL / "scripts" / "fbf16"
sys.path.insert(0, str(FBF16))

import chuan_bi  # noqa: E402
import gen_qwen  # noqa: E402
import qa16  # noqa: E402
import recolor_fx  # noqa: E402


def nhan_vat(chan=0, dx=0, size=500):
    """Hình người đơn giản quay phải; `chan` dịch hai chân ngược chiều nhau (mô phỏng bước)."""
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    cx = 250 + dx
    d.ellipse([cx - 40, 150, cx + 40, 230], fill=(90, 60, 140, 255))            # đầu
    d.rectangle([cx - 15, 185, cx + 10, 200], fill=(40, 220, 120, 255))           # mắt phát sáng
    d.polygon([(cx - 60, 230), (cx + 60, 230), (cx + 45, 420), (cx - 45, 420)], fill=(70, 40, 110, 255))
    d.line([(cx + 70, 180), (cx + 70, 430)], fill=(220, 210, 190, 255), width=10)  # gậy
    d.ellipse([cx + 55, 240, cx + 85, 280], fill=(40, 220, 120, 255))              # đèn
    for s in (-1, 1):
        x = cx + s * chan
        d.rectangle([x - 12, 420, x + 12, 499], fill=(50, 30, 80, 255))
    return im


def bo_16(d: Path, chan_di=(0, 30, 10, -30, -10, 30)):
    d.mkdir(parents=True, exist_ok=True)
    for i in range(1, 17):
        chan = chan_di[i - 3] if 3 <= i <= 8 else 0
        nhan_vat(chan=chan, dx=(i % 3)).save(d / f"{i}.png")
    return d


def test_kiem_chu_ky_di_dat_va_khong_dat(tmp_path):
    tot = bo_16(tmp_path / "tot")
    dung = bo_16(tmp_path / "dung", chan_di=(5, 5, 5, 5, 5, 5))
    py = [sys.executable, str(FBF16 / "kiem_chu_ky_di.py")]
    assert subprocess.run(py + [str(tot)], capture_output=True).returncode == 0
    r = subprocess.run(py + [str(dung)], capture_output=True, text=True)
    assert r.returncode == 2 and "KHÔNG ĐẠT" in r.stdout


def test_qa16_bat_khung_trung_va_cham_mep(tmp_path):
    d = bo_16(tmp_path / "bo")
    Image.open(d / "6.png").save(d / "7.png")                 # khung trùng
    nhan_vat(dx=230).save(d / "13.png")                        # lệch sát mép phải
    rows = qa16.run(d)
    nguong = json.loads((SKILL / "config" / "fbf16" / "nguong-16-khung.json").read_text())
    co = qa16.verdict(rows, nguong)
    assert "khung trùng" in co[7]
    assert "chạm mép" in co[13]


def test_recolor_giu_alpha_doi_sac(tmp_path):
    im = np.asarray(nhan_vat())
    ra = recolor_fx.recolor(im, hue=300.0)
    assert np.array_equal(ra[..., 3], im[..., 3])
    m = im[..., 3] > 0
    assert np.abs(ra[..., :3].astype(int) - im[..., :3].astype(int))[m].mean() > 5


def test_chuan_bi_dung_thu_muc(tmp_path, monkeypatch):
    goc = tmp_path / "goc.png"
    nhan_vat().save(goc)
    a, b = bo_16(tmp_path / "Bo A"), bo_16(tmp_path / "Bo B")
    monkeypatch.setattr(chuan_bi, "WS", tmp_path / "ws")
    monkeypatch.setattr(sys, "argv", ["chuan_bi.py", "--ma", "mob_test", "--goc", str(goc), "--dang", str(a),
                                      "--thay", f"5={b}", "--dap-an", str(a)])
    chuan_bi.main()
    q = tmp_path / "ws" / "mob_test"
    assert Image.open(q / "goc-nen-trang-768.png").size == (768, 768)
    assert Image.open(q / "goc-nen-trang-768.png").mode == "RGB"
    assert sorted(int(p.stem) for p in (q / "dang-mau-768").glob("*.png")) == list(range(2, 17))
    assert {p.name for p in (q / "thu-vien-dang").iterdir()} == {"Bo_A", "Bo_B"}
    assert (q / "dap-an" / "16.png").exists()
    mo_ta = json.loads((q / "mo_ta.json").read_text(encoding="utf-8"))
    assert {"MO_TA", "VU_KHI", "MAU"} <= set(mo_ta)


def test_prompt_qwen_thay_du_cho_trong():
    mo_ta = {"MO_TA": "same horned helmet", "VU_KHI": "spear", "MAU": "blue"}
    for che_do in ("chu", "dang", "xuong"):
        for f in range(2, 17):
            p = gen_qwen.prompt(che_do, f, mo_ta)
            assert "{" not in p and "same horned helmet" in p
            if che_do != "chu":
                assert "Picture 2" in p


def test_registry_ghi_nhan_fbf16():
    reg = json.loads((SKILL / "workflows" / "registry.json").read_text(encoding="utf-8"))
    m = {x["id"]: x for x in reg["models"]}
    assert m["dwpose"]["license_status"] == "check"
    assert m["qwen-image-edit"]["license_status"] == "check"      # chưa duyệt – không tự đổi sang ok
    assert any(w["id"] == "fbf16_qwen_edit" for w in reg["workflows"])
