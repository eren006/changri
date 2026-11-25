#!/bin/sh
# 长日bot：在本机防火墙放行端口。由 changri service install 写入，root 所有；
# /etc/sudoers.d/changri-bot 只允许 changri 用户免密运行这一个脚本。只接受 80、443 和 1024-65535 的端口号。
#   changri-firewall 5091 5092           放行，输出放行了的防火墙名字（ufw / firewalld / iptables），都没开输出空行
#   changri-firewall --check 5091 5092   只查不改，输出还被拦着的端口，都通输出空行
# ufw、firewalld 都没开时看裸 iptables：INPUT 默认丢弃，或者有一条全拦的 REJECT / DROP（Oracle Cloud 的 Ubuntu 镜像就是这样），
# 才在最前面插一条放行，并尽量存成重启后还在。iptables-nft 也走这条路；直接写 nft 规则的不管。
check=""
[ "$1" = --check ] && { check=1; shift; }
[ $# -ge 1 ] || exit 2
for p in "$@"; do
  case "$p" in ''|*[!0-9]*) exit 2 ;; esac
  [ "$p" = 80 ] || [ "$p" = 443 ] || { [ "$p" -ge 1024 ] && [ "$p" -le 65535 ]; } || exit 2
done

ufw_on() { command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; }
firewalld_on() { command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; }
iptables_strict() {
  command -v iptables >/dev/null 2>&1 || return 1
  iptables -S INPUT 2>/dev/null | grep -Eq -- '^-P INPUT (DROP|REJECT)|^-A INPUT( -i [^ ]+)? -j (REJECT|DROP)'
}
ipt_has() { iptables -C INPUT -p tcp --dport "$1" -j ACCEPT >/dev/null 2>&1; }
ipt_save() {
  if command -v netfilter-persistent >/dev/null 2>&1; then netfilter-persistent save >/dev/null 2>&1
  elif [ -f /etc/sysconfig/iptables ]; then iptables-save > /etc/sysconfig/iptables 2>/dev/null
  elif [ -d /etc/iptables ]; then iptables-save > /etc/iptables/rules.v4 2>/dev/null
  fi
}

if [ -n "$check" ]; then
  blocked=""
  if ufw_on && ! ufw status verbose 2>/dev/null | grep -q "allow (incoming)"; then
    for p in "$@"; do ufw status 2>/dev/null | grep -Eq "^$p(/tcp)?[[:space:]].*ALLOW" || blocked="$blocked $p"; done
  fi
  if firewalld_on; then
    for p in "$@"; do firewall-cmd --query-port="$p/tcp" >/dev/null 2>&1 || blocked="$blocked $p"; done
  fi
  if ! ufw_on && ! firewalld_on && iptables_strict; then
    for p in "$@"; do ipt_has "$p" || blocked="$blocked $p"; done
  fi
  [ -n "$blocked" ] && printf '%s\n' $blocked | sort -un | tr '\n' ' ' | sed 's/ $//'
  echo
  exit 0
fi

done=""
if ufw_on; then
  for p in "$@"; do ufw allow "$p/tcp" >/dev/null 2>&1; done
  done="ufw"
fi
if firewalld_on; then
  for p in "$@"; do firewall-cmd --permanent --add-port="$p/tcp" >/dev/null 2>&1; done
  firewall-cmd --reload >/dev/null 2>&1
  done="$done firewalld"
fi
if [ -z "$done" ] && iptables_strict; then
  for p in "$@"; do ipt_has "$p" || iptables -I INPUT -p tcp --dport "$p" -j ACCEPT >/dev/null 2>&1; done
  ipt_save
  done="iptables"
fi
echo $done
