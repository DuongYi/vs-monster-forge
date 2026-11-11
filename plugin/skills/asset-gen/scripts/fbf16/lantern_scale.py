"""Ước tỉ lệ ảnh AI / ảnh gốc bằng cách dò một chi tiết cứng (vd đèn lồng) ở nhiều cỡ."""
import numpy as np, cv2
def find_scale(ai_rgb, tpl_rgba, smin=0.8, smax=2.6, step=0.02):
    g = cv2.cvtColor(np.ascontiguousarray(ai_rgb[..., :3]), cv2.COLOR_RGB2GRAY).astype(np.float32)
    ge = cv2.Canny(g.astype(np.uint8), 60, 140).astype(np.float32); ge = cv2.GaussianBlur(ge, (5, 5), 0)
    t = cv2.cvtColor(np.ascontiguousarray(tpl_rgba[..., :3]), cv2.COLOR_RGB2GRAY)
    best = (-1, None, None)
    for s in np.arange(smin, smax, step):
        w, h = int(t.shape[1] * s), int(t.shape[0] * s)
        if w < 12 or h < 12 or w >= g.shape[1] or h >= g.shape[0]: continue
        ts = cv2.resize(t, (w, h), interpolation=cv2.INTER_AREA)
        te = cv2.GaussianBlur(cv2.Canny(ts, 60, 140).astype(np.float32), (5, 5), 0)
        r = cv2.matchTemplate(ge, te, cv2.TM_CCOEFF_NORMED)
        _, mv, _, ml = cv2.minMaxLoc(r)
        if mv > best[0]: best = (float(mv), float(s), ml)
    return best
