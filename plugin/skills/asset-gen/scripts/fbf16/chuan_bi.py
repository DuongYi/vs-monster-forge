#!/usr/bin/env python3
"""chuan_bi.py – dựng thư mục làm việc cho một quái định dạng hoạ sĩ 16 khung.

Ảnh quái thật KHÔNG nằm trong repo (repo công khai). Script này lấy ảnh từ repo client
(hoặc bất kỳ thư mục nào) và dựng vào workspace/fbf16/<mã>/ (đã nằm trong .gitignore):
  goc.png                     khung 1 gốc 500×500 trong suốt
  goc-nen-trang-768.png       khung 1 lót nền trắng, phóng 768 – đầu vào AI
  dang-mau-768/2..16.png      dáng mẫu lót nền trắng 768, lấy từ bộ hoạ sĩ cùng khuôn
  thu-vien-dang/<tên>/        bản chép các bộ hoạ sĩ dùng làm dáng mẫu / đáp án
  mo_ta.json                  mô tả nhân vật để điền (chỉ tạo nếu chưa có)

Dùng:
  python scripts/fbf16/chuan_bi.py --ma u_minh_ho_phap \
      --goc "<FE>/Monster/58 U Minh Ho Phap/1.png" \
      --dang "<FE>/Monster/60 Vu Ho Phap" --thay 5="<FE>/Monster/59 Xich Ho Phap" \
      --dap-an "<FE>/Monster/58 U Minh Ho Phap"
  (<FE> = fights-break-sphere-client/Assets)
  --thay N=<thư mục>: lấy dáng khung N từ bộ khác (vd 60 Vụ có 4.png trùng 5.png nên khung 5 lấy từ 59).
  --dap-an: bộ hoạ sĩ của chính con đang sinh, nếu có – chỉ để so, không đưa cho AI.
"""
import argparse
import json
import shutil
from pathlib import Path

from PIL import Image

SKILL = Path(__file__).resolve().parents[2]
WS = SKILL.parents[2] / "workspace" / "fbf16"


def nen_trang(src, dst, size=768):
    im = Image.open(src).convert("RGBA")
    nen = Image.new("RGB", im.size, "white")
    nen.paste(im, mask=im.split()[3])
    nen.resize((size, size), Image.LANCZOS).save(dst)


def chep_bo(src, dst):
    dst.mkdir(parents=True, exist_ok=True)
    for p in sorted(Path(src).glob("*.png")):
        shutil.copy(p, dst / p.name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ma", required=True, help="mã quái: chữ thường, số, _")
    ap.add_argument("--goc", required=True, help="khung 1 (PNG trong suốt, quay phải)")
    ap.add_argument("--dang", required=True, help="thư mục bộ hoạ sĩ cùng khuôn làm dáng mẫu (1..16.png)")
    ap.add_argument("--thay", action="append", default=[], help="N=<thư mục>: lấy dáng khung N từ bộ khác")
    ap.add_argument("--dap-an", help="bộ hoạ sĩ của chính quái này (chỉ để so)")
    ap.add_argument("--size", type=int, default=768)
    a = ap.parse_args()

    out = WS / a.ma
    (out / "dang-mau-768").mkdir(parents=True, exist_ok=True)
    shutil.copy(a.goc, out / "goc.png")
    nen_trang(a.goc, out / "goc-nen-trang-768.png", a.size)

    thay = {int(k): Path(v) for k, v in (x.split("=", 1) for x in a.thay)}
    bo = {Path(a.dang)} | set(thay.values())
    for f in range(2, 17):
        nen_trang(thay.get(f, Path(a.dang)) / f"{f}.png", out / "dang-mau-768" / f"{f}.png", a.size)
    for b in bo:
        chep_bo(b, out / "thu-vien-dang" / b.name.replace(" ", "_"))
    if a.dap_an:
        chep_bo(a.dap_an, out / "dap-an")

    mt = out / "mo_ta.json"
    if not mt.exists():
        mau = json.loads((SKILL / "config" / "fbf16" / "dang_16_khung.json").read_text(encoding="utf-8"))["mo_ta_mau"]
        mt.write_text(json.dumps(mau, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Điền mô tả nhân vật vào {mt} trước khi sinh.")
    print(f"Xong: {out}")


if __name__ == "__main__":
    main()
