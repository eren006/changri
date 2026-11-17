"""官网许愿池的服务，部署在主页服务器（system.changri.work）上，由作者部署，不随安装包使用。

大家在官网「许愿池」页许愿（想要的功能），作者审核通过后才上墙；墙上的愿望别人可以点「我也想要」。

接口（都是 JSON）：
  GET  /api/wishes                       墙上的愿望（审核通过的、已实现的），按「我也想要」排序
  POST /api/wishes          {text, name} 许愿：进待审核，不直接上墙
  POST /api/wishes/<id>/up               我也想要：同一个 IP 对同一个愿望只算一次
  GET  /api/wishes/admin                 全部（含待审核），要请求头 Authorization: Bearer <口令>
  POST /api/wishes/admin/<id>  {action}  approve 通过 / done 标为已实现 / reopen 改回进行中 / reject 不通过 / delete 删除；同样要口令
口令从环境变量 CHANGRI_WISHES_TOKEN 读，没设就不开放管理。

安全：请求体最多 4KB；愿望 2~200 字、称呼最多 12 字，去掉控制字符；每个 IP 每小时最多许 5 个愿、每分钟最多点 30 次；
待审核最多攒 500 条（满了不再收）；IP 只存加盐的哈希，不存原文。不回显 HTML，前端一律按纯文字显示。

运行：CHANGRI_WISHES_DIR=/var/lib/changri_wishes CHANGRI_WISHES_TOKEN=… python3 wishes_service.py（监听 127.0.0.1:5197），nginx 里：
    location /api/wishes { proxy_pass http://127.0.0.1:5197; proxy_set_header X-Real-IP $remote_addr; client_max_body_size 4k; }
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_BODY = 4 * 1024
TEXT_MIN, TEXT_MAX, NAME_MAX = 2, 200, 12
MAX_PENDING = 500
POST_PER_HOUR = 5
UP_PER_MIN = 30
STATUSES = ("pending", "open", "done", "rejected")
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f​-‏ -‮﻿]")
_lock = threading.Lock()
_hits: dict[str, list[float]] = {}


def _dir() -> Path:
    d = Path(os.environ.get("CHANGRI_WISHES_DIR", "./wishes_data"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _file() -> Path:
    return _dir() / "wishes.json"


def _salt() -> bytes:
    p = _dir() / "salt"
    if not p.exists():
        p.write_bytes(secrets.token_bytes(16))
    return p.read_bytes()


def ip_hash(ip: str) -> str:
    return hashlib.sha256(_salt() + ip.encode()).hexdigest()[:16]


def load() -> dict:
    try:
        d = json.loads(_file().read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("wishes"), list):
            return d
    except (OSError, ValueError):
        pass
    return {"next": 1, "wishes": []}


def save(d: dict) -> None:
    tmp = _file().with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    tmp.replace(_file())


def clean_text(s, limit: int) -> str:
    s = _CTRL.sub("", str(s or ""))
    s = re.sub(r"[ \t]+", " ", s).strip()
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s[:limit]


def rate_ok(key: str, limit: int, window: float, now: float | None = None) -> bool:
    now = now or time.time()
    hits = [t for t in _hits.get(key, []) if now - t < window]
    if len(hits) >= limit:
        _hits[key] = hits
        return False
    _hits[key] = hits + [now]
    return True


def public(w: dict) -> dict:
    return {"id": w["id"], "text": w["text"], "name": w["name"], "votes": len(w["voters"]), "status": w["status"], "ts": w["ts"]}


def wall(d: dict) -> list[dict]:
    shown = [w for w in d["wishes"] if w["status"] in ("open", "done")]
    shown.sort(key=lambda w: (w["status"] == "done", -len(w["voters"]), -w["ts"]))
    return [public(w) for w in shown]


def add(d: dict, text, name, who: str) -> tuple[int, dict]:
    text, name = clean_text(text, TEXT_MAX), clean_text(name, NAME_MAX).replace("\n", " ")
    if len(text) < TEXT_MIN:
        return 400, {"error": "写一句想要的功能吧（至少 2 个字）。"}
    if sum(1 for w in d["wishes"] if w["status"] == "pending") >= MAX_PENDING:
        return 503, {"error": "待审核的愿望太多了，过几天再来。"}
    w = {"id": d["next"], "text": text, "name": name, "status": "pending", "ts": int(time.time()), "by": who, "voters": [who]}
    d["next"] += 1
    d["wishes"].append(w)
    return 200, {"ok": True, "message": "收到了，审核通过后会出现在墙上。"}


def upvote(d: dict, wid: int, who: str) -> tuple[int, dict]:
    w = next((x for x in d["wishes"] if x["id"] == wid and x["status"] in ("open", "done")), None)
    if not w:
        return 404, {"error": "没有这个愿望。"}
    if who not in w["voters"]:
        w["voters"].append(who)
    return 200, {"ok": True, "votes": len(w["voters"])}


def admin_action(d: dict, wid: int, action: str) -> tuple[int, dict]:
    w = next((x for x in d["wishes"] if x["id"] == wid), None)
    if not w:
        return 404, {"error": "没有这个愿望。"}
    if action == "delete":
        d["wishes"].remove(w)
        return 200, {"ok": True}
    st = {"approve": "open", "reopen": "open", "done": "done", "reject": "rejected"}.get(action)
    if not st:
        return 400, {"error": "action 只能是 approve / done / reopen / reject / delete"}
    w["status"] = st
    return 200, {"ok": True, "wish": public(w)}


class H(BaseHTTPRequestHandler):
    server_version = "wishes"

    def log_message(self, *a):         # 不记访问日志（里面有 IP）
        pass

    def _ip(self) -> str:
        return self.headers.get("X-Real-IP") or self.client_address[0]

    def _send(self, code: int, obj) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_BODY:
            return None
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    def _admin(self) -> bool:
        tok = os.environ.get("CHANGRI_WISHES_TOKEN", "")
        got = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        return bool(tok) and hmac.compare_digest(got.encode(), tok.encode())

    def _path(self) -> list[str]:
        return [x for x in self.path.split("?")[0].split("/") if x][2:]      # 去掉 api/wishes

    def do_GET(self):
        p = self._path()
        with _lock:
            d = load()
        if not p:
            return self._send(200, {"wishes": wall(d)})
        if p == ["admin"]:
            if not self._admin():
                return self._send(403, {"error": "口令不对"})
            order = {s: i for i, s in enumerate(STATUSES)}
            ws = sorted(d["wishes"], key=lambda w: (order.get(w["status"], 9), -w["ts"]))
            return self._send(200, {"wishes": [public(w) for w in ws]})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        p, who = self._path(), ip_hash(self._ip())
        if p and p[0] == "admin":
            if not self._admin():
                return self._send(403, {"error": "口令不对"})
            b = self._body() or {}
            if len(p) != 2 or not p[1].isdigit():
                return self._send(404, {"error": "not found"})
            with _lock:
                d = load()
                code, out = admin_action(d, int(p[1]), str(b.get("action", "")))
                if code == 200:
                    save(d)
            return self._send(code, out)
        if not p:
            if not rate_ok("post:" + who, POST_PER_HOUR, 3600):
                return self._send(429, {"error": "许愿太频繁了，过一会儿再来。"})
            b = self._body()
            if not isinstance(b, dict):
                return self._send(400, {"error": "格式不对"})
            with _lock:
                d = load()
                code, out = add(d, b.get("text"), b.get("name"), who)
                if code == 200:
                    save(d)
            return self._send(code, out)
        if len(p) == 2 and p[0].isdigit() and p[1] == "up":
            if not rate_ok("up:" + who, UP_PER_MIN, 60):
                return self._send(429, {"error": "点得太快了。"})
            with _lock:
                d = load()
                code, out = upvote(d, int(p[0]), who)
                if code == 200:
                    save(d)
            return self._send(code, out)
        self._send(404, {"error": "not found"})


def main() -> None:
    port = int(os.environ.get("CHANGRI_WISHES_PORT", "5197"))
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()


if __name__ == "__main__":
    main()
