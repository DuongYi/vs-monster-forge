# Định dạng hoạ sĩ 16 khung – sinh từ một ảnh

> Cập nhật 10/11/2026. Tri thức rút từ nghiên cứu ART-02. Báo cáo đầy đủ, kèm ảnh so sánh, nằm trong repo nội bộ `game-distribution`: `knowledge/docs/70-noi-dung/sinh-quai-fbf-tu-1-anh.md`.
> **Repo này công khai.** Ảnh quái thật (bộ hoạ sĩ, ảnh AI sinh từ quái thật) không vào git. `scripts/fbf16/chuan_bi.py` dựng chúng vào `workspace/fbf16/<mã>/`, thư mục đã nằm trong `.gitignore`.

## 1. Định dạng đích

Đây là định dạng hoạ sĩ đang giao cho game. Nó khác định dạng atlas `dpvs.fbf/1` của Xưởng Quái.

| Hạng mục | Giá trị (đo trên 3 bộ 58/59/60) |
|---|---|
| Khung | 16 PNG 500×500 RGBA, quay phải: `1-2` đứng · `3-8` đi · `9-11` chém · `12-14` thi pháp · `15-16` chết |
| Chân | Đáy y = 494–499 ở mọi khung, không chạm mép |
| Chiều cao so với khung 1 | đi 0,96–1,01 · chém 0,99–1,10 · thi pháp 0,88–1,14 · chết 0,32–0,88 |
| Trôi chân trong nhóm | đi ≤ 37 px · chém ≤ 87 px · chết ≤ 55 px. Có chủ đích; client tự tính pivot từng khung |
| Đạn | `a/b/c` 300×300. Ba bộ có **alpha trùng 100%**, chỉ khác màu |
| Khuôn dáng | Ba con dùng **chung một bộ dáng** (thân chibi người đứng), khác vũ khí, mũ, màu |

Đây là **dáng chốt** (pose-to-pose), phát 2–7 khung/giây, không phải video. Vì vậy:
- **Không lấy khung từ video** (Wan 2.2): chọn 3 khung trong 49 hiếm khi trúng đúng 3 cực điểm của đòn chém, và không ép được "quỳ rồi nằm".
- **Ngưỡng `config/qa_thresholds.json` không dùng được cho định dạng này.** Chạy trên chính bộ hoạ sĩ, nó báo 58 WARN, 59 và 60 FAIL (`walk.pop`, `scale_spread`). Dùng `scripts/fbf16/qa16.py` với `config/fbf16/nguong-16-khung.json`.

## 2. Công cụ (`scripts/fbf16/`, `config/fbf16/`)

| File | Dùng để |
|---|---|
| `chuan_bi.py` | Dựng `workspace/fbf16/<mã>/`: ảnh gốc, ảnh gốc nền trắng 768, 15 dáng mẫu nền trắng, bản chép bộ hoạ sĩ, `mo_ta.json` |
| `gen_qwen.py` | Sinh khung 2–16 bằng Qwen-Image-Edit-2511 (diffusers). **Chưa chạy thử**. Mô hình còn `check` trong registry |
| `ai_to_fbf16.py` (+ `lantern_scale.py`) | Ảnh AI nền trắng → 500×500 trong suốt: tách nền, **canh cỡ từng khung**, chân y = 498, kéo màu về ảnh gốc |
| `qa16.py` | Kiểm 16 khung so với khung 1: nhận dạng (SIFT), lệch màu, nhảy chân, sai cỡ, thiếu mảng, khung trùng, chạm mép. Có cờ thì thoát mã 2 |
| `kiem_chu_ky_di.py` | Khung đi 3–8 có **bước thật** không: đo độ chồng hình vùng chân giữa các khung kề nhau |
| `recolor_fx.py` | Đạn a/b/c cho quái mới: xoay sắc LCh bộ đạn chuẩn về màu phát sáng của quái |
| `fbf16_qa.py` | Đưa bộ 16 khung qua `postprocess.py run` để tham khảo |
| `config/fbf16/nguong-16-khung.json` | Ngưỡng từng khung, hiệu chỉnh trên 3 bộ hoạ sĩ |
| `config/fbf16/ti_le_cao_khuon.json`, `ti_le_dien_tich_khuon.json` | Tỉ lệ cao/diện tích từng khung so với khung 1 của khuôn chibi. Quái khác khuôn thì `--khong-khuon` |
| `config/fbf16/dang_16_khung.json` | Prompt 15 dáng, khung giữ nhân vật, mẫu `mo_ta.json` |

Mọi lệnh chạy từ thư mục skill (`plugin/skills/asset-gen`).

## 3. Đã thử, đã đo

