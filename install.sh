#!/usr/bin/env bash
# Linux 一键安装（全新服务器也行）：装 Python、装全部依赖、注册成开机自启的服务并启动。
# 用法：在解压出来的目录里运行   sudo ./install.sh
set -eu
cd "$(dirname "$0")"
DIR="$(pwd)"
. ./lib.sh
[ "$(id -u)" -eq 0 ] || { echo "请用 sudo 运行：sudo ./install.sh"; exit 1; }
command -v systemctl >/dev/null 2>&1 || { echo "这台机器没有 systemd，请改用 ./start.sh 前台运行。"; exit 1; }
USER_NAME="${SUDO_USER:-root}"
[ "$USER_NAME" = "root" ] || id "$USER_NAME" >/dev/null 2>&1 || USER_NAME=root

PY="$(find_python || true)"
if [ -z "$PY" ]; then install_system_python || exit 1; PY="$(find_python)"; fi
echo "使用 Python：$($PY --version 2>&1)"

chown -R "$USER_NAME":"$(id -gn "$USER_NAME")" "$DIR" 2>/dev/null || true
# 以服务运行的那个用户把环境和依赖准备好；失败就停在这里，不会装出一个坏服务
sudo -u "$USER_NAME" -H bash -c ". ./lib.sh && prepare_env '$PY'"
check_outbound

cat > /etc/systemd/system/changri-bot.service <<UNIT
[Unit]
Description=长日bot（机器人 + 设置平台）
After=network-online.target
Wants=network-online.target

[Service]
User=$USER_NAME
WorkingDirectory=$DIR
Environment=PYTHONUNBUFFERED=1
ExecStart=$DIR/venv/bin/python bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now changri-bot
sleep 5
PORT="$(grep -E '^PORT=' .env | head -1 | cut -d= -f2)"
if systemctl is-active changri-bot >/dev/null; then
  echo
  echo "已启动。先在浏览器打开设置平台，设置主账号。"
  # 小手机端口（玩家从外网打开）：放行本机防火墙；云服务商的「安全组」要在网页控制台里放行，程序碰不到
  PHONE_PORT=5092
  ADMIN_PORT="$(grep -E '^PORT=' .env | head -1 | cut -d= -f2)"; ADMIN_PORT="${ADMIN_PORT:-5091}"
  if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then ufw allow ${PHONE_PORT}/tcp >/dev/null && ufw allow ${ADMIN_PORT}/tcp >/dev/null && echo "已在本机防火墙(ufw)放行 ${PHONE_PORT} 端口。"; fi
  if command -v firewall-cmd >/dev/null && firewall-cmd --state >/dev/null 2>&1; then firewall-cmd --permanent --add-port=${PHONE_PORT}/tcp --add-port=${ADMIN_PORT}/tcp >/dev/null && firewall-cmd --reload >/dev/null && echo "已在本机防火墙(firewalld)放行 ${PHONE_PORT} 端口。"; fi
  IP="$(curl -s -m 5 https://api.ipify.org || true)"
  echo "设置平台：http://${IP:-服务器公网IP}:${ADMIN_PORT}　　玩家的小手机：http://${IP:-服务器公网IP}:${PHONE_PORT}/p"
  echo "还需要到云服务商控制台的「安全组」放行 ${ADMIN_PORT} 和 ${PHONE_PORT} 两个端口（程序碰不到云厂商的设置，这一步只能你来点）。"
  SC="$(cat data/.setup_code 2>/dev/null || true)"
  [ -n "$SC" ] && echo "首次打开设置平台要输入验证码：$SC （只有这一次，建好账号后失效）"
  echo "查看日志：journalctl -u changri-bot -f　　重启：sudo systemctl restart changri-bot"
else
  echo "启动失败，查看日志：journalctl -u changri-bot -n 50"; exit 1
fi
