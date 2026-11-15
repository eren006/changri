@rem 内部用（别的 .bat 会 call 它）：按 current 和 RUNTIME 找到当前版本要用的 Python。
set "CHANGRI_ROOT=%~dp0"
set "VER="
set "RT="
set /p VER=<"%CHANGRI_ROOT%current"
if not defined VER exit /b 1
set /p RT=<"%CHANGRI_ROOT%versions\%VER%\RUNTIME"
if not defined RT exit /b 1
set "PY=%CHANGRI_ROOT%runtime\%RT%\python.exe"
set "PYTHONPATH=%CHANGRI_ROOT%versions\%VER%"
set "PYTHONNOUSERSITE=1"
set "PYTHONUTF8=1"
if not exist "%PY%" exit /b 1
exit /b 0
