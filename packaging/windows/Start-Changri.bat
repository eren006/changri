@echo off
setlocal
cd /d "%~dp0"
title 长日bot
rem 双击运行：启动长日bot（机器人 + 设置平台 + 小手机），会自动打开浏览器。
rem 还没设开机自启时，会先问一次「是否允许此应用对你的设备进行更改」：点「是」就设好开机自启、交给后台运行，这个窗口自己关掉；
rem 点「否」就在这个窗口里运行，关掉窗口就停止（细节见 changri/platform/winstart.py）。
if /i "%~1"=="--service" set "CHANGRI_SERVICE=1"
:loop
call "%~dp0_env.bat" || goto broken
"%PY%" -m changri supervise
set "CODE=%errorlevel%"
if "%CODE%"=="10" goto loop
if "%CODE%"=="0" goto end
echo 长日bot 退出了（%CODE%），5 秒后重新启动。关掉这个窗口就会停止。
rem 开机自启时没有窗口，timeout 用不了，用 ping 等 5 秒
ping -n 6 127.0.0.1 >nul
goto loop
:broken
echo 找不到程序文件，安装包可能没解压完整。请重新下载解压，或把这个窗口截图发给维护的人。
if not defined CHANGRI_SERVICE pause
exit /b 1
:end
endlocal
