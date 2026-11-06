# Spec quái FBF (`specs/<monster_id>.json`)

| Trường | Bắt buộc | Mặc định | Ý nghĩa |
|---|---|---|---|
| `monster_id` | ✔ | – | `a-z0-9_`, trùng mã quái trong DB/content |
| `display_name` | | `null` | Tên hiển thị, chỉ để đọc |
| `target_height_px` | ✔ | – | Chiều cao quái ở tư thế idle trong ảnh xuất (px) |
| `pixels_per_unit` | | `null` | Ghi vào manifest cho Unity |
| `anchor.x` | | `feet` | `feet` = tâm vùng chân · `body` = trọng tâm thân (quái bay) |
| `anchor.y` | | `ground` | `ground` = mép dưới chân · `body` = trọng tâm thân |
| `scale_mode` | | `global` | `global`: một tỉ lệ cho mọi anim, lấy theo `scale_reference` · `first_frame`: mỗi anim scale theo khung đầu của nó · `fixed`: dùng `scale` |
| `scale_reference` | | anim đầu tiên | Anim dùng để tính tỉ lệ khi `global` |
| `padding_px` | | `4` | Lề trong suốt quanh hình trong mỗi ô |
| `background.mode` | | `auto` | `auto`: có alpha thì dùng alpha, không thì tự đoán màu nền từ viền · `alpha` · `key` (cần `color: [r,g,b]`) |
| `background.spill` | | `true` | Khử ám màu nền ở mép (chỉ áp dụng với màu khoá một kênh trội) |
| `background.keep_islands` | | `false` | `true` = giữ các mảng rời nhỏ (tia lửa, hạt bay quanh quái) |
| `style_reference` | | khung đầu anim đầu | Ảnh chuẩn để đo trôi màu |
| `palette` | | – | Danh sách `#RRGGBB`; nếu có thì kiểm % pixel trong bảng màu (pixel art) |
| `animations` | ✔ | – | Object, **thứ tự giữ nguyên** trong atlas |

## Mỗi animation

| Trường | Bắt buộc | Ý nghĩa |
|---|---|---|
| `frames`, `fps` | ✔ | Số khung xuất và tốc độ phát |
| `loop` | | Anim lặp: bật kiểm mối nối và tự tìm cửa sổ lặp khi nguồn là video |
| `stabilize` | | `lock` / `detrend` / `none`, hoặc `{"x": …, "y": …}`. Mặc định theo tên: idle/hurt/stun → `lock`, run/walk → `detrend`, còn lại → `none` |
| `events` | | `{"hit": 5}` hoặc `{"hit": [3, 6]}` – chỉ số khung (từ 0) |
| `target_duration_ms` | | Thời lượng của bản cũ. Lệch quá 1 khung → QA lỗi |
| `target_events_ms` | | Mốc sự kiện của bản cũ, cùng dạng `events` |
| `pick` | | Chỉ số khung nguồn chọn tay, ghi đè cách chọn tự động |
| `trim` | | `[đầu, cuối)` – chỉ chọn khung trong khoảng này của nguồn |

## Ý nghĩa `stabilize`
- `lock`: đặt điểm neo của mọi khung trùng nhau. Hết rung hoàn toàn, nhưng xoá luôn chuyển động tịnh tiến. Dùng cho đứng yên, trúng đòn.
- `detrend`: bỏ phần trôi tuyến tính (camera trôi), giữ dao động. Dùng cho chạy tại chỗ.
- `none`: giữ nguyên chuyển động so với khung đầu. Dùng cho lao tới, nhảy, ngã. Đòi hỏi nguồn có camera đứng yên.

## Manifest xuất ra (`dpvs.fbf/1`)
- `cell`, `pivot` (px tính từ góc trên-trái ô; `x_norm`/`y_norm` theo quy ước Unity gốc dưới-trái).
- `animations[].frames[]`: `rect` (gốc trên-trái), `rect_unity` (gốc dưới-trái), `bbox` vùng có hình, `source` (file + chỉ số khung nguồn).
- `animations[].events[]`: `frame`, `time_ms`, `target_ms`.
- `provenance`: nội dung `provenance.json` + sha256 từng file nguồn.
- `postprocess`: phiên bản script, sha256 spec, ngưỡng đã dùng.
- `qa`: kết quả tổng và từng anim.
