# Kế hoạch chuyển quái từ Spine sang FBF (giữ phong cách ảnh)

Phạm vi: **chỉ quái** (quái thường, tinh anh, boss). Pet và nhân vật giữ Spine.

## 1. Nguyên tắc tương thích – không để server và logic phải đổi
Bản FBF của một con quái phải giữ đúng ba thứ của bản Spine:
1. **Tên animation** (`idle`, `attack_1`…), vì code client gọi phát anim theo tên.
2. **Tên và mốc sự kiện** (`hit`…), vì số sát thương, hiệu ứng trúng đòn và âm thanh bám theo sự kiện.
3. **Thời lượng**, vì nhịp ra đòn của boss (`RM_Boss_Attack`, `BossData.UseSkill`) có thể đang dựa vào độ dài anim.

`scripts/spine_timing.py` lấy cả ba từ Spine JSON. `postprocess.py` sẽ báo **lỗi** nếu thời lượng hoặc mốc sự kiện lệch quá 1 khung.

> Cần xác minh khi đọc được repo client: nơi nghe sự kiện Spine (`AnimationState.Event`), nơi dùng `Animation.Duration`, và có chỗ nào đọc vị trí xương (bone) để đặt hiệu ứng hay điểm bắn không. Vị trí xương **không còn** khi chuyển sang FBF, nên phải thay bằng điểm neo khai báo tay theo từng khung.

## 2. Phía client (Unity)
- Thêm lớp trừu tượng, ví dụ `IMonsterAnimator`: `Play(name, loop)`, sự kiện `OnEvent(name)`, `GetDuration(name)`, `SetFlip`, `SetTint`.
  Viết hai bản cài đặt: `SpineMonsterAnimator` (bọc code hiện có) và `FbfMonsterAnimator` (đọc manifest `dpvs.fbf/1`). Các chỗ đang gọi phát anim chỉ làm việc qua lớp này.
- Thêm trường `render_type: spine | fbf` cho từng quái trong dữ liệu. Nhờ vậy chuyển được **từng con**, và rollback chỉ cần sửa dữ liệu.
- Pivot đặt ở chân và ô có kích thước đều, nên collider (BoxCollider2D của Corgi) giữ được như cũ. `bbox` trong manifest giúp chỉnh collider nếu cần.
- Hiệu ứng nháy khi trúng đòn: Spine đang đổi màu skeleton; FBF đổi `SpriteRenderer.color` hoặc dùng material flash.
- Quái chỉ vẽ một hướng (quay phải), lật bằng `scale.x = -1`.

## 3. Bộ nhớ và dung lượng tải
FBF tốn texture hơn Spine nhiều lần, vì mỗi khung là một ảnh đầy đủ thay vì dùng chung bộ phận.

Ước tính một quái thường cao 160px, khoảng 25–33 khung, ô khoảng 300×180 → atlas khoảng 1216×1232:

| Định dạng | Dung lượng |
|---|---|
| RGBA32 (không nén) | ≈ 6,0 MB |
| ETC2 RGBA8 | ≈ 1,5 MB |
| ASTC 6×6 | ≈ 0,7 MB |

- Mobile: nén ASTC, tắt mipmap cho sprite.
- Gom atlas theo map và tải bằng Addressables; không nạp sẵn mọi quái.
- Ngân sách: quái thường ≤ 2048px, boss ≤ 4096px. Boss nhiều khung có thể phải tách 2 atlas.
- **Phải đo trên máy Android yếu nhất đang hỗ trợ trong pilot.**

## 4. Giữ phong cách: dataset LoRA từ chính Spine
- Xuất 30–50 ảnh render từ **nhiều con quái khác nhau**, nhiều tư thế (idle, đánh, trúng đòn), nền một màu, độ phân giải đồng đều.
- Mỗi ảnh có một caption ngắn mô tả nội dung. Không mô tả phong cách; phong cách là thứ LoRA phải tự học.
- Giữ riêng 5 ảnh không đưa vào huấn luyện, dùng để so trước/sau.
- Render Spine có kiểu "cắt ghép"; LoRA học đúng kiểu đó. Đây là điều mong muốn, vì mục tiêu là giữ phong cách.
- Ảnh và quái đều là tài sản của studio, nên giấy phép dataset không phải lo. Riêng mô hình nền vẫn phải là loại cho phép thương mại (`workflows/registry.json`).

## 5. Pilot trước khi mở rộng
Mọi bước chạy trên máy nhà (ComfyUI cục bộ, không thuê cloud).

Làm 3 con:
- 1 quái thường cũ, làm **cả hai đường** để so: xuất Spine (không AI) và AI vẽ lại.
- 1 quái tinh anh mới (AI sinh từ ảnh gốc).
- 1 boss.

Đo các chỉ số sau:

| Chỉ số | Mục tiêu đề xuất |
|---|---|
| Tỉ lệ animation đạt QA ngay vòng 1 | ≥ 50% |
| Số vòng sinh trung bình mỗi anim | ≤ 2 |
| Thời gian người làm cho một quái (chọn, duyệt, sửa tay) | ≤ 1 ngày công |
| Thời gian GPU mỗi quái (máy nhà) | Ghi lại để biết một máy làm được bao nhiêu quái/ngày |
| FPS và bộ nhớ trên máy yếu, map đông quái nhất | Không tụt so với bản Spine |
| Hoạ sĩ chấm "đúng phong cách" | Đạt cả 3 con |

Đạt thì mở rộng theo map; không đạt thì giữ Spine cho nhóm quái đó.

## 6. Rủi ro
- **Trôi chi tiết giữa các khung** (vũ khí, hoa văn đổi hình). QA tự động chỉ bắt được trôi màu và nhảy hình, nên vẫn cần người xem.
- **Mất điểm gắn hiệu ứng theo xương.** Cần bảng điểm neo theo khung nếu client đang dùng.
- **Bộ nhớ trên mobile** (mục 3).
- **Giấy phép mô hình thay đổi theo thời gian.** Kiểm lại model card mỗi lần nâng phiên bản.
- Câu hỏi IP Đấu Phá còn mở (tài liệu bàn giao, mục 7) áp dụng cho cả asset sinh bằng AI.
