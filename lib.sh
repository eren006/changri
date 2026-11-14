# 被 start.sh / install.sh 共用的函数。不要单独运行。

# 找一个 3.10 以上的 Python，找到就打印命令名
find_python() {
  local c
  for c in "${PYTHON:-}" python3.13 python3.12 python3.11 python3.10 python3 python; do
    [ -n "$c" ] || continue
    command -v "$c" >/dev/null 2>&1 || continue
    if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
      echo "$c"; return 0
    fi
  done
  return 1
}

# 全新的服务器：自己把 Python、venv、pip 装上（需要 root）
install_system_python() {
  echo "没找到 Python 3.10 以上，正在安装……"
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    apt-get install -y ca-certificates curl python3 python3-venv python3-pip || true
    find_python >/dev/null 2>&1 || apt-get install -y python3.12 python3.12-venv || apt-get install -y python3.11 python3.11-venv || true
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y ca-certificates curl python3.12 python3.12-pip || dnf install -y python3.11 python3.11-pip || dnf install -y python3 python3-pip || true
  elif command -v yum >/dev/null 2>&1; then
    yum install -y ca-certificates curl python3 python3-pip || true
  elif command -v apk >/dev/null 2>&1; then
    apk add --no-cache python3 py3-pip curl ca-certificates || true
  else
    echo "不认识这个系统的包管理器，请手动安装 Python 3.10 或更高版本。"
    return 1
  fi
  find_python >/dev/null 2>&1
}

# 建虚拟环境并装 requirements.txt 里的全部依赖（官方源不通就自动换国内镜像）
prepare_env() {
  local py="$1"
  if [ ! -x venv/bin/python ]; then
    echo "创建虚拟环境……"
    rm -rf venv
    "$py" -m venv venv || { echo "创建虚拟环境失败。Debian/Ubuntu 请先：sudo apt install -y python3-venv"; return 1; }
  fi
  [ -f .env ] || cp .env.example .env
  if [ ! -f venv/.deps_stamp ] || [ requirements.txt -nt venv/.deps_stamp ]; then
    echo "安装依赖（需要联网，第一次会花一两分钟）……"
    venv/bin/python -m pip install -q --upgrade pip >/dev/null 2>&1 || true
    if [ -n "${PIP_INDEX:-}" ]; then
      venv/bin/pip install -q --default-timeout 30 -i "$PIP_INDEX" -r requirements.txt || return 1
    else
      venv/bin/pip install -q --default-timeout 25 -r requirements.txt \
        || { echo "官方源不通，换国内镜像重试……"; venv/bin/pip install -q --default-timeout 30 -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt; } \
        || { echo "依赖安装失败：请检查这台服务器能不能上网。"; return 1; }
    fi
    touch venv/.deps_stamp
  fi
  # 装完再确认一遍：关键模块都能导入
  venv/bin/python -c 'import nonebot, flask, a2wsgi, PIL, apscheduler' 2>/dev/null || { echo "依赖装得不完整，请重新运行。"; rm -f venv/.deps_stamp; return 1; }
}

# 机器人只往外连 QQ，不需要任何入站端口；这里只检查能不能访问 QQ
check_outbound() {
  if command -v curl >/dev/null 2>&1; then
    curl -s -m 8 -o /dev/null https://bots.qq.com/ && echo "网络检查：能访问 QQ 开放平台。" || echo "提醒：这台服务器暂时访问不了 QQ 开放平台（bots.qq.com），请检查网络或出站规则。"
  fi
}
