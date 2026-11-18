"""接收长日bot 报错日志的服务，部署在主页服务器（system.changri.work）上，由作者部署，不随安装包使用。

收：POST /api/errors，JSON {"app","version","install","contact"（选填，装的人自己填的联系 QQ）,"errors":[{ts,kind,source,message,trace,version}]}（长日bot 的 core/errlog.py 发的）。
    每条另记来源 IP 和实例编号（install，机器人本地随机生成的，0.21.6 起才带），监控后台（console_service.py）按这两个分实例。
看：GET /api/errors（需要请求头 Authorization: Bearer <口令>；口令从环境变量 CHANGRI_ERRORS_TOKEN 读，没设就不开放查看），
    返回最近 100 条。也可以直接 tail 数据文件。

安全：只收、只存文本；请求体最多 64KB；每条每个字段截断，条数最多 50；每个来源 IP 每分钟最多 10 次；
收到的文字再抹一遍长得像密钥的串和长数字；数据文件超过 5MB 就改名成 .1 重开（只留一份旧的），被刷也封顶。
不回显收到的内容，不执行、不转发任何东西。

运行：CHANGRI_ERRORS_DIR=/var/lib/changri_errors python3 errors_service.py（监听 127.0.0.1:5198），nginx 里：
    location /api/errors { proxy_pass http://127.0.0.1:5198; proxy_set_header X-Real-IP $remote_addr; client_max_body_size 64k; }
"""
import hmac
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_BODY = 64 * 1024
MAX_ITEMS = 50
MAX_FILE = 5 * 1024 * 1024
FIELDS = {"kind": 20, "source": 120, "message": 600, "trace": 1500, "version": 20}
_SECRET = re.compile(r"[A-Za-z0-9_\-]{24,}")
_CODE_NAME = re.compile(r"_*[a-z][a-z0-9]*(?:_+[a-z0-9]+)+_*")   # 代码里的函数名（_get_authorization_header 这种 snake_case）不是密钥，不遮
_DIGITS = re.compile(r"(?<!\d)\d{6,}(?!\d)")
HITS: dict[str, list[float]] = {}
_lock = threading.Lock()


def data_file() -> Path:
    d = Path(os.environ.get("CHANGRI_ERRORS_DIR", "./errors_data"))
    d.mkdir(parents=True, exist_ok=True)
    return d / "errors.jsonl"


def _mask(m) -> str:
    return m.group(0) if _CODE_NAME.fullmatch(m.group(0)) else "<…>"


def scrub(text) -> str:
    return _DIGITS.sub("#", _SECRET.sub(_mask, str(text or "")))


def rate_ok(ip: str, now: float | None = None) -> bool:
    now = now or time.time()
    hits = [t for t in HITS.get(ip, []) if now - t < 60]
    if len(hits) >= 10:
        HITS[ip] = hits
        return False
    HITS[ip] = hits + [now]
    return True


_INSTALL = re.compile(r"^[0-9a-f]{16,32}$")
_CONTACT = re.compile(r"^\d{5,12}$")          # 装的人自己填的联系 QQ（选填，0.22.1 起）；不像 QQ 号的一律丢掉


def clean(payload, ip: str = "") -> list[dict]:
    """把收到的内容整理成安全的记录；格式不对返回空列表。ip = 发来的那台机器（来自反代的 X-Real-IP）。"""
    if not isinstance(payload, dict) or not isinstance(payload.get("errors"), list):
        return []
    app = scrub(payload.get("app"))[:40]
    ver = scrub(payload.get("version"))[:20]
    inst = str(payload.get("install") or "")
    inst = inst if _INSTALL.match(inst) else ""
    contact = str(payload.get("contact") or "")
    contact = contact if _CONTACT.match(contact) else ""
    out = []
    for e in payload["errors"][-MAX_ITEMS:]:
        if not isinstance(e, dict):
            continue
        row = {k: (scrub(e.get(k))[-n:] if k == "trace" else scrub(e.get(k))[:n]) for k, n in FIELDS.items()}   # trace 留尾：真正的报错在最后
        try:
            row["ts"] = int(e.get("ts") or 0)
        except (TypeError, ValueError):
            row["ts"] = 0
        row["app"], row["app_version"] = app, ver
        row["install"], row["ip"] = inst, str(ip)[:45]
        if contact:
            row["contact"] = contact
        row["got"] = int(time.time())
        out.append(row)
    return out


def store(rows: list[dict]) -> None:
    p = data_file()
    with _lock:
        if p.exists() and p.stat().st_size > MAX_FILE:
            p.replace(p.with_suffix(".jsonl.1"))
        with p.open("a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")


def recent(n: int = 100) -> list[dict]:
    p = data_file()
    if not p.exists():
        return []
    with _lock:
        lines = p.read_text(encoding="utf-8").splitlines()[-n:]
    return [json.loads(x) for x in lines if x.strip()]


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, obj: dict | list):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path.split("?")[0] != "/api/errors":
            return self._send(404, {"ok": False})
        ip = self.headers.get("X-Real-IP", self.client_address[0])
        if not rate_ok(ip):
            return self._send(429, {"ok": False})
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = -1
        if n <= 0 or n > MAX_BODY:
            return self._send(413 if n > MAX_BODY else 400, {"ok": False})
        try:
            payload = json.loads(self.rfile.read(n).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._send(400, {"ok": False})
        rows = clean(payload, ip)
        if not rows:
            return self._send(400, {"ok": False})
        store(rows)
        self._send(200, {"ok": True, "n": len(rows)})

    def do_GET(self):
        token = os.environ.get("CHANGRI_ERRORS_TOKEN", "")
        got = self.headers.get("Authorization", "")
        if self.path.split("?")[0] != "/api/errors" or not token or not hmac.compare_digest(got, "Bearer " + token):
            return self._send(404, {"ok": False})
        self._send(200, recent())

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 5198), Handler).serve_forever()
