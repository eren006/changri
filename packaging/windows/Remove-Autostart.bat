@echo off
chcp 65001 >nul
cd /d "%~dp0"
net session >nul 2>nul || ( echo 请右键这个文件，选「以管理员身份运行」。 & pause & exit /b 1 )
call "%~dp0_env.bat" || ( echo 找不到程序文件，请重新解压安装包。 & pause & exit /b 1 )
"%PY%" -m changri service uninstall
pause
