#!/usr/bin/env sh
# Xưởng Quái – khởi động trên Linux/macOS
cd "$(dirname "$0")" || exit 1
PY=python3
command -v "$PY" >/dev/null 2>&1 || { echo "Chưa có python3 (cần 3.10+)."; exit 1; }
"$PY" -c "import numpy, cv2, PIL, scipy" 2>/dev/null || {
  echo "Lần đầu chạy: đang cài thư viện cần thiết..."
  "$PY" -m pip install -r requirements.txt || exit 1
}
exec "$PY" app/server.py "$@"
