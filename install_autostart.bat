@echo off
chcp 65001 >nul
cd /d "%~dp0"
net session >nul 2>nul || ( echo 请右键这个文件，选「以管理员身份运行」。 & pause & exit /b 1 )
rem 任务计划：开机后自动运行 start.bat（不需要有人登录）。失败会自动重启由 start.bat 里的循环负责。
schtasks /Create /TN "ChangriBot" /TR "cmd /c \"%~dp0start.bat\"" /SC ONSTART /RU SYSTEM /RL HIGHEST /F
if errorlevel 1 ( echo 创建任务失败 & pause & exit /b 1 )
schtasks /Run /TN "ChangriBot" >nul
netsh advfirewall firewall delete rule name="ChangriPhone" >nul 2>nul
netsh advfirewall firewall add rule name="ChangriPhone" dir=in action=allow protocol=TCP localport=5091,5092 >nul && echo 已在 Windows 防火墙放行 5091（设置平台）和 5092（小手机）。
echo 还需要到云服务商控制台的「安全组」放行 5091 和 5092，再在设置平台「小手机」页点「检查玩家能不能打开」。
echo 已设置为开机自动运行，并已启动。用浏览器打开设置平台即可。
echo 取消开机运行：schtasks /Delete /TN "ChangriBot" /F
pause
