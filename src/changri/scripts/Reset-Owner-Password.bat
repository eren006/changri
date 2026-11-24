@echo off
cd /d "%~dp0"
rem 主账号忘了密码时用：随机设一个新密码并显示出来，旧密码立刻失效。其他管理员的账号不受影响。
call "%~dp0_env.bat" || ( echo 找不到程序文件，请重新解压安装包。 & pause & exit /b 1 )
"%PY%" -m changri reset-owner
pause
