"""Kiểm chu kỳ đi (khung 3-8) có thật sự bước không: đo độ chồng hình vùng chân
(25 % dưới của hình) giữa các khung đi kề nhau, kể cả 8 -> 3.
Hoạ sĩ 58/59/60: TB 0,41-0,50, max 0,57 (bỏ cặp 60/4=5 trùng). FLUX lượt 2: TB 0,74.
python kiem_chu_ky_di.py <thư mục 1..16.png> [--tb 0.55] [--max 0.65]
Thoát mã 2 nếu không đạt."""
import sys
import numpy as np
from PIL import Image


def chan(d, i):
    a = np.asarray(Image.open(f"{d}/{i}.png").convert("RGBA"))[..., 3] > 32
    ys = np.where(a.any(1))[0]
    y0, y1 = ys[0], ys[-1]
    a = a.copy()
    a[: int(y1 - (y1 - y0) * 0.25)] = False
    return a


def main():
    d = sys.argv[1]
    tb_max = float(sys.argv[sys.argv.index("--tb") + 1]) if "--tb" in sys.argv else 0.55
    cap_max = float(sys.argv[sys.argv.index("--max") + 1]) if "--max" in sys.argv else 0.65
    cap = list(zip(range(3, 9), list(range(4, 9)) + [3]))
    v = []
    for a, b in cap:
        x, y = chan(d, a), chan(d, b)
        v.append((x & y).sum() / max((x | y).sum(), 1))
        print(f"{a}->{b}: {v[-1]:.2f}" + ("  <- gần như đứng yên" if v[-1] > cap_max else ""))
    tb = float(np.mean(v))
    ok = tb <= tb_max and max(v) <= cap_max
    print(f"TB {tb:.2f} (trần {tb_max}) · max {max(v):.2f} (trần {cap_max}) -> {'ĐẠT' if ok else 'KHÔNG ĐẠT: chân gần như không đổi giữa các khung đi'}")
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