| Cách | Kết quả |
|---|---|
| **FLUX.2 klein 4B** 4-bit (mflux, Mac M2 Pro 16 GB), ảnh gốc + **ảnh dáng mẫu** | **Hỏng.** Mô hình bỏ qua dáng mẫu và chép lại dáng đứng, kể cả khung nằm chết. Đảo thứ tự ảnh hay img2img đều không cứu được |
| FLUX.2 klein 4B, ảnh gốc + **mô tả dáng bằng chữ** | Ra đúng ý 16 dáng, phong cách giữ khá, ~70 s/khung. Nhưng khung đi 4–8 gần giống nhau (`kiem_chu_ky_di` 0,74), vũ khí đổi hình (gậy thành giáo), hoa văn trôi. `qa16` 8–9/15 cờ. **Người dùng chốt không đạt (10/11/2026)** |
| AI đổi cỡ theo dáng | Quỳ/nằm bị vẽ to ~1,4 lần, đứng lệch ±10 %. SIFT ước cỡ thất bại (4–5 điểm khớp). Thứ tự canh cỡ dùng được: tỉ lệ cao của khuôn (dáng đứng) → dò chi tiết cứng nhiều cỡ (`--chi-tiet`) → tỉ lệ diện tích khuôn |
| Đạn bằng `recolor_fx.py` | Dựng a/b/c của 59, 60 từ 58: ΔE 10–16, nhìn cùng họ màu, hình giữ 100 % |
| `qa16.py` trên 6 lỗi cài sẵn | Bắt 6/6 (đổi cỡ, đổi màu, mất phụ kiện, khung trùng, lẫn quái khác, lệch 40 px). **Không bắt** mất chi tiết nhỏ (nhẫn, số hiệu ứng bay): người duyệt bắt buộc |
| Bộ hoạ sĩ trong game | `60/4.png` trùng `5.png`. Khi dùng 60 làm dáng mẫu, lấy khung 5 từ 59 (`--thay 5=...`) |

## 4. Bước tiếp: thử Qwen-Image-Edit-2511 trên máy NVIDIA

Lý do chọn: Apache 2.0; giữ nhân vật tốt hơn mô hình 4B; nhận nhiều ảnh; hiểu sẵn ảnh khung xương OpenPose làm dáng. Đây đúng là thứ FLUX 4B thiếu.

**Trước khi bắt đầu, cần người dùng:**
- đồng ý tải mô hình;
- duyệt `qwen-image-edit` (và `dwpose` nếu dùng) sang `ok` trong `registry.json`. Quy tắc 8–10 của `HUONG-DAN.md`.

### 4.1 Máy

- GPU NVIDIA ≥ 24 GB. Card 24 GB đi bản fp8 trong ComfyUI; ≥ 48 GB chạy bf16 bằng diffusers.
- RAM ≥ 32 GB (≥ 64 GB nếu `--offload`), ổ trống ~60 GB.

### 4.2 Chuẩn bị (ảnh lấy từ repo client, không qua git này)

```
python scripts/fbf16/chuan_bi.py --ma u_minh_ho_phap --goc "<FE>/Monster/58 U Minh Ho Phap/1.png" --dang "<FE>/Monster/60 Vu Ho Phap" --thay 5="<FE>/Monster/59 Xich Ho Phap" --dap-an "<FE>/Monster/58 U Minh Ho Phap"
```

`<FE>` = `fights-break-sphere-client/Assets`. Sau đó điền `workspace/fbf16/u_minh_ho_phap/mo_ta.json`. Bản đã dùng với FLUX nằm ở báo cáo nội bộ: `ban-giao-nvidia/dau-vao/mo_ta.json`.

### 4.3 Ba chế độ, làm theo thứ tự (mỗi chế độ 15 khung × 4 seed)

| Chế độ | Ảnh 2 | Câu hỏi |
|---|---|---|
| `chu` | – (mô tả chữ) | Qwen có hơn FLUX ở cùng cách không? |
| `dang` | dáng mẫu hoạ sĩ | Qwen có theo được dáng mẫu mà không chép vũ khí/màu của con mẫu không? |
| `xuong` | khung xương của dáng mẫu | Có tách được dáng khỏi nhân vật mẫu không? |

**Khung xương:**
1. Chạy node DWPose Estimator (`comfyui_controlnet_aux`) trên `workspace/fbf16/<mã>/dang-mau-768/`.
2. Quái chibi đầu lâu có thể không được nhận là người. Xem từng ảnh xương, thiếu khớp thì sửa tay bằng trình chỉnh OpenPose.
3. Lưu `2..16.png`.

Bộ xương này dùng lại được cho mọi quái cùng khuôn.

