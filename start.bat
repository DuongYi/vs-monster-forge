@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Xưởng Quái
set "PY=python"
%PY% --version >nul 2>nul || set "PY=py -3"
%PY% --version >nul 2>nul || (
  echo Chưa có Python 3.10 trở lên. Tải tại https://www.python.org/downloads/ và đánh dấu "Add python.exe to PATH" khi cài.
  pause
  exit /b 1
)
%PY% -c "import numpy, cv2, PIL, scipy" >nul 2>nul || (
  echo Lần đầu chạy: đang cài thư viện cần thiết...
  %PY% -m pip install -r requirements.txt || (
    echo Cài thư viện thất bại. Kiểm tra mạng rồi chạy lại.
    pause
    exit /b 1
  )
)
%PY% app\server.py %*
pause
