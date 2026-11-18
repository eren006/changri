#!/bin/bash
# 长日bot 修复工具（Linux）。用法（复制整行运行）：
#   curl -fsSL @BASE@/fix.sh | sudo bash
# 只修「打不开」这一类：正常运行 → 告诉你地址；卡死 / 没在运行 → 重新启动；位置（端口）被别的程序占了 → 问过再关；
# 没装开机自启的，问你要不要顺便装上（装上以后崩了、重启机器都会自己起来）。
# 每次运行的都是官网上最新的这份，作者改了修复办法大家马上就用上。
FIX_VERSION='@FIX_VERSION@'
UNIT=changri-bot
DEFAULT_PORT=5091

say()  { printf '%s\n' "$*"; }
ok()   { printf '  \033[32m√\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m×\033[0m %s\n' "$*"; }
ask_yes() {                                  # 用 curl | bash 运行时 stdin 是脚本本身，问话要从终端读
  local a=""
  if [ -r /dev/tty ]; then printf '%s ' "$1" > /dev/tty; read -r a < /dev/tty || a=""; fi
  case "$(printf '%s' "${a}" | tr 'A-Z' 'a-z')" in y|yes|是|好) return 0 ;; *) return 1 ;; esac
}

say ""
say "  长日bot 修复工具（${FIX_VERSION}）"
say "  ----------------------------------------"
if [ "$(id -u)" != 0 ]; then
  bad "要用管理员身份运行：在命令前面加 sudo，比如"
  say "    curl -fsSL @BASE@/fix.sh | sudo bash"
  exit 1
fi

# ---------- 找长日bot ----------
changri_pids() {                             # 长日bot 的进程：命令行里有「-m changri」
  local p
  for p in /proc/[0-9]*; do
    tr '\0' ' ' < "${p}/cmdline" 2>/dev/null | grep -qE -- '-m changri( |$)' && printf '%s\n' "${p#/proc/}"
  done
}
root_of_pid() {                              # .../<安装目录>/runtime/<编号>/bin/python3 → <安装目录>
  local exe d
  exe="$(readlink -f "/proc/$1/exe" 2>/dev/null)" || return 1
  d="$(dirname "$(dirname "$(dirname "${exe}")")")"
  [ "$(basename "${d}")" = runtime ] && [ -x "$(dirname "${d}")/changri" ] && printf '%s\n' "$(dirname "${d}")"
}

ROOT=""
UNIT_FILE="${CHANGRI_UNIT_FILE:-/etc/systemd/system/${UNIT}.service}"   # 测试时可以换成别的文件
if [ -n "${CHANGRI_DIR}" ] && [ -x "${CHANGRI_DIR}/changri" ]; then
  ROOT="${CHANGRI_DIR}"                        # 自己指定了安装目录
elif [ -f "${UNIT_FILE}" ]; then
  ROOT="$(sed -n 's/^WorkingDirectory=//p' "${UNIT_FILE}" | head -n1)"
fi
if [ -z "${ROOT}" ]; then
  for pid in $(changri_pids); do ROOT="$(root_of_pid "${pid}")" && [ -n "${ROOT}" ] && break; done
fi
[ -z "${ROOT}" ] && [ -x /opt/changri-bot/changri ] && ROOT=/opt/changri-bot
if [ -z "${ROOT}" ] || [ ! -x "${ROOT}/changri" ]; then
  bad "没找到长日bot 装在哪里（默认是 /opt/changri-bot）。"
  say "    装在别处的话，这样运行：curl -fsSL @BASE@/fix.sh | sudo CHANGRI_DIR=/你的/安装目录 bash"
  exit 1
fi

PORT="$(sed -nE 's/^[[:space:]]*port[[:space:]]*=[[:space:]]*([0-9]+).*/\1/p' "${ROOT}/data/config.toml" 2>/dev/null | head -n1)"
PORT="${PORT:-${DEFAULT_PORT}}"
say "  长日bot 装在：${ROOT}"
say "  设置平台的端口：${PORT}"
say ""