**Đường ComfyUI** (khuyên dùng):
1. Mở template **Qwen Image Edit 2511** trong ComfyUI. Template báo file cần tải.
2. Chạy bằng tay với ảnh 1 = `goc-nen-trang-768.png`, ảnh 2 theo chế độ; prompt lấy từ `config/fbf16/dang_16_khung.json`.
3. Đặt tên ảnh ra `<khung>_s<seed>.png`.
4. Tốt thì Export (API) → `comfy_local.py templatize` (xem `workflows/README.md`). `comfy_local.py run` đã nhận nhiều ảnh: lặp `--input-image`, giữ chỗ `INPUT_IMAGE_2`.

LoRA tăng tốc vài bước (Lightning) chỉ dùng khi đã đọc giấy phép. Lần đầu nên chạy đủ bước để có mốc chất lượng.

**Đường diffusers:**

```
python scripts/fbf16/gen_qwen.py u_minh_ho_phap --che-do chu
python scripts/fbf16/gen_qwen.py u_minh_ho_phap --che-do dang
python scripts/fbf16/gen_qwen.py u_minh_ho_phap --che-do xuong --xuong-dir <thư mục xương>
```

Mặc định 40 bước, `true_cfg_scale` 4,0, ảnh 1024, theo model card và chưa tinh chỉnh. Card 24 GB thêm `--offload`. Mỗi lần sinh ghi `log_*.json` (prompt, seed, thời gian).

### 4.4 Hậu xử lý và kiểm

`W` = `../../../workspace/fbf16/u_minh_ho_phap`, `<cd>` = chế độ. Bước canh cỡ chạy riêng từng seed.

```
python scripts/fbf16/ai_to_fbf16.py W/ai/<cd> W/ai/<cd>-500 --seed 1 --goc W/goc.png --chi-tiet 368,240,427,332 --mau 0.6
python scripts/fbf16/qa16.py W/ai/<cd>-500 --ref W/goc.png --json W/ai/<cd>-500/qa.json
python scripts/fbf16/kiem_chu_ky_di.py W/ai/<cd>-500
```

`--chi-tiet` là hộp đèn lồng trên `goc.png` của con 58. Quái khác thì chọn một chi tiết cứng của nó, hoặc bỏ cờ này.

Sau đó **so bằng mắt** với `W/dap-an/`: bảng 16 khung (hoạ sĩ trên, AI dưới) và GIF theo nhịp game.

### 4.5 Tiêu chí đạt

| Tiêu chí | Đạt khi | Hoạ sĩ | FLUX 4B |
|---|---|---|---|
| Cờ `qa16.py` | ≤ 2/15 | 0–1 | 8–9 |
| `kiem_chu_ky_di.py` | TB ≤ 0,55 và max ≤ 0,65 | TB 0,41–0,50 | 0,74 |
| Nhận dạng nhóm đứng/đi/thi pháp | ≥ 0,075 | 0,075–0,17 | 0,03–0,07 |
| Người duyệt | Vũ khí một kiểu ở cả 16 khung; đủ mũ, phụ kiện, hiệu ứng bay; không chép đồ của con mẫu | – | gậy thành giáo, hoa văn trôi |

- **Đạt cả bốn tiêu chí:** làm workflow chính thức. Việc cần làm: mẫu `*.template.json`, `status: tested` trong registry, và bước xuất 16 PNG + a/b/c thay cho atlas.
- **Đạt một phần:** dùng làm bản nháp cho hoạ sĩ sửa.
- **Không đạt:** báo lại. Còn API trả phí (cần duyệt chi tiền) hoặc LoRA nhân vật.

Ghi kết quả vào báo cáo nội bộ ART-02, mục §6.7.

## 5. Quy trình khi đã có mô hình đạt

1. `chuan_bi.py`.
2. Sinh khung 2–16, 4 seed mỗi khung.
3. `ai_to_fbf16.py` từng seed.
4. `qa16.py` + `kiem_chu_ky_di.py`, chọn seed tốt nhất mỗi khung.
5. Sinh lại khung có cờ, tối đa 3 vòng (`budget`).
6. Làm đạn bằng `recolor_fx.py`:
   ```
   python scripts/fbf16/recolor_fx.py <bộ đạn chuẩn> <ra> --from-image <khung1 quái mới> --chroma-ref <khung1 quái gốc>
   ```
7. Người duyệt xem bảng khung và GIF. Chi tiết nhỏ sai thì hoạ sĩ sửa tay.
8. Đưa vào client bằng `fbf_unity.py`. Script này thuộc plugin `dpvs-studio` và tự tính pivot chân từng khung.
