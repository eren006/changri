@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title 长日bot

rem ===== 1. 找 Python 3.10+（全新的服务器会自动安装）=====
set "PY="
call :findpy
if defined PY goto have_py

echo 没找到 Python，正在自动安装（需要联网，约 1 分钟）……
where winget >nul 2>nul && winget install -e --id Python.Python.3.12 --scope machine --silent --accept-package-agreements --accept-source-agreements
call :findpy
if defined PY goto have_py

set "PYEXE=%TEMP%\python-installer.exe"
for %%U in (
  "https://registry.npmmirror.com/-/binary/python/3.12.7/python-3.12.7-amd64.exe"
  "https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe"
) do (
  if not exist "%PYEXE%" powershell -NoProfile -Command "try { [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing -Uri %%~U -OutFile '%PYEXE%' } catch {}" >nul 2>nul
)
if exist "%PYEXE%" (
  "%PYEXE%" /quiet InstallAllUsers=1 PrependPath=1 Include_test=0
  del "%PYEXE%" >nul 2>nul
)
call :findpy
if defined PY goto have_py
echo 自动安装 Python 失败。请手动安装 Python 3.12：https://www.python.org/downloads/
echo 安装时勾选 "Add python.exe to PATH"，装完再双击这个文件。
pause
exit /b 1

:have_py
rem ===== 2. 虚拟环境和全部依赖 =====
if not exist venv\Scripts\python.exe (
  echo 创建虚拟环境……
  %PY% -m venv venv || ( echo 创建虚拟环境失败 & pause & exit /b 1 )
)
if not exist .env copy .env.example .env >nul

if not exist venv\.deps_ok (
  echo 安装依赖（需要联网，第一次会花一两分钟）……
  venv\Scripts\python -m pip install -q --upgrade pip >nul 2>nul
  if defined PIP_INDEX (
    venv\Scripts\python -m pip install -q --default-timeout 30 -i %PIP_INDEX% -r requirements.txt
  ) else (
    venv\Scripts\python -m pip install -q --default-timeout 25 -r requirements.txt
    if errorlevel 1 (
      echo 官方源不通，换国内镜像重试……
      venv\Scripts\python -m pip install -q --default-timeout 30 -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
    )
  )
  if errorlevel 1 ( echo 依赖安装失败：请检查这台服务器能不能上网。 & pause & exit /b 1 )
  venv\Scripts\python -c "import nonebot, flask, a2wsgi, PIL, apscheduler" 2>nul
  if errorlevel 1 ( echo 依赖装得不完整，请重新双击。 & pause & exit /b 1 )
  echo ok> venv\.deps_ok
)

set "PORT=5091"
for /f "tokens=2 delims==" %%a in ('findstr /b "PORT=" .env') do set "PORT=%%a"
echo.
echo 机器人只往外连 QQ，设置平台只在本机打开：不需要开放防火墙端口。
echo 设置平台：用浏览器打开 http://服务器IP:%PORT%（本机可用 http://127.0.0.1:%PORT%）；首次设置的验证码在 data\.setup_code
echo 关闭这个窗口就会停止机器人。想开机自动运行：右键「以管理员身份运行」install_autostart.bat
echo.

:loop
venv\Scripts\python bot.py
echo 程序退出了，5 秒后重新启动。关闭窗口结束。
timeout /t 5 /nobreak >nul
goto loop

:findpy
set "PY="
if exist "runtime\python.exe" ( set "PY=runtime\python.exe" & goto :eof )
for %%C in ("py -3.13" "py -3.12" "py -3.11" "py -3.10" "python") do (
  if not defined PY (
    %%~C -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul && set "PY=%%~C"
  )
)
if not defined PY (
  for %%P in ("%ProgramFiles%\Python313\python.exe" "%ProgramFiles%\Python312\python.exe" "%ProgramFiles%\Python311\python.exe" "%ProgramFiles%\Python310\python.exe") do (
    if not defined PY if exist %%P set "PY=%%~P"
  )
)
goto :eof