healthy() { [ "$(curl -fsS -m 3 "http://127.0.0.1:${PORT}/healthz" 2>/dev/null)" = ok ]; }
listener_pid() {
  if command -v ss >/dev/null 2>&1; then
    ss -ltnpH "sport = :${PORT}" 2>/dev/null | grep -oE 'pid=[0-9]+' | head -n1 | cut -d= -f2
  elif command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN -t 2>/dev/null | head -n1
  fi
}
is_changri() { tr '\0' ' ' < "/proc/$1/cmdline" 2>/dev/null | grep -qE -- '-m changri( |$)'; }
wait_healthy() {                             # 最多等 $1 秒
  local i=0
  while [ "${i}" -lt "$1" ]; do healthy && { echo; return 0; }; printf '.'; sleep 2; i=$((i + 2)); done
  echo; healthy
}
port_free() { [ -z "$(listener_pid)" ]; }
wait_free() { local i=0; while [ "${i}" -lt 15 ]; do port_free && return 0; sleep 1; i=$((i + 1)); done; port_free; }
has_unit() { [ -f "${UNIT_FILE}" ]; }
done_ok() { ok "修好了，长日bot 正常运行。设置平台：http://服务器IP:${PORT}/（云服务器记得在安全组放行 ${PORT} 端口）"; exit 0; }

# ---------- 看状态、修 ----------
if healthy; then
  ok "长日bot 正在正常运行，没有问题。设置平台：http://服务器IP:${PORT}/"
  has_unit || warn "还没设开机自启：运行 sudo ${ROOT}/changri service install 可以设上（崩了、重启机器都会自己起来）。"
  exit 0
fi

PID="$(listener_pid)"
if [ -n "${PID}" ] && ! is_changri "${PID}"; then
  NAME="$(cat "/proc/${PID}/comm" 2>/dev/null)"; NAME="${NAME:-PID ${PID}}"
  bad "长日bot 需要的端口 ${PORT} 被「${NAME}」（PID ${PID}）占着，所以长日bot 打不开。"
  if ! ask_yes "  要关掉「${NAME}」、让长日bot 正常启动吗？输入 y 回车 = 关掉；直接回车 = 不关："; then
    warn "没有关。也可以把 ${ROOT}/data/config.toml 里的 port = ${PORT} 改成别的数字，再重新启动长日bot。"
    exit 1
  fi
  kill "${PID}" 2>/dev/null; sleep 3; kill -9 "${PID}" 2>/dev/null
  wait_free || { bad "没能关掉「${NAME}」。"; exit 1; }
  ok "已经关掉「${NAME}」"
elif [ -n "${PID}" ] || [ -n "$(changri_pids)" ]; then
  warn "长日bot 开着，但是没有反应。再等它一会儿"
  wait_healthy 30 && done_ok
  warn "长日bot 卡住了，帮你重新启动……"
  if has_unit; then systemctl stop "${UNIT}" 2>/dev/null; fi
  for p in $(changri_pids); do kill "${p}" 2>/dev/null; done
  sleep 3
  for p in $(changri_pids); do kill -9 "${p}" 2>/dev/null; done
  wait_free || { bad "没能关掉卡住的长日bot。重启服务器后再运行一次这个修复工具。"; exit 1; }
  ok "已经关掉卡住的长日bot"
else
  warn "长日bot 没有在运行。"
fi

if has_unit; then
  systemctl reset-failed "${UNIT}" 2>/dev/null
  systemctl restart "${UNIT}" && ok "已经重新启动开机自启的长日bot（systemctl restart ${UNIT}）"
elif ask_yes "  长日bot 还没设开机自启。要设上并启动吗（推荐：以后崩了、重启机器都会自己起来）？输入 y 回车 = 设上："; then
  "${ROOT}/changri" service install && ok "已经设为开机自启并启动"
else
  warn "没设开机自启。你可以自己在 screen / tmux 里运行：${ROOT}/changri run"
  exit 1
fi

say "  等长日bot 启动（最多 2 分钟）"
wait_healthy 120 && done_ok
bad "长日bot 还是没能打开。最近的日志："
if has_unit; then journalctl -u "${UNIT}" -n 15 --no-pager 2>/dev/null; fi
tail -n 1 "${ROOT}/data/errors.jsonl" 2>/dev/null
say "  请把上面这些截图发给维护的人。"
exit 1
