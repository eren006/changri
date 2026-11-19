@echo off
cd /d "%~dp0"
rem 一键 HTTPS 的第一步：装 Caddy。装好后回设置平台「HTTPS」页点「开通」。
call "%~dp0_env.bat" || ( echo 找不到程序文件，请重新解压安装包。 & pause & exit /b 1 )
call "%~dp0versions\%VER%\changri\scripts\install_caddy.bat"
