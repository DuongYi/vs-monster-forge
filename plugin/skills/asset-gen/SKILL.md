---
name: asset-gen
description: Làm animation frame-by-frame (FBF) cho quái, chạy hoàn toàn trên máy nhà (ComfyUI cục bộ, không thuê cloud), giữ đúng phong cách vẽ hiện có. Gồm xuất từ Spine, sinh bằng AI, hậu xử lý, tự kiểm tra và trình người duyệt. Dùng khi cần làm mới hoặc chuyển một con quái từ Spine sang FBF, sinh lại một animation của quái, hoặc kiểm tra một atlas FBF đã có.
---

# asset-gen – Quái FBF, chạy trên máy nhà

Định hướng đã chốt:
- **Quái** chuyển từ Spine sang frame-by-frame, **giữ nguyên phong cách ảnh**. Pet và nhân vật vẫn dùng Spine.
- **Chạy hoàn toàn trên máy của studio**: ComfyUI cài trên máy có card NVIDIA, gọi qua `scripts/comfy_local.py`. **Không thuê Comfy Cloud.**

Quy tắc không đổi: AI chỉ đọc dữ liệu và nộp **draft**. Người duyệt approve → backend ghi DB → phát hành bundle → Unity tải về. Không ghi thẳng vào game.

## Công cụ

| Đường dẫn | Dùng để |
|---|---|
| `scripts/comfy_local.py doctor` | Kiểm máy: GPU, VRAM, RAM, mô hình đã có, mẫu nào chạy được |
| `scripts/comfy_local.py templatize` | Workflow xuất từ ComfyUI (Export (API)) → mẫu dùng lại được |
| `scripts/comfy_local.py validate` | Mẫu chạy được trên máy này không: node, file mô hình, giấy phép |
| `scripts/comfy_local.py run` | Chạy mẫu (1 hoặc nhiều seed), in tiến độ từng bước, tải kết quả về `raw/`, ghi `provenance.json` |
| `scripts/spine_timing.py` | Spine JSON → spec (giữ tên anim, tên sự kiện, thời lượng) |
| `scripts/postprocess.py` | Xoá nền, khử viền, scale, căn chân, atlas, manifest, QA (`run` / `check`) |
| `config/anim_presets.json` | Bộ anim chuẩn: quái thường / tinh anh / boss / bay |
| `workflows/registry.json` | Mô hình được phép (theo tên file) + trạng thái workflow |
| `workflows/*.template.json` | Mẫu workflow đã cố định |
| `reference/` | Mô tả spec/manifest; kế hoạch chuyển đổi Spine → FBF |

Mọi lệnh chạy từ thư mục skill (`plugin/skills/asset-gen`). ComfyUI mặc định ở `http://127.0.0.1:8188`; đổi bằng `--url`.

Người dùng thường làm qua ứng dụng **Xưởng Quái** (`start.bat` ở thư mục gốc gói). Ứng dụng chạy đúng quy trình dưới đây, dữ liệu ở `workspace/monsters/<id>/`:
- `spec.json`, `ref/ref.png`
- `raw/<anim>/` (khung đang dùng)
- `variants/<anim>/seed_<n>/` (biến thể AI)
- `out/` (kết quả hậu xử lý)

Khi làm việc trên dữ liệu của ứng dụng, đọc/ghi đúng các vị trí này để giao diện hiển thị được. Không sửa trong `workspace/export/`, vì đó là bản người đã duyệt.

## Quy trình cho một con quái

### 0. Kiểm tra máy (chỉ cần khi chạy lần đầu trong phiên)
```
python scripts/comfy_local.py doctor
```
- Không kết nối được → nhắc người dùng mở ComfyUI. Không tự cài hay tự khởi động phần mềm trên máy người dùng.
- Máy dưới 8 GB VRAM, hoặc ComfyUI chạy CPU → chỉ dùng **Đường A** (xuất Spine).
- Một mẫu ở trạng thái `CHƯA` → báo cho người dùng đúng từng vấn đề doctor liệt kê (thiếu file nào, vào thư mục nào).

### 1. Thu thập
1. Thông tin quái: dùng `get_content` (monster) qua MCP backend nếu có. Mã quái ↔ Spine ID nằm ở tab Quái của "Sheet data cho Dũng".
2. **Quái cũ có Spine:**
   `python scripts/spine_timing.py <monster>.json --fps 12 --monster-id <id> --out specs/<id>.json`
   Giữ nguyên tên animation và tên sự kiện, vì code client và server đang gọi theo các tên này.
3. **Quái mới:** chép preset trong `config/anim_presets.json` sang `specs/<id>.json`.
4. Ảnh chuẩn phong cách = ảnh tư thế idle (render từ Spine, hoặc concept đã duyệt). Ghi vào `style_reference` của spec.

### 2. Chọn đường làm

| Đường | Khi nào | Chi phí |
|---|---|---|
| **A. Xuất Spine** | Quái cũ, chấp nhận chuyển động y như bản Spine | Không cần GPU, không AI |
| **B. AI vẽ lại / sinh mới** | Quái mới chưa có Spine, hoặc muốn chuyển động/chi tiết đẹp hơn bản Spine | Thời gian GPU của máy nhà |

Mặc định với quái cũ là **A**, trừ khi người dùng nói muốn vẽ lại.

### 3A. Xuất Spine
Hướng dẫn người dùng: Spine Editor ▸ Export ▸ chuỗi PNG, fps = fps trong spec, nền trong suốt, mỗi animation một thư mục, đặt vào `raw/<id>/<anim>/`. Sau đó chuyển sang bước 4.

