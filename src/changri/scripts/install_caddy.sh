#!/usr/bin/env bash
# 给长日bot 装 Caddy（一键 HTTPS 用）。用法：sudo bash install_caddy.sh
# 做的事：装 Caddy（2.10 以上）→ 注册成开机自启的「接口模式」服务（只在本机 127.0.0.1:2019 收配置）→ 本机防火墙放行端口。
# 之后在设置平台的「HTTPS」页点「开通」，配置由程序推给 Caddy，不用再碰命令行。重复运行是安全的。
set -eu
[ "$(id -u)" -eq 0 ] || { echo "请用 sudo 运行：sudo bash $0"; exit 1; }
command -v systemctl >/dev/null 2>&1 || { echo "这台机器没有 systemd，暂不支持一键 HTTPS。"; exit 1; }
MIN="2.10.0"
UNIT=changri-caddy

ver() { caddy version 2>/dev/null | grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+' | head -1 | tr -d v; }
ver_ok() { local v; v="$(ver)"; [ -n "$v" ] && [ "$(printf '%s\n%s\n' "$MIN" "$v" | sort -V | head -1)" = "$MIN" ]; }

# 已经有人用 Caddyfile 跑着自己的网站：不接管，免得弄坏它
if systemctl is-active --quiet caddy 2>/dev/null && [ -f /etc/caddy/Caddyfile ]; then
  if grep -vE '^\s*(#.*)?$' /etc/caddy/Caddyfile | grep -qvE '^\s*(:80 \{|root \* /usr/share/caddy|file_server|\})\s*$'; then
    echo "这台服务器上的 Caddy 已经在跑你自己的网站（/etc/caddy/Caddyfile），为了不弄坏它，脚本不接管。"
    echo "如果你确定要让长日bot 接管，先自己停掉它：sudo systemctl disable --now caddy，再重新运行这个脚本。"
    exit 1
  fi
fi

install_apt() {
  command -v apt-get >/dev/null 2>&1 || return 1
  echo "从 Caddy 官方软件源安装……"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y >/dev/null
  apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl gnupg >/dev/null
  curl -1sLf --max-time 30 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf --max-time 30 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' -o /etc/apt/sources.list.d/caddy-stable.list
  chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -y >/dev/null
  apt-get install -y caddy >/dev/null
}

install_bin() {
  local arch tmp tag
  case "$(uname -m)" in
    x86_64|amd64) arch=amd64 ;;
    aarch64|arm64) arch=arm64 ;;
    *) echo "不认识的 CPU 架构 $(uname -m)"; return 1 ;;
  esac
  tmp="$(mktemp -d)"
  echo "下载 Caddy 程序……"
  if ! curl -fsSL --max-time 120 -o "$tmp/caddy" "https://caddyserver.com/api/download?os=linux&arch=$arch"; then
    echo "官网下载不通，改从 GitHub 下载……"
    tag="$(curl -fsSI --max-time 20 https://github.com/caddyserver/caddy/releases/latest | grep -i '^location:' | grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
    [ -n "$tag" ] || { echo "查不到最新版本号。"; rm -rf "$tmp"; return 1; }
    curl -fsSL --max-time 180 -o "$tmp/c.tgz" "https://github.com/caddyserver/caddy/releases/download/$tag/caddy_${tag#v}_linux_$arch.tar.gz" || { rm -rf "$tmp"; return 1; }
    tar -xzf "$tmp/c.tgz" -C "$tmp" caddy
  fi
  chmod 755 "$tmp/caddy"
  "$tmp/caddy" version >/dev/null 2>&1 || { echo "下载的文件不能运行。"; rm -rf "$tmp"; return 1; }
  install -m 755 "$tmp/caddy" /usr/bin/caddy
  rm -rf "$tmp"
}

if ver_ok; then
  echo "已经装了 Caddy $(ver)。"
else
  install_apt || true
  ver_ok || install_bin || true
  ver_ok || { echo "没能装上 2.10 以上的 Caddy，请检查这台服务器能不能上网。"; exit 1; }
  echo "已装好 Caddy $(ver)。"
fi

id caddy >/dev/null 2>&1 || useradd --system --home /var/lib/caddy --create-home --shell /usr/sbin/nologin caddy 2>/dev/null \
  || useradd --system --home /var/lib/caddy --create-home --shell /sbin/nologin caddy
mkdir -p /var/lib/caddy && chown caddy:caddy /var/lib/caddy

# 软件包自带的 caddy 服务跑的是默认欢迎页（占 80 端口和 2019 接口），换成我们的接口模式服务
if systemctl list-unit-files caddy.service >/dev/null 2>&1; then systemctl disable --now caddy >/dev/null 2>&1 || true; fi
if systemctl list-unit-files caddy-api.service >/dev/null 2>&1; then systemctl disable --now caddy-api >/dev/null 2>&1 || true; fi

cat > /etc/systemd/system/$UNIT.service <<'UNIT'
[Unit]
Description=Caddy（长日bot 的 HTTPS，配置由设置平台通过本机接口下发）
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
User=caddy
Group=caddy
Environment=HOME=/var/lib/caddy
ExecStart=/usr/bin/caddy run --environ --resume
TimeoutStopSec=5s
LimitNOFILE=1048576
AmbientCapabilities=CAP_NET_BIND_SERVICE
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now $UNIT >/dev/null
systemctl restart $UNIT
sleep 2
if ! curl -fs --max-time 5 http://127.0.0.1:2019/config/ >/dev/null; then
  echo "Caddy 没有正常启动，查看日志：journalctl -u $UNIT -n 50"; exit 1
fi

# 本机防火墙：80 和 443（证书验证，有一个能通就行）、5443（设置平台 https）、6092-6199（各机器人小手机的 https）
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null; ufw allow 5443/tcp >/dev/null; ufw allow 6092:6199/tcp >/dev/null
  echo "已在本机防火墙(ufw)放行 80、443、5443、6092-6199。"
fi
if command -v firewall-cmd >/dev/null && firewall-cmd --state >/dev/null 2>&1; then
  firewall-cmd --permanent --add-port=80/tcp --add-port=443/tcp --add-port=5443/tcp --add-port=6092-6199/tcp >/dev/null && firewall-cmd --reload >/dev/null
  echo "已在本机防火墙(firewalld)放行 80、443、5443、6092-6199。"
fi

echo
echo "Caddy 已就绪。回到设置平台的「HTTPS」页刷新，点「开通 HTTPS」。"
echo "别忘了到云服务商的安全组放行 80、443 和 5443（以及小手机的 https 端口，页面上会列出来）。"
echo "80 和 443 被 nginx 占着也能用：页面上会给出让 nginx 转发的几行配置。"
echo "Caddy 的日志：journalctl -u $UNIT -f"
