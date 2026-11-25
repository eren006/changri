@echo off
rem 内部用：双击 Start-Changri.bat 时，看门进程（platform/winstart.py）用管理员身份运行它来设开机自启。参数：安装目录。
cd /d "%~1" || exit /b 3
call "%~1\_env.bat" || exit /b 3
"%PY%" -m changri service install
