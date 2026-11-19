@echo off
cd /d "%~dp0"
net session >nul 2>nul || ( echo 请右键这个文件，选「以管理员身份运行」。 & pause & exit /b 1 )
rem 给长日bot 装 Caddy（一键 HTTPS 用）：下载 caddy.exe、注册成开机自启的 Windows 服务、放行防火墙。重复运行是安全的。
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_caddy.ps1"
pause
