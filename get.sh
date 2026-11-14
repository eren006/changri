#!/usr/bin/env bash
# 长日bot 一键安装（Linux，全新服务器也行）。
#
# 在服务器上只跑这一条：
#   curl -fsSL https://raw.githubusercontent.com/eren006/changri/main/get.sh | sudo bash
#
# 它会：装 git/curl → 把代码拉到 /opt/changri-bot → 交给 install.sh（装 Python、装依赖、注册开机自启）。
# 机器人的 AppID / AppSecret 不在这里填，装好后在网页后台的安装向导里填。
#
# 可用环境变量：
#   CHANGRI_REPO   代码仓库地址（多个用空格隔开，按顺序试，哪个能连上用哪个）
#   INSTALL_DIR    安装目录，默认 /opt/changri-bot
#   BRANCH         分支，默认 main
set -eu

# ↓↓↓ 仓库地址。国内服务器连 GitHub 可能很慢；以后建了 Gitee 镜像，把它写在前面，空格隔开即可。↓↓↓
DEFAULT_REPOS="https://github.com/eren006/changri.git"
# ↑↑↑

REPOS="${CHANGRI_REPO:-$DEFAULT_REPOS}"
INSTALL_DIR="${INSTALL_DIR:-/opt/changri-bot}"
BRANCH="${BRANCH:-main}"

say() { printf '\n>>> %s\n' "$*"; }
die() { printf '\n出错了：%s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "请用 root 运行：curl -fsSL <地址>/get.sh | sudo bash"
case "$REPOS" in *你的账号*) die "这个脚本还没填仓库地址。请编辑 get.sh 顶部的 DEFAULT_REPOS，或运行时加 CHANGRI_REPO=仓库地址。" ;; esac

# 已经装过：不重新下载（后台「更新」负责升级，重新拉代码会和它打架），只把 install.sh 再跑一遍，它是可重复运行的
if [ -f "$INSTALL_DIR/install.sh" ]; then
  say "$INSTALL_DIR 已经装过了，重新运行安装（数据和设置不会动）。要升级请用网页后台的「更新」。"
  cd "$INSTALL_DIR" && exec bash ./install.sh
fi

say "准备 git 和 curl"
if ! command -v git >/dev/null 2>&1 || ! command -v curl >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y && apt-get install -y git curl ca-certificates
  elif command -v dnf >/dev/null 2>&1; then dnf install -y git curl ca-certificates
  elif command -v yum >/dev/null 2>&1; then yum install -y git curl ca-certificates
  elif command -v apk >/dev/null 2>&1; then apk add --no-cache git curl ca-certificates bash
  else die "不认识这个系统的包管理器，请先手动安装 git 和 curl 再重试。"; fi
fi
command -v git >/dev/null 2>&1 || die "git 没装上，请检查服务器能不能联网。"

say "下载代码到 $INSTALL_DIR"
mkdir -p "$(dirname "$INSTALL_DIR")"
OK=""
for url in $REPOS; do
  echo "尝试：$url"
  rm -rf "$INSTALL_DIR"
  # 20 秒连不上、或速度长时间接近 0 就放弃这个地址，换下一个
  if GIT_TERMINAL_PROMPT=0 git -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=20 \
       clone --depth 1 -b "$BRANCH" "$url" "$INSTALL_DIR"; then
    OK=1; break
  fi
  echo "这个地址没成功，换下一个……"
done
[ -n "$OK" ] || die "所有仓库地址都下载失败。请检查服务器网络，或手动把代码解压到 $INSTALL_DIR 后运行 sudo ./install.sh。"
rm -rf "$INSTALL_DIR/.git"   # 之后升级走后台「更新」，不用 git，留着只会占地方

[ -f "$INSTALL_DIR/install.sh" ] || die "仓库里没找到 install.sh，仓库地址可能填错了。"
cd "$INSTALL_DIR"
exec bash ./install.sh
