"""机器人账号（AppID / AppSecret）。以数据库为准，由主账号在设置平台里管理。
旧部署 .env 里的 QQ_BOTS 只在数据库还没有任何机器人时导入一次。bot 进程和设置平台共用。"""
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import dotenv_values

from ..paths import LEGACY_ENV as ENV_PATH
TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
GATEWAY_URL = "https://api.sgroup.qq.com/gateway"
IP_SERVICES = ("https://api.ipify.org", "https://ifconfig.me/ip", "https://icanhazip.com")


# ---------- 旧的 .env 来源 ----------


# QQ 开放平台里找东西的路径（照 2026-11 的界面写；界面改了就改这里和 admin/templates/_ip_whitelist.html）
CREDS_PATH = "QQ 开放平台（q.qq.com）「机器人 → 去创建或管理我的 QQ 机器人 → 点你的机器人 → 开发设置」"
WHITELIST_PATH = "QQ 开放平台（q.qq.com）「机器人 → 去创建或管理我的 QQ 机器人 → 点你的机器人 → 开发设置 → 服务器 IP 白名单 → 添加 IP」"

def _env_bots() -> list[dict]:
    raw = os.environ.get("QQ_BOTS") or dotenv_values(ENV_PATH).get("QQ_BOTS") or "[]"
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [b for b in data if isinstance(b, dict) and b.get("id") and b.get("secret")]


def import_env_bots() -> None:
    from .db import get_global_conn as get_conn
    with get_conn() as conn:
        if conn.execute("SELECT COUNT(*) FROM bot_accounts").fetchone()[0]:
            return
        for b in _env_bots():
            conn.execute("INSERT OR IGNORE INTO bot_accounts(app_id, secret, created_ts) VALUES (?,?,?)",
                         (str(b["id"]), str(b["secret"]), int(time.time())))


# ---------- 增删改查 ----------

def list_accounts() -> list[dict]:
    from .db import get_global_conn as get_conn
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='bot_accounts'").fetchone():
            return []                       # 旧库升级过程中表还没建
        return [dict(r) for r in conn.execute("SELECT * FROM bot_accounts ORDER BY created_ts, app_id")]


def get_account(app_id: str) -> dict | None:
    return next((a for a in list_accounts() if a["app_id"] == app_id), None)


def mask(secret: str) -> str:
    return "••••" + secret[-4:] if len(secret) > 6 else "••••"


def _check_id(app_id: str, secret: str) -> str:
    if not re.fullmatch(r"\d{6,20}", app_id):
        return f"AppID 是一串数字，请从 {CREDS_PATH} 复制。"
    if not re.fullmatch(r"[A-Za-z0-9]{16,64}", secret):
        return "AppSecret 看起来不对（应是十几到几十位字母数字），请重新复制。"
    return ""


def add_account(app_id: str, secret: str, note: str = "") -> tuple[bool, str]:
    from .db import get_global_conn as get_conn
    app_id, secret = app_id.strip(), secret.strip()
    if err := _check_id(app_id, secret):
        return False, err
    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM bot_accounts WHERE app_id=?", (app_id,)).fetchone():
            return False, "这个 AppID 已经接入过了。要换密钥请用它那一行的「改密钥」。"
        conn.execute("INSERT INTO bot_accounts(app_id, secret, note, created_ts) VALUES (?,?,?,?)",
                     (app_id, secret, note.strip()[:30], int(time.time())))
    from .db import forget_bots
    forget_bots()
    return True, ""


def update_secret(app_id: str, secret: str) -> tuple[bool, str]:
    from .db import get_global_conn as get_conn
    secret = secret.strip()
    if err := _check_id(app_id, secret):
        return False, err
    with get_conn() as conn:
        cur = conn.execute("UPDATE bot_accounts SET secret=? WHERE app_id=?", (secret, app_id))
    return (cur.rowcount > 0, "" if cur.rowcount else "没有这个机器人。")


def set_enabled(app_id: str, on: bool) -> None:
    from .db import get_global_conn as get_conn
    with get_conn() as conn:
        conn.execute("UPDATE bot_accounts SET enabled=? WHERE app_id=?", (1 if on else 0, app_id))
    from .db import forget_bots
    forget_bots()


def delete_account(app_id: str) -> None:
    from .db import get_global_conn as get_conn
    with get_conn() as conn:
        conn.execute("DELETE FROM bot_accounts WHERE app_id=?", (app_id,))
    from .db import forget_bots
    forget_bots()


def configured_bot_ids() -> list[str]:
    ids = [a["app_id"] for a in list_accounts() if a["enabled"]]
    return ids or [str(b["id"]) for b in _env_bots()]    # 数据库还空着时（刚升级）退回 .env


def qq_bots_config() -> list[dict]:
    """给 nonebot.init(qq_bots=...) 用的完整配置。"""
    return [{"id": a["app_id"], "token": "", "secret": a["secret"],
             "intent": {"c2c_group_at_messages": True}, "use_websocket": True}
            for a in list_accounts() if a["enabled"]]


# ---------- 测试连接 ----------

def _http(url: str, *, data: dict | None = None, headers: dict | None = None, timeout: float = 10) -> tuple[int, str]:
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", **(headers or {})},
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _json(text: str) -> dict:
    try:
        v = json.loads(text)
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


def public_ip() -> str:
    for url in IP_SERVICES:
        try:
            status, text = _http(url, timeout=4)
        except Exception:  # noqa: BLE001
            continue
        text = text.strip()
        if status == 200 and re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", text):
            return text
    return ""


def test_credentials(app_id: str, secret: str) -> dict:
    """依次验证：密钥对不对 → 这台服务器的 IP 在不在白名单。
    返回 {ok, step('network'|'secret'|'whitelist'|'ok'), message, ip}。"""
    try:
        status, text = _http(TOKEN_URL, data={"appId": app_id, "clientSecret": secret})
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "step": "network", "ip": "", "message": f"连不上 QQ 的服务器（{type(e).__name__}）。请检查这台服务器能不能访问外网。"}
    token = _json(text).get("access_token")
    if not token:
        return {"ok": False, "step": "secret", "ip": "", "message": "AppID 或 AppSecret 不对。请回 QQ 开放平台重新复制（注意没有多余的空格）。"}
    try:
        status, text = _http(GATEWAY_URL, headers={"Authorization": f"QQBot {token}", "X-Union-Appid": app_id})
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "step": "network", "ip": "", "message": f"连不上 QQ 的服务器（{type(e).__name__}）。"}
    data = _json(text)
    if status == 200 and data.get("url"):
        return {"ok": True, "step": "ok", "ip": "", "message": "密钥正确，白名单也通过了，机器人可以连接。"}
    msg = str(data.get("message", ""))
    if data.get("code") == 11298 or "whitelist" in msg.lower() or "白名单" in msg:
        m = re.search(r"\d{1,3}(?:\.\d{1,3}){3}", msg)
        ip = m.group(0) if m else public_ip()
        return {"ok": False, "step": "whitelist", "ip": ip,
                "message": f"密钥是对的，但这台服务器的 IP 还没加进白名单。请到 {WHITELIST_PATH} 添加" + (f"：{ip}" if ip else "这台服务器的公网 IP") + "，保存后再测一次。"}
    return {"ok": False, "step": "network", "ip": "", "message": f"QQ 返回了意外的结果（{status}）：{msg or text[:120]}"}
