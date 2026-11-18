#!/bin/sh
# 长日bot：在本机防火墙放行端口。由 changri service install 写入，root 所有；
# /etc/sudoers.d/changri-bot 只允许 changri 用户免密运行这一个脚本。只接受 80、443 和 1024-65535 的端口号。
[ $# -ge 1 ] || exit 2
for p in "$@"; do
  case "$p" in ''|*[!0-9]*) exit 2 ;; esac
  [ "$p" = 80 ] || [ "$p" = 443 ] || { [ "$p" -ge 1024 ] && [ "$p" -le 65535 ]; } || exit 2
done
done=""
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  for p in "$@"; do ufw allow "$p/tcp" >/dev/null 2>&1; done
  done="ufw"
fi
if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
  for p in "$@"; do firewall-cmd --permanent --add-port="$p/tcp" >/dev/null 2>&1; done
  firewall-cmd --reload >/dev/null 2>&1
  done="$done firewalld"
fi
echo $done
