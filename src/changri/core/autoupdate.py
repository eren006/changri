"""落后太多时自动更新，以及每次更新前后的检查。

什么时候自动更新（maybe_update，每 10 分钟由 plugins/botsync 调一次，只在后台已经发现有新版本时才往下走）：
- 主人没关「自动更新」（update 页的开关，auto_update_enabled，默认开）；
- 落后够多：中间差了 auto_update_behind 个版本（默认 3），或者最早没装的那版已经发布 auto_update_days 天（默认 14）；
  作者在 changelog 里标了 "urgent": true 的版本不等落后，也不等空闲时段：半小时没人写戏就更新（作者 2026-11-20 定：
  紧急修复要尽快到用户手里，等凌晨的话，像 Windows 3 天时限那种问题，等的这段时间里还会再坏一次）；
- 作者没给中间任何一版标 "auto": false（改动大、要主人自己看着更新的版本）；
- 主人没对这一版点过「这次先不更新」（urgent 的除外），这一版也没有自动更新失败过；
- 更新前检查都过（pre_check）：在空闲时段里（默认凌晨 04:00–06:00）、半小时内没人在房间里写戏、
  不在心动信派送前后半小时、没有待派送的「立即派送」、磁盘够。

更新后检查（post_check，新版本启动 90 秒后跑）：设置平台、小手机能打开，数据库能读，机器人在线数和定时任务不比更新前少，
启动后没有一下冒出很多报错。不过就 updater.request_rollback → 看门进程恢复数据库、切回旧版本。
手动点「立即更新」也走更新后检查，只有「什么时候自动更新」只管自动的。
"""
from __future__ import annotations

import datetime
import shutil
import time
import urllib.request
from pathlib import Path

from ..paths import DATA_DIR
from ..platform import layout
from . import updater

DEFAULT_WINDOW = "04:00-06:00"
DEFAULT_BEHIND = 3
DEFAULT_DAYS = 14
QUIET_MINUTES = 30            # 这么久内有人在房间里写戏，就不算空闲
LOVEMAIL_MARGIN = 30          # 心动信派送时间前后这么多分钟不更新
ERROR_LIMIT = 20              # 新版本启动后到检查时报错超过这么多条，算没通过
SPARE_BYTES = 300 * 1024 * 1024

# 更新后检查等机器人重连：最多等这么久（秒），每隔 BOTS_RETRY 秒看一次
BOTS_WAIT = 240
BOTS_RETRY = 30


# ---------- 设置 ----------

def _cfg(key: str, default: str) -> str:
    from .db import get_config
    return (get_config(key, default) or default).strip()


def enabled() -> bool:
    return _cfg("auto_update_enabled", "1") != "0"


def _int(key: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(_cfg(key, str(default)))))
    except ValueError:
        return default


def behind_limit() -> int:
    return _int("auto_update_behind", DEFAULT_BEHIND, 1, 50)


def days_limit() -> int:
    return _int("auto_update_days", DEFAULT_DAYS, 1, 365)


def _hm(text: str) -> int | None:
    try:
        h, m = text.strip().replace("：", ":").split(":")
        h, m = int(h), int(m)
    except ValueError:
        return None
    return h * 60 + m if 0 <= h <= 24 and 0 <= m < 60 and h * 60 + m <= 1440 else None


def parse_window(text: str) -> tuple[int, int] | None:
    """「04:00-06:00」→ (240, 360)，分钟。跨午夜的（23:00-02:00）也行。格式不对返回 None。"""
    parts = (text or "").replace("～", "-").replace("~", "-").replace("—", "-").split("-")
    if len(parts) != 2:
        return None
    a, b = _hm(parts[0]), _hm(parts[1])
    return (a, b % 1440) if a is not None and b is not None and a % 1440 != b % 1440 else None


def window() -> tuple[int, int]:
    return parse_window(_cfg("auto_update_window", DEFAULT_WINDOW)) or parse_window(DEFAULT_WINDOW)


def in_window(minute: int, win: tuple[int, int]) -> bool:
    a, b = win
    return a <= minute < b if a < b else (minute >= a or minute < b)


def window_text(win: tuple[int, int] | None = None) -> str:
    a, b = win or window()
    return f"{a // 60:02d}:{a % 60:02d}-{b // 60:02d}:{b % 60:02d}"


# ---------- 该不该自动更新 ----------

def _failed_version(data_dir: Path) -> str:
    import json
    p = data_dir / "update_failed.json"
    try:
        return str(json.loads(p.read_text(encoding="utf-8")).get("to", "")) if p.exists() else ""
    except (ValueError, OSError, AttributeError):
        return ""


