#!/usr/bin/env python3
"""gen_qwen.py – sinh khung 2-16 định dạng hoạ sĩ bằng Qwen-Image-Edit-2511 (diffusers), máy NVIDIA.
*** CHƯA CHẠY THỬ: viết theo model card, chưa có GPU NVIDIA để kiểm (10/11/2026). ***
Mô hình qwen-image-edit trong workflows/registry.json đang 'check': phải được duyệt sang 'ok' trước khi dùng thật.

Cài: pip install torch (bản CUDA) "diffusers>=0.36" transformers accelerate
Bản bf16 cần ~40 GB VRAM; card 24 GB thêm --offload (cần RAM ≥ 64 GB, chậm) hoặc đi đường ComfyUI fp8.

Trước đó chạy chuan_bi.py để có workspace/fbf16/<mã>/ và điền mo_ta.json.
  python scripts/fbf16/gen_qwen.py <mã> --che-do chu|dang|xuong [--khung all|2,9] [--seed 1,2,3,4]
      [--xuong-dir <thư mục 2..16.png khung xương>] [--steps 40] [--cfg 4.0] [--size 1024] [--offload]
Chế độ:
  chu   = ảnh gốc + mô tả dáng bằng chữ (cách đã chạy với FLUX.2 klein 4B, để so ngang).
  dang  = ảnh gốc + ảnh dáng mẫu của hoạ sĩ (dang-mau-768/). FLUX 4B bỏ qua cách này; Qwen cần thử.
  xuong = ảnh gốc + ảnh khung xương OpenPose/DWPose của dáng mẫu (Qwen 2509+ hỗ trợ sẵn).
Ra: workspace/fbf16/<mã>/ai/<chế độ>/<khung>_s<seed>.png  → ai_to_fbf16.py đọc được ngay.
"""
import argparse
import json
import time
from pathlib import Path

from PIL import Image

SKILL = Path(__file__).resolve().parents[2]
WS = SKILL.parents[2] / "workspace" / "fbf16"
CFG = json.loads((SKILL / "config" / "fbf16" / "dang_16_khung.json").read_text(encoding="utf-8"))


def prompt(che_do, f, mo_ta):
    thay = lambda s: s.replace("{MO_TA}", mo_ta["MO_TA"]).replace("{VU_KHI}", mo_ta["VU_KHI"]).replace("{MAU}", mo_ta["MAU"])
    dang = CFG["dang"][str(f)]
    if che_do == "chu":
        return thay(CFG["giu_nhan_vat"] + " " + dang)
    dau = CFG["mo_dau_dang"] if che_do == "dang" else CFG["mo_dau_xuong"]
    return thay(dau + " " + CFG["giu_nhan_vat_2_anh"] + " " + dang)


def vuong(path, size):
    im = Image.open(path)
    if im.mode in ("RGBA", "LA"):
        nen = Image.new("RGB", im.size, "white")
        nen.paste(im, mask=im.convert("RGBA").split()[3])
        im = nen
    return im.convert("RGB").resize((size, size), Image.LANCZOS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ma")
    ap.add_argument("--che-do", choices=["chu", "dang", "xuong"], required=True)
    ap.add_argument("--khung", default="all")
    ap.add_argument("--seed", default="1,2,3,4")
    ap.add_argument("--xuong-dir")
    ap.add_argument("--steps", type=int, default=40)
    ap.add_argument("--cfg", type=float, default=4.0)
    ap.add_argument("--size", type=int, default=1024)
    ap.add_argument("--offload", action="store_true")
    ap.add_argument("--model", default="Qwen/Qwen-Image-Edit-2511")
    a = ap.parse_args()

    qd = WS / a.ma
    mo_ta = json.loads((qd / "mo_ta.json").read_text(encoding="utf-8"))
    out = qd / "ai" / a.che_do
    out.mkdir(parents=True, exist_ok=True)
    khung = list(range(2, 17)) if a.khung == "all" else [int(x) for x in a.khung.split(",")]
    seeds = [int(x) for x in a.seed.split(",")]
    if a.che_do == "xuong" and not a.xuong_dir:
        ap.error("chế độ xuong cần --xuong-dir")

    import torch
    from diffusers import QwenImageEditPlusPipeline

    t0 = time.time()
    pipe = QwenImageEditPlusPipeline.from_pretrained(a.model, torch_dtype=torch.bfloat16)
    pipe.enable_model_cpu_offload() if a.offload else pipe.to("cuda")
    pipe.set_progress_bar_config(disable=True)
    print(f"nạp mô hình {time.time() - t0:.0f}s", flush=True)

    goc = vuong(qd / "goc-nen-trang-768.png", a.size)
    log = []
    for f in khung:
        anh = [goc]
        if a.che_do == "dang":
            anh.append(vuong(qd / "dang-mau-768" / f"{f}.png", a.size))
        elif a.che_do == "xuong":
            anh.append(vuong(Path(a.xuong_dir) / f"{f}.png", a.size))
        p = prompt(a.che_do, f, mo_ta)
        for s in seeds:
            t = time.time()
            img = pipe(image=anh, prompt=p, negative_prompt=" ", true_cfg_scale=a.cfg, guidance_scale=1.0,
                       num_inference_steps=a.steps, generator=torch.Generator("cuda").manual_seed(s),
                       num_images_per_prompt=1).images[0]
            img.save(out / f"{f}_s{s}.png")
            dt = time.time() - t
            log.append({"khung": f, "seed": s, "giay": round(dt, 1), "che_do": a.che_do, "model": a.model,
                        "steps": a.steps, "cfg": a.cfg, "prompt": p})
            print(f"khung {f} seed {s}: {dt:.1f}s", flush=True)
    (out / f"log_{int(time.time())}.json").write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
