# VS Monster Forge – Xưởng Quái

Công cụ làm quái frame-by-frame cho game 2D, chạy trên máy nhà.

Ứng dụng làm animation frame-by-frame (FBF) cho quái game, giữ nguyên phong cách ảnh. Mọi thứ chạy trên máy của studio, không thuê cloud:
- **ComfyUI** và mô hình **Wan 2.2** đều miễn phí, dùng được thương mại (Apache 2.0).
- Quái cũ có thể **xuất thẳng từ Spine**, không cần AI, không cần card mạnh.
- Pet và nhân vật vẫn dùng Spine như cũ.

## Mở ứng dụng
- **Windows:** bấm đúp `start.bat`. Lần đầu nó tự cài thư viện (cần mạng), sau đó trình duyệt tự mở `http://127.0.0.1:7865`.
- **Linux/macOS:** chạy `./start.sh`.
- Giữ cửa sổ đen mở trong lúc dùng; đóng nó là tắt ứng dụng.
- Cần **Python 3.10+**. Khi cài trên Windows, đánh dấu "Add python.exe to PATH".

Ứng dụng chỉ mở cho chính máy đó truy cập. Dữ liệu nằm trong thư mục `workspace/` cạnh ứng dụng; bản đã duyệt nằm ở `workspace/export/<mã quái>/`.

## Làm một con quái (4 bước trên giao diện)
1. **Tạo quái mới**: tải file Spine JSON của quái (giữ nguyên tên animation, nhịp và sự kiện đòn đánh), hoặc chọn bộ animation chuẩn nếu là quái mới.
2. **Ảnh chuẩn**: ảnh quái đứng yên, quay phải, nền một màu. Bắt buộc nếu sinh bằng AI.
3. **Khung hình từng animation**, chọn một trong hai cách:
   - *Nhập thư mục PNG từ Spine*: trong Spine Editor xuất chuỗi PNG nền trong suốt cho animation đó, rồi chọn thư mục.
   - *Sinh bằng AI*: viết mô tả động tác, chọn số biến thể (1–4). Mỗi biến thể có ảnh động xem trước và kết quả kiểm tra nhanh. Bấm **Dùng bản này** cho bản đẹp nhất.
4. **Hậu xử lý và kiểm tra**: tách nền, đưa về cùng cỡ, khoá chân cho hết rung, ghép atlas, tự kiểm lỗi. Xem bảng khung bằng mắt.
5. **Duyệt và xuất**: ghi tên người duyệt, chép atlas, manifest và báo cáo sang thư mục xuất.

Bật **Nâng cao** (góc trên phải) để thấy thêm: seed, số khung, kích thước, prompt loại trừ, sửa spec JSON, bảng QA đầy đủ, nhật ký từng việc.

## Để dùng được phần AI (làm một lần)
1. Cài **ComfyUI** (bản Desktop hoặc Portable cho Windows) trên máy có card NVIDIA. Mở và để chạy; mặc định ở `http://127.0.0.1:8188`.
2. Đặt 3 file mô hình Wan 2.2 5B (chạy được trên card 8 GB) vào đúng thư mục trong ComfyUI:
   | File | Thư mục |
   |---|---|
   | `wan2.2_ti2v_5B_fp16.safetensors` | `models/diffusion_models/` |
   | `umt5_xxl_fp8_e4m3fn_scaled.safetensors` | `models/text_encoders/` |
   | `wan2.2_vae.safetensors` | `models/vae/` |

   Cách dễ nhất: mở template "Wan 2.2 5B" trong ComfyUI. Nếu thiếu file, ComfyUI sẽ báo và chỉ chỗ tải.
3. Trong Xưởng Quái bấm **Kiểm tra lại** ở thanh trên. Thanh trên sẽ hiện tên card, VRAM và trạng thái "ComfyUI đã kết nối".

> Mẫu workflow Wan 2.2 trong gói **chưa từng chạy trên ComfyUI thật** (chỉ thử với ComfyUI giả lập). Nếu báo lệch node, làm theo `plugin/skills/asset-gen/workflows/README.md` để tạo mẫu từ template có sẵn trong ComfyUI.

## Dùng bằng dòng lệnh (cho dev, hoặc để Claude Code tự chạy)
Các bước trên đều có lệnh tương ứng trong `plugin/skills/asset-gen/scripts/`:
- `comfy_local.py`: `doctor`, `templatize`, `validate`, `run`
- `spine_timing.py`
- `postprocess.py`: `run`, `check`

Quy trình cho Claude Code nằm trong `plugin/skills/asset-gen/SKILL.md`.

## Cấu trúc
```
start.bat / start.sh            mở ứng dụng
requirements.txt                numpy, opencv-python, pillow, scipy
app/
├── server.py                   máy chủ web cục bộ (thư viện chuẩn Python)
├── core.py                     không gian làm việc, hàng đợi việc (GPU chạy lần lượt), các thao tác
├── static/                     giao diện (HTML/CSS/JS thuần)
└── tests/test_app.py           test đầu-cuối qua HTTP với ComfyUI giả lập
plugin/skills/asset-gen/        skill + script + mẫu workflow + registry giấy phép + test
```

## Kiểm thử
```
pip install pytest
python -m pytest -q app/tests plugin/skills/asset-gen/tests
```
Kết quả hiện tại: 47 test đạt. Toàn bộ chạy trên dữ liệu và ComfyUI giả lập.

## Chưa làm
| Hạng mục | Cần gì |
|---|---|
| Chạy mẫu Wan 2.2 trên ComfyUI thật | Một máy studio có card NVIDIA |
| Mẫu first-last-frame (khép vòng lặp idle/run) | Tạo từ template có sẵn trong ComfyUI |
| LoRA giữ phong cách | 30–50 ảnh render Spine của quái hiện có |
| Nộp draft vào game từ nút "Duyệt" | Xem `slice_sprites.py`, `validate_payload.py` |
| Phần phát animation FBF trong Unity | Đọc repo client |
| Ngưỡng QA | Hiệu chỉnh trên quái thật trong pilot |