def decide(info: dict, now: float | None = None, data_dir: Path | None = None) -> tuple[bool, str]:
    """只看版本：这次检查到的新版本该不该自动装。返回 (要不要, 原因)。不看空闲、磁盘（那些是 pre_check）。"""
    data_dir = data_dir or DATA_DIR
    if not enabled():
        return False, "主人关了自动更新"
    if not info.get("newer"):
        return False, "已经是最新版本"
    pend = info.get("pending") or []
    if any(not c.get("auto", True) for c in pend):
        return False, "中间有作者要求手动更新的版本"
    if _failed_version(data_dir) == info["version"]:
        return False, f"{info['version']} 自动更新失败过，不再自动试"
    if any(c.get("urgent") for c in pend):
        return True, "有重要更新，不等时段，半小时没人写戏就装"
    from .db import get_config
    if get_config("update_skip", "") == info["version"]:
        return False, "主人选了这个版本先不更新"
    if len(pend) >= behind_limit():
        return True, f"落后 {len(pend)} 个版本"
    dates = [c.get("date", "") for c in pend if c.get("date")]
    if dates:
        try:
            oldest = datetime.date.fromisoformat(min(dates))
        except ValueError:
            oldest = None
        today = datetime.date.fromtimestamp(now or time.time())
        if oldest and (today - oldest).days >= days_limit():
            return True, f"最早没装的版本已经发布 {(today - oldest).days} 天"
    return False, f"只落后 {len(pend)} 个版本，还不用自动更新"


# ---------- 更新前检查 ----------

def _bot_ids() -> list[str]:
    from .db import list_bot_ids
    try:
        return list_bot_ids() or [""]
    except Exception:  # noqa: BLE001
        return [""]


def _recent_play(since: int) -> bool:
    """有没有哪个机器人在 since 之后有人在房间里写过戏。"""
    from .db import get_conn, use_bot
    for app_id in _bot_ids():
        with use_bot(app_id):
            try:
                with get_conn() as c:
                    if c.execute("SELECT 1 FROM room_lines WHERE ts>=? LIMIT 1", (since,)).fetchone():
                        return True
            except Exception:  # noqa: BLE001  某个机器人的库还没建好表：不算有人在写
                continue
    return False


def _lovemail_busy(minute: int) -> bool:
    """有机器人的心动信派送时间就在前后半小时内，或者后台点了「立即派送」还没派完。"""
    from .db import get_config, use_bot
    for app_id in _bot_ids():
        with use_bot(app_id):
            try:
                if get_config("lovemail_deliver_now", "0") == "1":
                    return True
                if get_config("lovemail_enabled", "1") == "0":
                    continue
                t = _hm(get_config("lovemail_delivery_time", "22:00").replace('"', "") or "22:00")
            except Exception:  # noqa: BLE001
                continue
            if t is not None and min((minute - t) % 1440, (t - minute) % 1440) <= LOVEMAIL_MARGIN:
                return True
    return False


def _data_bytes(data_dir: Path) -> int:
    total = 0
    for p in data_dir.rglob("changri.db*"):
        if "backups" in p.relative_to(data_dir).parts:
            continue
        try:
            total += p.stat().st_size
        except OSError:
            pass
    return total


def pre_check(info: dict, now: float | None = None, data_dir: Path | None = None) -> str:
    """自动更新前的检查。全过返回空字符串，否则返回没过的那条（下一个 10 分钟再看）。"""
    data_dir = data_dir or DATA_DIR
    t = time.localtime(now or time.time())
    minute = t.tm_hour * 60 + t.tm_min
    urgent = any(c.get("urgent") for c in info.get("pending") or [])
    if not urgent and not in_window(minute, window()):          # 紧急版本不等时段，下面几条照样要过
        return f"不在自动更新时段（{window_text()}）"
    if _recent_play(int(now or time.time()) - QUIET_MINUTES * 60):
        return f"{QUIET_MINUTES} 分钟内有人在房间里写戏"
    if _lovemail_busy(minute):
        return "心动信派送前后"
    need = int(info.get("size") or 0) * 3 + _data_bytes(data_dir) + SPARE_BYTES
    try:
        free = shutil.disk_usage(layout.APP_ROOT).free
    except OSError:
        free = need
    if free < need:
        return f"磁盘空间不够（剩 {free // 1048576} MB，需要约 {need // 1048576} MB）"
    return ""


# ---------- 更新前的样子（拿来跟更新后对照） ----------

def snapshot() -> dict:
    """现在几个机器人在线、有哪些定时任务、小手机开没开。拿不到的就不记，更新后那一项也就不查。"""
    snap: dict = {}
    try:
        import nonebot
        snap["bots_online"] = len(nonebot.get_bots())
    except Exception:  # noqa: BLE001
        pass
    try:
        from nonebot_plugin_apscheduler import scheduler
        snap["jobs"] = sorted(j.id for j in scheduler.get_jobs() if j.id not in ("update_confirm", "update_post_check", "auto_update",
                                                                         "usage_on_connect"))   # 机器人连上后 1 分钟的一次性补报，跑完就没了
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import phoneserver
        snap["phone"] = phoneserver.running()
    except Exception:  # noqa: BLE001
        pass
    return snap


# ---------- 更新后检查 ----------

