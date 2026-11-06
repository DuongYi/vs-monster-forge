# Mẫu workflow ComfyUI (chạy trên máy nhà)

Mỗi mẫu `*.template.json` là một workflow ComfyUI dạng API đã đặt chỗ giữ chỗ (`__SEED__`, `__PROMPT__`, `__INPUT_IMAGE__`, `__NUM_FRAMES__`…), kèm giá trị mặc định, quy tắc tham số và danh sách mô hình cần có. `scripts/comfy_local.py` điền tham số và chạy.

## Đang có
| Mẫu | Trạng thái | Ghi chú |
|---|---|---|
| `wan22_5b_i2v.template.json` | **untested** | Viết tay theo tài liệu Wan 2.2. Đã kiểm với ComfyUI giả lập, **chưa chạy trên ComfyUI thật**. Chạy `validate` trên máy trước. Nếu báo lệch node/input → làm theo mục dưới để tạo mẫu từ template chính thức. |

## Tạo mẫu từ giao diện ComfyUI (cách chắc chắn nhất)
1. Trong ComfyUI: mở **Templates** → chọn template gần nhất (vd. "Wan 2.2 5B video", "Wan 2.2 14B First-Last Frame").
   - Nếu thiếu mô hình, ComfyUI sẽ báo. Tải đúng tên file vào đúng thư mục `ComfyUI/models/...`.
2. **Đổi node lưu kết quả sang `Save Image`** (lưu chuỗi PNG). Lưu video sẽ nén mất chi tiết mép, làm khó tách nền.
3. Đặt prompt có "static camera, plain flat solid green background". Chạy tay 1 lần cho chắc chạy được.
4. Menu **Workflow ▸ Export (API)** → lưu file, ví dụ `wan22_14b_flf_api.json`.
5. Chạy:
   ```
   python scripts/comfy_local.py templatize --workflow wan22_14b_flf_api.json --name wan22_14b_flf \
          --rule NUM_FRAMES=4n+1 --rule WIDTH=multiple_of_32 --rule HEIGHT=multiple_of_32 --min-vram 16 \
          --out workflows/wan22_14b_flf.template.json
   ```
   Đọc danh sách thay thế lệnh in ra. Dòng có `!` cần sửa tay.
6. `python scripts/comfy_local.py validate --template workflows/wan22_14b_flf.template.json`
7. Chạy thử `run` → `postprocess.py run`. QA đạt thì đổi `"status": "tested"` trong mẫu và trong `registry.json`.

## Quy tắc
- Mẫu nằm trong git; thay đổi qua PR. `provenance.json` ghi sha256 của mẫu, nên mọi asset truy được về đúng phiên bản.
- Seed cố định: cùng mẫu + cùng tham số + cùng seed + cùng máy → cùng kết quả. Máy/driver khác có thể lệch nhẹ.
- Mọi file mô hình trong mẫu phải khớp một mục `license_status: "ok"` trong `registry.json`. `validate` và `run` tự chặn file không khớp.
  Thêm mô hình mới = thêm mục vào registry kèm giấy phép đã đọc từ model card.
- Không dùng LoRA/checkpoint ghi "personal use" hoặc "non-commercial".
