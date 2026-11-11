# Hướng dẫn sử dụng VS Monster Forge (Xưởng Quái)

Tài liệu này giúp dựng và chạy Xưởng Quái **trên một máy Windows có card NVIDIA**, rồi làm quái hằng ngày theo đúng quy tắc.

- Cài lần đầu mất khoảng 1 giờ, phần lớn là thời gian tải ~18 GB mô hình.
- Các lần sau chỉ cần mở ComfyUI rồi bấm đúp `start.bat`.

Mục lục:
1. [Máy cần gì](#1-máy-cần-gì)
2. [Cài đặt lần đầu](#2-cài-đặt-lần-đầu)
3. [Chạy thử lần đầu](#3-chạy-thử-lần-đầu)
4. [Làm một con quái](#4-làm-một-con-quái)
5. [Quy tắc bắt buộc](#5-quy-tắc-bắt-buộc)
6. [Xử lý sự cố](#6-xử-lý-sự-cố)
7. [Dùng bằng dòng lệnh](#7-dùng-bằng-dòng-lệnh)
8. [Phiếu ghi lần chạy đầu](#8-phiếu-ghi-lần-chạy-đầu)

---

## 1. Máy cần gì

| | Tối thiểu | Nên có |
|---|---|---|
| Card đồ hoạ | NVIDIA 8 GB VRAM (RTX 3060 8GB, RTX 4060) | NVIDIA 12–16 GB trở lên (RTX 4060 Ti 16GB, 4070 Ti Super, 3090, 4090) |
| RAM | 16 GB (chạy được nhưng chậm) | 32 GB trở lên |
| Ổ cứng trống | 40 GB | 100 GB, ổ SSD |
| Hệ điều hành | Windows 10/11 64-bit | |

Gợi ý theo VRAM (ước tính, xác nhận bằng chạy thử):

| VRAM | Dùng được |
|---|---|
| Dưới 8 GB | Chỉ đường Spine, không sinh AI |
| 8–12 GB | Wan 2.2 5B, khung ≤ 640 px, ≤ 49 khung, chậm |
| 12–16 GB | Wan 2.2 5B thoải mái |
| 24 GB trở lên | Thêm Wan 2.2 14B và mẫu khép vòng lặp (first-last-frame) |

> **Máy Mac chip Apple (M1/M2/M3…) không có card NVIDIA.** Trên Mac chỉ nên dùng Xưởng Quái cho đường Spine (nhập PNG, hậu xử lý, duyệt). Hướng dẫn này không áp dụng cho việc sinh AI trên Mac.

---

## 2. Cài đặt lần đầu

### Bước 1: Driver NVIDIA
Cập nhật driver mới nhất qua ứng dụng NVIDIA App, hoặc tại https://www.nvidia.com/drivers. ComfyUI bản mới cần driver mới.

### Bước 2: Git và Python
- **Git for Windows:** https://git-scm.com/download/win
- **Python 3.10 trở lên:** https://www.python.org/downloads/. Khi cài, **tick "Add python.exe to PATH"**.

### Bước 3: Lấy mã Xưởng Quái
Mở Command Prompt, chọn chỗ đặt (ví dụ ổ D):
```
cd /d D:\
git clone https://github.com/DuongYi/vs-monster-forge.git
```
Đã có thư mục từ trước thì vào thư mục đó và chạy `git pull` để lấy bản mới nhất.

### Bước 4: Cài ComfyUI (chọn một trong hai)

| | **A. ComfyUI Portable** (khuyên dùng cho máy chuyên chạy) | **B. ComfyUI Desktop** (dễ cài nhất) |
|---|---|---|
| Tải | Mục Releases trên https://github.com/comfyanonymous/ComfyUI/releases, bản Windows portable cho NVIDIA | https://www.comfy.org/download |
| Cài | Giải nén (dùng 7-Zip), bấm đúp `run_nvidia_gpu.bat` | Chạy file cài, làm theo hướng dẫn |
| Cổng | 8188 | 8000 |
| Thư mục mô hình | `ComfyUI_windows_portable\ComfyUI\models\` | `models\` trong thư mục cài đã chọn lúc cài |

Xưởng Quái **tự dò cả cổng 8188 và 8000**, nên không cần chỉnh gì thêm.

**Bật ảnh xem trước khi đang sinh** (để khay việc của Xưởng Quái hiện ảnh nhỏ):
- **Portable:** mở `run_nvidia_gpu.bat` bằng Notepad. Thêm ` --preview-method auto` vào cuối dòng có chữ `main.py`, lưu lại, chạy lại.
- **Desktop:** Settings → Server Config → mục Preview → Preview Method chọn **Taesd** (rõ hơn) hoặc **Latent**, rồi khởi động lại ComfyUI.

Không bật thì vẫn sinh bình thường, chỉ không có ảnh xem trước.

### Bước 5: Tải 3 file mô hình Wan 2.2 5B (tổng khoảng 18 GB)

| File | Đặt vào | Link tải trực tiếp |
|---|---|---|
| `wan2.2_ti2v_5B_fp16.safetensors` | `models\diffusion_models\` | https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors |
| `umt5_xxl_fp8_e4m3fn_scaled.safetensors` | `models\text_encoders\` | https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors |
| `wan2.2_vae.safetensors` | `models\vae\` | https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/vae/wan2.2_vae.safetensors |

- **Giữ nguyên tên file.** Xưởng Quái kiểm tra giấy phép mô hình theo tên file.
- Cách khác: trong ComfyUI mở template "Wan 2.2 5B Video Generation" (bước 7). Nếu thiếu file, ComfyUI sẽ hiện nút tải.

Nguồn: hướng dẫn Wan 2.2 chính thức của ComfyUI, https://docs.comfy.org/tutorials/video/wan/wan2_2

### Bước 6: ComfyUI-Manager (nên có)
Dùng để tự tìm và cài node hoặc mô hình còn thiếu khi mở workflow mới. Bản Desktop thường có sẵn. Bản Portable chưa có thì cài theo https://github.com/Comfy-Org/ComfyUI-Manager.

### Bước 7: Kiểm tra ComfyUI chạy được trước
Mở ComfyUI trong trình duyệt:
- Portable: http://127.0.0.1:8188
- Desktop: cửa sổ ứng dụng

Chọn **Templates** (hoặc Workflow → Browse Templates) → nhóm Video → **Wan 2.2 5B Video Generation** → bấm **Run**.

Chạy ra video là ComfyUI và mô hình đã ổn. **Đừng sang bước sau khi bước này còn lỗi.** Bước này giúp biết lỗi nằm ở ComfyUI hay ở Xưởng Quái.

---

## 3. Chạy thử lần đầu

1. Mở ComfyUI, để nó chạy.
2. Trong thư mục `vs-monster-forge`, **bấm đúp `start.bat`**.
   - Lần đầu, nó tự cài thư viện Python (cần mạng, vài phút).
   - Trình duyệt tự mở http://127.0.0.1:7865.
   - **Giữ cửa sổ đen mở** trong lúc dùng; đóng nó là tắt Xưởng Quái.
3. Thanh trên cùng phải hiện: "ComfyUI đã kết nối (cổng …), Card NVIDIA …, … GB VRAM". Không hiện thì xem [mục 6](#6-xử-lý-sự-cố).
4. Bấm **Cài đặt** → nhập **Tên người duyệt** → Lưu cài đặt.
5. Tạo một **quái thử**:
   - Bấm **Tạo quái mới**.
   - Mã quái: `mob_thu`.
   - Chọn "Bộ animation chuẩn" → Quái thường.
   - Bấm Tạo quái.
6. Bước 1 trên trang quái: **Tải ảnh chuẩn lên** (một ảnh quái bất kỳ, quay phải).
7. Ở animation `idle`, bấm **Sinh bằng AI**:
   - Số biến thể: **1**.
   - Bật **Nâng cao** (góc trên phải), đặt "Số khung sinh" **25**, "Rộng × cao" **512 × 512** để lần đầu chạy nhanh.
   - Bấm **Sinh 1 biến thể**.
8. Xem khay việc ở cuối màn hình. Phải thấy lần lượt: "Nạp mô hình" → "Đọc mô tả động tác" → "Sinh khung: bước x/20" kèm thanh tiến độ, ảnh xem trước → "Giải mã khung hình" → Xong. Bật Nâng cao và mở "Các bước trong flow" để xem từng node.
9. Xong sẽ có một ô biến thể kèm ảnh động và kết quả kiểm tra. Bấm **Dùng bản này**.
10. Hậu xử lý cần đủ khung cho mọi animation. Với quái thử:
    - Nâng cao → **Sửa spec (JSON)**: xoá các animation khác trong `"animations"`, chỉ giữ `"idle"` → Lưu spec.
    - Bấm **Chạy hậu xử lý**.
    - Xem bảng khung và danh sách lỗi.
11. Điền [phiếu ghi lần chạy đầu](#8-phiếu-ghi-lần-chạy-đầu) và gửi lại để chỉnh ngưỡng kiểm tra cùng cấu hình mặc định.

Nếu bước 7 báo "Máy này chưa chạy được mẫu này" kèm lỗi node hoặc input, nghĩa là mẫu viết sẵn lệch với bản ComfyUI đang cài. Xem mục **Tạo lại mẫu từ template chính thức** ở [mục 6](#6-xử-lý-sự-cố).

---

## 4. Làm một con quái

### Bước 1: Tạo quái
- **Quái cũ (đã có Spine): luôn tạo từ file Spine JSON.**
  - Trong Spine Editor: Export → JSON.
  - Trong Xưởng Quái: Tạo quái mới → File Spine JSON.
  - Cách này giữ đúng tên animation, nhịp và thời điểm ra đòn, nên code game không phải sửa.
- **Quái mới:** chọn bộ animation chuẩn (Quái thường, Tinh anh, Boss, Quái bay).
- **Mã quái phải trùng mã quái trong dữ liệu game.**

### Bước 2: Ảnh chuẩn
- Quái đứng yên, toàn thân, **quay mặt sang phải**.
- Quái cũ: lấy một khung của animation idle xuất từ Spine. Nền trong suốt cũng được; Xưởng Quái tự lót màu nền trước khi gửi cho AI.
- Quái mới: concept đã được duyệt.

### Bước 3: Khung hình từng animation (chọn cách cho từng animation)
- **Nhập từ Spine (quái cũ, không cần AI, đúng 100% phong cách):**
  - Trong Spine Editor xuất animation đó thành **chuỗi ảnh PNG**: nền trong suốt, không gộp thành sprite sheet.
  - Khung/giây bằng số ghi trên thẻ animation trong Xưởng Quái.
  - Xuất ở kích thước đủ lớn: quái cao ít nhất gấp 1,5 lần "Chiều cao quái khi xuất".
  - Mỗi animation một thư mục. Bấm **Nhập thư mục PNG từ Spine** và chọn thư mục đó.
- **Sinh bằng AI (quái mới, hoặc muốn chuyển động đẹp hơn bản Spine):**
  - Viết mô tả động tác **bằng tiếng Anh**, ngắn gọn. Ví dụ: `lunges forward and bites, then returns to stance`.
  - Sinh **2 biến thể**, xem ảnh động, bấm **Dùng bản này** cho bản tốt nhất.
  - Số khung sinh nên nhiều hơn số khung của animation (ví dụ animation 8 khung thì sinh 25–49 khung). Công cụ tự chọn khung đẹp nhất và tự tìm điểm lặp.

### Bước 4: Hậu xử lý và kiểm tra
- Bấm **Chạy hậu xử lý**.
- **Luôn xem bảng khung bằng mắt.** Kiểm tra tự động không bắt được sai giải phẫu, mất vũ khí, hay mặt đổi giữa các khung.
- Chữ thập đỏ là điểm neo ở chân; vạch xanh là mặt đất.

### Bước 5: Duyệt và xuất
- Bấm **Duyệt và xuất**.
- Kết quả nằm ở `workspace\export\<mã quái>\`:
  - atlas `.png`;
  - manifest `.fbf.json`;
  - báo cáo kiểm tra;
  - bảng khung;
  - `approval.json` (ai duyệt, lúc nào).
- Gửi thư mục này cho dev Unity.

---

## 5. Quy tắc bắt buộc

### Đặt tên và dữ liệu
1. Mã quái trùng mã trong dữ liệu game. Chỉ dùng chữ thường, số và `_`.
2. Quái cũ **luôn tạo từ Spine JSON**. Không đổi tên animation hay tên sự kiện (`hit`…), vì client và server đang gọi theo các tên này.
3. Thời lượng và thời điểm ra đòn phải khớp bản cũ. Kiểm tra báo lỗi "Thời lượng lệch bản cũ" hoặc "Sự kiện … lệch nhịp" thì sửa trước khi duyệt.

### Ảnh chuẩn và sinh AI
4. Ảnh chuẩn quay phải, toàn thân, nền **một màu phẳng**, không bóng đổ trên nền.
5. Mặc định nền xanh lá. **Quái tông xanh lá** thì đổi màu khoá sang magenta: Nâng cao → Sửa spec → `"background": {"mode": "key", "color": [255, 0, 255]}`.
6. **Tối đa 3 vòng sinh lại cho mỗi animation.** Quá 3 vòng mà vẫn hỏng thì chuyển hoạ sĩ sửa tay, không sinh tiếp.
7. Không sửa tay file `provenance.json`, vì đó là hồ sơ truy vết (mẫu, mô hình, seed).

### Mô hình và giấy phép
8. **Chỉ dùng mô hình có trong `plugin/skills/asset-gen/workflows/registry.json` với trạng thái `ok`.** Xưởng Quái tự chặn mô hình lạ.
9. Không tải về LoRA hay checkpoint ghi "personal use", "non-commercial" hoặc "research only".
10. Muốn thêm mô hình: đọc model card, ghi giấy phép và link vào `registry.json`, rồi commit có mô tả.

### Duyệt
11. Luôn xem bảng khung bằng mắt trước khi duyệt.
12. Kiểm tra còn lỗi thì chỉ duyệt khi có lý do rõ ràng, ghi vào ô Ghi chú.
13. Boss và quái chủ lực phải do art lead duyệt.
14. **Không sửa tay trong `workspace\export\`.** Sửa ở nguồn (khung, spec) rồi chạy lại hậu xử lý và duyệt lại.

### Git và bảo mật
15. **Repo đang để công khai (Public).** Không commit:
    - thư mục `workspace\`;
    - ảnh, Spine, spec của quái thật;
    - mật khẩu hay key.

    `workspace\` và `app\settings.json` đã nằm trong `.gitignore`.
16. Thay đổi mẫu workflow, ngưỡng kiểm tra hay registry đều qua commit có mô tả rõ, để truy vết được asset nào làm bằng phiên bản nào.
17. Không chạy Xưởng Quái với `--host 0.0.0.0`, và không mở ComfyUI bằng `--listen`, ngoài mạng nội bộ tin cậy. Cả hai đều không có đăng nhập.

### Máy
18. Mỗi máy chỉ chạy một Xưởng Quái với một ComfyUI.
19. Khi đang sinh, tránh chạy game hay phần mềm nặng GPU khác cùng lúc.
20. Trước mỗi buổi làm, chạy `git pull` để lấy bản mới.

---

## 6. Xử lý sự cố

| Hiện tượng | Nguyên nhân | Cách xử lý |
|---|---|---|
| Bấm `start.bat` báo chưa có Python | Chưa cài, hoặc quên tick "Add to PATH" | Cài lại Python và tick ô đó |
| Thanh trên báo "ComfyUI chưa mở" | ComfyUI chưa chạy, hoặc chạy ở cổng lạ | Mở ComfyUI rồi bấm **Kiểm tra lại**. Cổng khác 8188/8000 thì vào Cài đặt → Địa chỉ ComfyUI |
| "Thiếu node 'Wan22ImageToVideoLatent'" | ComfyUI cũ | Cập nhật ComfyUI (Portable: chạy `update\update_comfyui.bat`; Desktop: tự cập nhật) |
| "Chưa có file mô hình '…'" | Sai thư mục hoặc sai tên file | Đối chiếu bảng ở Bước 5, giữ nguyên tên file |
| "license_status" hoặc "chưa có trong registry" | Dùng mô hình chưa duyệt giấy phép | Xem quy tắc 8–10 |
| Lỗi "CUDA out of memory" ở bước Sinh khung | Card không đủ bộ nhớ | Giảm Rộng × cao (bội số 32, ví dụ 512), giảm Số khung sinh (dạng 4n+1: 25, 33, 49), đóng ứng dụng khác dùng GPU |
| Không có ảnh xem trước trong khay việc | ComfyUI chưa bật xem trước | Xem Bước 4, phần bật ảnh xem trước |
| Khay việc không có tiến độ chi tiết, chỉ có thời gian | ComfyUI không cho kết nối theo dõi | Không ảnh hưởng kết quả; cập nhật ComfyUI nếu muốn có |
| Hậu xử lý báo "Nền ảnh không phẳng" | AI vẽ nền loang hoặc có cảnh vật | Sinh lại; thêm "plain flat solid green background" vào mô tả; quái tông xanh thì đổi màu khoá (quy tắc 5) |
| Kiểm tra báo "Viền màu nền ở mép" hoặc "Sót màu nền bên trong" | Quái có màu gần màu nền | Đổi màu khoá sang magenta (quy tắc 5) rồi sinh lại |
| Kiểm tra báo "Mối nối vòng lặp" | Animation lặp không khép vòng | Sinh lại; với máy 24 GB, dùng mẫu first-last-frame |
| Kiểm tra báo "Lệch màu so với ảnh chuẩn" | AI trôi màu | Sinh lại seed khác; nếu thường xuyên, cần LoRA phong cách (báo lại) |
| Cổng 7865 bị chiếm | Đã có Xưởng Quái khác đang chạy | Đóng cửa sổ cũ; công cụ cũng tự thử cổng tiếp theo |

### Tạo lại mẫu từ template chính thức
Dùng khi Xưởng Quái báo mẫu `wan22_5b_i2v` lệch node hoặc input với ComfyUI đang cài.

1. Trong ComfyUI mở template **Wan 2.2 5B Video Generation**.
2. Thay node lưu video ở cuối bằng node **Save Image** (lưu chuỗi PNG), nối vào đầu ra của VAE Decode.
3. Chạy thử một lần cho chắc.
4. Menu **Workflow → Export (API)** → lưu thành `wan22_5b_api.json` trong thư mục `vs-monster-forge`.
5. Mở Command Prompt trong thư mục `vs-monster-forge`:
   ```
   python plugin\skills\asset-gen\scripts\comfy_local.py templatize --workflow wan22_5b_api.json --name wan22_5b_i2v --rule NUM_FRAMES=4n+1 --rule WIDTH=multiple_of_32 --rule HEIGHT=multiple_of_32 --min-vram 8 --out plugin\skills\asset-gen\workflows\wan22_5b_i2v.template.json
   python plugin\skills\asset-gen\scripts\comfy_local.py validate --template plugin\skills\asset-gen\workflows\wan22_5b_i2v.template.json
   ```
6. `validate` báo OK thì chạy thử lại trong Xưởng Quái. Chi tiết: `plugin\skills\asset-gen\workflows\README.md`.

---

## 7. Dùng bằng dòng lệnh

Cho dev, hoặc để Claude Code chạy tự động. Chạy trong thư mục `vs-monster-forge`:
```
:: kiểm máy, mô hình, mẫu nào chạy được (tự dò cổng 8188 rồi 8000)
python plugin\skills\asset-gen\scripts\comfy_local.py doctor

:: lấy tên animation, nhịp, sự kiện từ Spine
python plugin\skills\asset-gen\scripts\spine_timing.py D:\spine\lang_dau.json --fps 12 --monster-id mob_lang_dau_01 --out spec.json

:: sinh 2 biến thể, in tiến độ từng bước
python plugin\skills\asset-gen\scripts\comfy_local.py run --template plugin\skills\asset-gen\workflows\wan22_5b_i2v.template.json --input-image ref.png --set PROMPT="idle breathing loop, static camera, plain flat solid green background" --set NUM_FRAMES=49 --seeds 2 --dest raw\mob_lang_dau_01\idle

:: hậu xử lý và kiểm tra
python plugin\skills\asset-gen\scripts\postprocess.py run --spec spec.json --input raw\mob_lang_dau_01 --out out\mob_lang_dau_01 --thresholds plugin\skills\asset-gen\config\qa_thresholds.json
```
Quy trình đầy đủ cho Claude Code nằm trong `plugin/skills/asset-gen/SKILL.md`.

Kiểm thử: `pip install pytest`, rồi `python -m pytest -q app/tests plugin/skills/asset-gen/tests`.

### Định dạng hoạ sĩ 16 khung (thử sinh từ một ảnh)
Quái 58/59/60 trong game dùng **16 PNG 500×500 dáng chốt** + đạn a/b/c, không dùng atlas. Công cụ cho định dạng này nằm ở `plugin\skills\asset-gen\scripts\fbf16\`.

**Việc đang chờ máy có card:** thử Qwen-Image-Edit-2511. Làm theo `plugin/skills/asset-gen/reference/fbf16-tu-1-anh.md` §4. Tài liệu có các bước chuẩn bị, ba chế độ thử, lệnh kiểm và tiêu chí đạt.

Lưu ý:
- Ảnh quái thật lấy từ repo client vào `workspace\fbf16\`. Không commit.
- Mô hình còn `check`: hỏi người dùng trước khi tải và trước khi đổi sang `ok`.

```
:: dựng thư mục làm việc từ bộ hoạ sĩ
python plugin\skills\asset-gen\scripts\fbf16\chuan_bi.py --ma u_minh_ho_phap --goc "<FE>\Monster\58 U Minh Ho Phap\1.png" --dang "<FE>\Monster\60 Vu Ho Phap" --thay 5="<FE>\Monster\59 Xich Ho Phap" --dap-an "<FE>\Monster\58 U Minh Ho Phap"

:: sinh (chưa chạy thử), rồi canh cỡ và kiểm
python plugin\skills\asset-gen\scripts\fbf16\gen_qwen.py u_minh_ho_phap --che-do chu
python plugin\skills\asset-gen\scripts\fbf16\ai_to_fbf16.py workspace\fbf16\u_minh_ho_phap\ai\chu workspace\fbf16\u_minh_ho_phap\ai\chu-500 --seed 1 --goc workspace\fbf16\u_minh_ho_phap\goc.png --chi-tiet 368,240,427,332 --mau 0.6
python plugin\skills\asset-gen\scripts\fbf16\qa16.py workspace\fbf16\u_minh_ho_phap\ai\chu-500 --ref workspace\fbf16\u_minh_ho_phap\goc.png
python plugin\skills\asset-gen\scripts\fbf16\kiem_chu_ky_di.py workspace\fbf16\u_minh_ho_phap\ai\chu-500
```

---

## 8. Phiếu ghi lần chạy đầu

Điền sau lần chạy thử ở [mục 3](#3-chạy-thử-lần-đầu) và gửi lại, để chỉnh cấu hình mặc định và ngưỡng kiểm tra theo máy thật. Ngưỡng hiện tại mới chỉnh trên hình giả lập.

| Mục | Kết quả |
|---|---|
| Card và VRAM (thanh trên của Xưởng Quái) | |
| RAM | |
| ComfyUI: Portable hay Desktop, phiên bản | |
| Template chính thức (Bước 7) chạy được không, mất bao lâu | |
| Mẫu `wan22_5b_i2v`: chạy được ngay hay phải tạo lại | |
| Rộng × cao, số khung sinh đã dùng | |
| Thời gian một biến thể (xem khay việc) | |
| VRAM cao nhất lúc sinh (Task Manager → Performance → GPU) | |
| Kết quả kiểm tra của biến thể (Đạt / Cảnh báo / Lỗi, lỗi gì) | |
| Nhận xét: giữ phong cách không, có trôi chi tiết không | |
| Ảnh chụp bảng khung sau hậu xử lý | (đính kèm) |
