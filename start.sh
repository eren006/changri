#!/usr/bin/env bash
# 前台运行长日bot（挂掉自动重启）。第一次运行会自己准备好环境并生成 .env。
# 全新的服务器请用  sudo ./install.sh  （会连 Python 一起装好，并设置开机自启）。
set -u
cd "$(dirname "$0")"
. ./lib.sh

PY="$(find_python || true)"
if [ -z "$PY" ]; then
  if [ "$(id -u)" -eq 0 ]; then install_system_python && PY="$(find_python)" || exit 1
  else echo "这台服务器还没有 Python 3.10 以上。请运行：sudo ./install.sh（会自动装好）"; exit 1; fi
fi
prepare_env "$PY" || exit 1
check_outbound

PORT="$(grep -E '^PORT=' .env | head -1 | cut -d= -f2)"
echo "设置平台：用浏览器打开 http://服务器IP:${PORT:-5091}（本机可用 http://127.0.0.1:${PORT:-5091}）；首次设置的验证码在 data/.setup_code"
while true; do
  venv/bin/python bot.py
  echo "程序退出了（$?），5 秒后重新启动。按 Ctrl+C 结束。"
  sleep 5
done