### 3B. Sinh bằng AI trên máy
1. **Ảnh gốc** (quái mới): sinh tối đa 4 biến thể, nền một màu phẳng (mặc định `#00FF00`; quái tông xanh lá dùng `#FF00FF`), quay mặt sang **phải**. Trình người dùng chọn 1 bản.
2. **Từng animation:**
   ```
   python scripts/comfy_local.py run --template workflows/<mẫu>.template.json \
          --input-image ref/<id>_idle.png --set PROMPT="<mô tả động tác>, static camera, plain flat solid green background" \
          --set NUM_FRAMES=49 --seeds 2 --dest raw/<id>/<anim>
   ```
   - Chỉ dùng mẫu mà `validate` báo OK. Lệnh `run` cũng tự validate và sẽ từ chối mẫu dùng mô hình chưa duyệt giấy phép.
   - Nhiều seed → kết quả nằm ở `raw/<id>/<anim>/seed_<n>/`. Chọn bản tốt nhất (sau khi hậu xử lý thử), chuyển nội dung bản đó lên `raw/<id>/<anim>/`.
   - Anim lặp (idle, run): ưu tiên mẫu first-last-frame nếu máy đủ VRAM. Truyền `--input-image` hai lần: khung đầu và khung cuối là cùng ảnh gốc.
   - `provenance.json` do `run` ghi tự động. **Không sửa tay.**
3. **Giới hạn** (`workflows/registry.json` → `budget`): tối đa 6 lượt mỗi lệnh, 3 vòng sinh lại mỗi animation. Báo trước thời gian ước tính khi làm lô nhiều quái, vì máy sẽ bận.

### 4. Hậu xử lý + QA
```
python scripts/postprocess.py run --spec specs/<id>.json --input raw/<id> --out out/<id> --thresholds config/qa_thresholds.json
```
Đầu ra: `<id>.png` (atlas), `<id>.fbf.json` (manifest, có provenance từng anim), `qa_report.md/json`, `preview/contact_sheet.png`, `preview/<anim>.gif`.

### 5. AI tự soát
1. Đọc `qa_report.md`.
2. **Mở `preview/contact_sheet.png` bằng Read và xem bằng mắt.** QA tự động không bắt được sai giải phẫu, sai chi tiết trang phục, vũ khí mất/hiện, hay mặt đổi giữa các khung.
3. Sửa theo bảng dưới. **Tối đa 3 vòng sinh lại cho mỗi animation**; quá 3 vòng thì dừng, chuyển người kèm ghi chú.

| Lỗi | Nguyên nhân hay gặp | Cách xử lý |
|---|---|---|
| `comfy_local` báo hết bộ nhớ (out of memory) | Khung quá lớn hoặc quá nhiều khung | Giảm `WIDTH/HEIGHT` (bội số 32), giảm `NUM_FRAMES` (dạng 4n+1), đóng ứng dụng khác dùng GPU |
| `frame_count` fail | Nguồn ít khung hơn spec | Tăng `NUM_FRAMES`, hoặc giảm `frames` trong spec (rồi kiểm lại `duration`) |
| `halo` / `key_residue` | Nền không phẳng hoặc quái trùng màu khoá | Đổi màu khoá trong prompt; thêm node tách nền vào workflow |
| `pop` / `area_flicker` | Khung hỏng, biến hình | Bỏ khung bằng `pick`, hoặc sinh lại seed khác |
| `loop_seam` | Video không khép vòng | Dùng mẫu first-last-frame; hoặc thu hẹp `trim` |
| `color_drift` / `brightness_drift` | Mô hình trôi màu | Tăng trọng số LoRA/ảnh mẫu; sinh lại |
| `duration` / `event:*` | Lệch nhịp với bản Spine | Sửa `frames`/`fps`/`events`; với quái cũ cân nhắc quay về Đường A |
| `scale_spread` | Các anim sinh ở cỡ khác nhau | Sinh cùng `WIDTH/HEIGHT`; hoặc `scale_mode: "first_frame"` |
| `atlas_size` | Quá nhiều khung / quá to | Giảm `target_height_px` hoặc số khung |
| Thấy bằng mắt: sai chi tiết | Mô hình quên chi tiết | Sinh lại; quá 3 vòng → hoạ sĩ sửa tay rồi chạy `postprocess.py check` |

### 6. Trình duyệt
Gửi người dùng `contact_sheet.png`, các file `.gif`, `qa_report.md`, kèm tóm tắt: đường A hay B, số vòng sinh, tổng thời gian GPU (cộng `duration_s` trong các file provenance), cảnh báo còn lại. Quái chủ lực (boss, quái map đầu) bắt buộc có người duyệt.

### 7. Nộp draft
Chỉ làm sau khi người dùng duyệt. Luồng hiện có: `slice_sprites.py` → `validate_payload.py sprite` → MCP `submit_draft("sprite", payload, sourceRef, rationale)`.
- `sourceRef` = đường dẫn manifest + sha256 atlas.
- `rationale` = mẫu, mô hình, LoRA, seed (lấy từ `provenance` trong manifest).
- **Manifest `dpvs.fbf/1` chưa được ánh xạ sang schema `sprite`.** Chưa có adapter thì dừng ở bước 6 và báo rõ; không tự đoán schema.

## Dựng mẫu workflow mới
Xem `workflows/README.md`. Tóm tắt: mở template trong giao diện ComfyUI → chạy được bằng tay → Export (API) → `templatize` → `validate` → chạy thử + `postprocess` → đổi `status` thành `tested` trong mẫu và registry.
