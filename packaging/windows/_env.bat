@rem 内部用（别的 .bat 会 call 它）：按 current 和 RUNTIME 找到当前版本要用的 Python。
@rem 找不到时先说清楚缺的是哪个文件，调用方再打印「请重新解压安装包」。
set "CHANGRI_ROOT=%~dp0"
set "VER="
set "RT="
if not exist "%CHANGRI_ROOT%current" goto no_current
set /p VER=<"%CHANGRI_ROOT%current"
if not defined VER goto no_current
if not exist "%CHANGRI_ROOT%versions\%VER%\RUNTIME" goto no_runtime_id
set /p RT=<"%CHANGRI_ROOT%versions\%VER%\RUNTIME"
if not defined RT goto no_runtime_id
set "PY=%CHANGRI_ROOT%runtime\%RT%\python.exe"
set "PYTHONPATH=%CHANGRI_ROOT%versions\%VER%"
set "PYTHONNOUSERSITE=1"
set "PYTHONUTF8=1"
if not exist "%PY%" goto no_python
exit /b 0
:no_current
echo 缺少文件：%CHANGRI_ROOT%current
echo 这个 .bat 要放在解压出来的长日bot 文件夹里运行，不能单独拷出来，也不能在压缩包里直接双击。
exit /b 1
:no_runtime_id
echo 缺少文件：%CHANGRI_ROOT%versions\%VER%\RUNTIME
exit /b 1
:no_python
echo 缺少文件：%PY%
echo 可能被杀毒软件隔离了：去杀毒软件的隔离区恢复它，并把长日bot 文件夹加入信任。
exit /b 1
