@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 自检：端口、能不能连上 QQ、公网 IP。只看不改。
call "%~dp0_env.bat" || ( echo 找不到程序文件，请重新解压安装包。 & pause & exit /b 1 )
"%PY%" -m changri doctor
pause