def _http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def check_once(pending: dict, started: float) -> list[str]:
    """除机器人在线数以外的检查，一次跑完。返回没通过的项（空 = 全过）。"""
    bad = []
    from .. import config
    if not _http_ok(f"http://127.0.0.1:{config.admin_port()}/healthz"):
        bad.append("设置平台打不开")
    if pending.get("phone"):
        from . import phoneserver
        if not phoneserver.running() or not _http_ok(f"http://127.0.0.1:{phoneserver.port()}/p/health"):
            bad.append("小手机打不开")
    from .db import get_conn, use_bot
    for app_id in _bot_ids():
        with use_bot(app_id):
            try:
                with get_conn() as c:
                    c.execute("SELECT COUNT(*) FROM roles").fetchone()
                    c.execute("SELECT COUNT(*) FROM rooms").fetchone()
            except Exception as e:  # noqa: BLE001
                bad.append(f"数据库读不了（{type(e).__name__}）")
                break
    if "jobs" in pending:
        try:
            from nonebot_plugin_apscheduler import scheduler
            now_jobs = {j.id for j in scheduler.get_jobs()}
            gone = [j for j in pending["jobs"] if j not in now_jobs]
            if gone:
                bad.append("定时任务少了：" + "、".join(gone[:5]))
        except Exception:  # noqa: BLE001
            pass
    from . import errlog
    n = sum(1 for r in errlog.read_all() if r.get("kind") == "error" and int(r.get("ts", 0)) >= started)
    if n >= ERROR_LIMIT:
        bad.append(f"启动后已经报错 {n} 条")
    return bad


def bots_ok(pending: dict) -> bool:
    if "bots_online" not in pending:
        return True
    try:
        import nonebot
        return len(nonebot.get_bots()) >= int(pending["bots_online"])
    except Exception:  # noqa: BLE001
        return True


async def post_check(started: float, sleep=None) -> str:
    """新版本启动后调用（有待确认的更新才查）。通过就确认；没通过就请看门进程回滚（这个进程会退出）。返回结果说明（测试和日志用）。"""
    import asyncio
    sleep = sleep or asyncio.sleep
    pending = updater.read_pending()
    if pending is None:
        return "没有待确认的更新"
    waited = 0
    while not bots_ok(pending) and waited < BOTS_WAIT:      # QQ 重连要一会儿，多等几次
        await sleep(BOTS_RETRY)
        waited += BOTS_RETRY
    bad = [] if bots_ok(pending) else [f"机器人没全连上（更新前 {pending['bots_online']} 个在线）"]
    loop = asyncio.get_running_loop()
    bad += await loop.run_in_executor(None, check_once, pending, started)
    if bad:
        updater.request_rollback("；".join(bad))
        return "没通过：" + "；".join(bad)
    updater.confirm_healthy()
    return "通过"


# ---------- 自动更新 ----------

def maybe_update(snap: dict, now: float | None = None) -> str:
    """该自动更新就装上（调用方负责 restart）。返回空字符串 = 没装；否则是装好的新版本号。"""
    if not layout.INSTALLED or not enabled():
        return ""
    try:
        info = updater.check()
    except updater.UpdateError:
        return ""
    ok, _why = decide(info, now)
    if not ok or pre_check(info, now):
        return ""
    res = updater.update_now(extra={**snap, "auto": True})
    from .db import set_config
    set_config("update_latest", "")
    (DATA_DIR / "update_failed.txt").unlink(missing_ok=True)
    return res["to"]


def status(info: dict | None = None) -> dict:
    """「更新」页显示用：开关、条件、上次自动更新的结果。"""
    import json
    last = None
    p = DATA_DIR / "update_auto.json"
    try:
        last = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    except (ValueError, OSError):
        last = None
    out = {"enabled": enabled(), "behind": behind_limit(), "days": days_limit(), "window": window_text(), "last": last,
           "installed": layout.INSTALLED}
    if info and info.get("newer"):
        out["decision"] = decide(info)
    return out


# ---------- 后台顶上的提示 ----------

def note(data_dir: Path | None = None) -> dict | None:
    """主人打开后台时顶上要不要提示：自动更新成功了，或者更新没通过检查回滚了。点过「知道了」的不再提示。"""
    data_dir = data_dir or DATA_DIR
    import json
    for name, kind in (("update_failed.json", "failed"), ("update_auto.json", "done")):
        p = data_dir / name
        try:
            d = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
        except (ValueError, OSError):
            d = None
        if isinstance(d, dict) and not d.get("seen"):
            return {"kind": kind, "from": str(d.get("from", "")), "to": str(d.get("to", "")), "ts": int(d.get("ts") or 0),
                    "message": str(d.get("message", ""))}
    return None


def mark_seen(data_dir: Path | None = None) -> None:
    data_dir = data_dir or DATA_DIR
    import json
    for name in ("update_failed.json", "update_auto.json"):
        p = data_dir / name
        try:
            d = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
        except (ValueError, OSError):
            d = None
        if isinstance(d, dict):
            d["seen"] = True
            p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
