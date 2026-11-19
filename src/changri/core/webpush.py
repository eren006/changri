"""小手机网页推送（照老系统 rp_archive 的 Web Push）。
- 通知里不带发信人和正文（匿名信、匿名对话不能泄露身份，锁屏上也不该被旁人看到），只说「你收到了 N 条新短信，1 份礼物……」。
- 不挂在各个落库点上：定时（约 10 秒）扫一次新增的短信/礼物、送达的心动信、关系线、偷礼物，QQ 里发的和网页发的一视同仁。
- 第一次扫只记起点，不把历史当新消息；没人订阅时游标照样往前走。
- 不另装 pywebpush：VAPID 签名（ES256）和 aes128gcm 加密（RFC 8291 / 8188）用已经装着的 cryptography 自己做，安装包不变。
- 只往浏览器自家的推送服务发（苹果 / 谷歌 / 火狐 / 微软），不往玩家随便填的地址发。"""
import base64
import json
import logging
import os
import re
import struct
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from .db import get_config, get_conn, set_config

log = logging.getLogger(__name__)
MAX_SUBS_PER_ROLE = 5
WORDS = {"sms": "条新短信", "gift": "份礼物", "lovemail": "封心动信", "rel_line": "条新关系线", "rel_chat": "条关系线留言", "steal": "次有人动了你的图鉴"}


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def endpoint_allowed(endpoint) -> bool:
    """只认浏览器自己的 HTTPS 推送服务，不往任意地址发。"""
    if not isinstance(endpoint, str) or not 1 <= len(endpoint) <= 600 or any(ord(c) <= 32 for c in endpoint):
        return False
    try:
        u = urlparse(endpoint)
        host = u.hostname or ""
        return (u.scheme == "https" and not u.username and not u.password and u.port in (None, 443) and not u.fragment and "\\" not in endpoint
                and (host in {"web.push.apple.com", "fcm.googleapis.com", "updates.push.services.mozilla.com"}
                     or re.fullmatch(r"(?:[a-z0-9-]+\.)?notify\.windows\.com", host) is not None))
    except ValueError:
        return False


# ---------- VAPID 密钥（第一次用时生成存库，以后沿用；换了密钥所有订阅都会失效） ----------

def _vapid():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    pem = get_config("push_vapid_pem", "")
    if pem:
        key = serialization.load_pem_private_key(pem.encode(), password=None)
    else:
        key = ec.generate_private_key(ec.SECP256R1())
        set_config("push_vapid_pem", key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                       serialization.NoEncryption()).decode())
    pub = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return key, pub


def public_key() -> str:
    return b64u(_vapid()[1])


def _vapid_header(endpoint: str) -> str:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    key, pub = _vapid()
    u = urlparse(endpoint)
    sub = get_config("push_vapid_sub", "") or "mailto:admin@example.com"
    head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    body = b64u(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": sub}, separators=(",", ":")).encode())
    r, s = decode_dss_signature(key.sign(f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256())))
    return f"vapid t={head}.{body}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}, k={b64u(pub)}"


# ---------- aes128gcm 加密（RFC 8291） ----------

def _hkdf(salt: bytes, ikm: bytes, info: bytes, n: int) -> bytes:
    import hmac
    from hashlib import sha256
    prk = hmac.new(salt, ikm, sha256).digest()
    return hmac.new(prk, info + b"\x01", sha256).digest()[:n]


def encrypt(payload: bytes, p256dh: str, auth: str, salt: bytes | None = None, as_key=None) -> bytes:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    ua_pub = b64u_dec(p256dh)
    secret = b64u_dec(auth)
    as_key = as_key or ec.generate_private_key(ec.SECP256R1())
    as_pub = as_key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    shared = as_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub))
    ikm = _hkdf(secret, shared, b"WebPush: info\x00" + ua_pub + as_pub, 32)
    salt = salt or os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    ct = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + struct.pack("!IB", 4096, len(as_pub)) + as_pub + ct


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def send(sub, payload: dict) -> bool:
    """发一条；订阅已失效（404/410）就删掉。"""
    if not endpoint_allowed(sub["endpoint"]):
        return False
    try:
        body = encrypt(json.dumps(payload, ensure_ascii=False).encode(), sub["p256dh"], sub["auth"])
        req = urllib.request.Request(sub["endpoint"], data=body, method="POST", headers={
            "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream", "TTL": str(6 * 3600), "Urgency": "normal",
            "Authorization": _vapid_header(sub["endpoint"])})
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=10) as r:
            return 200 <= r.status < 300
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            with get_conn() as c:
                c.execute("DELETE FROM push_subs WHERE id=?", (sub["id"],))
        else:
            log.warning("网页推送失败 host=%s status=%s", urlparse(sub["endpoint"]).hostname, e.code)
    except Exception as e:  # noqa: BLE001
        log.warning("网页推送出错 host=%s type=%s", urlparse(sub["endpoint"]).hostname, type(e).__name__)
    return False


# ---------- 订阅 ----------

def subscribe(role_id: int, endpoint: str, p256dh: str, auth: str) -> tuple[bool, str]:
    if not (endpoint_allowed(endpoint) and isinstance(p256dh, str) and isinstance(auth, str) and 1 <= len(p256dh) <= 200 and 1 <= len(auth) <= 100):
        return False, "订阅信息不完整"
    with get_conn() as c:
        c.execute("INSERT INTO push_subs(role_id, endpoint, p256dh, auth, created_ts) VALUES(?,?,?,?,?) "
                  "ON CONFLICT(endpoint) DO UPDATE SET role_id=excluded.role_id, p256dh=excluded.p256dh, auth=excluded.auth, created_ts=excluded.created_ts",
                  (role_id, endpoint, p256dh, auth, int(time.time())))
        c.execute("DELETE FROM push_subs WHERE role_id=? AND id NOT IN (SELECT id FROM push_subs WHERE role_id=? ORDER BY created_ts DESC, id DESC LIMIT ?)",
                  (role_id, role_id, MAX_SUBS_PER_ROLE))
    return True, "已开启"


def unsubscribe(role_id: int, endpoint: str) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM push_subs WHERE role_id=? AND endpoint=?", (role_id, endpoint))


def subs_of(role_id: int) -> list:
    with get_conn() as c:
        return c.execute("SELECT * FROM push_subs WHERE role_id=?", (role_id,)).fetchall()


# ---------- 扫新消息（定时调用） ----------

CURSORS = ("msg", "lm", "rel_line", "rel_chat", "rel_detail", "steal")


def high_marks() -> dict[str, int]:
    with get_conn() as c:
        q = lambda sql: c.execute(sql).fetchone()[0] or 0  # noqa: E731
        return {"msg": q("SELECT MAX(id) FROM messages"), "lm": q("SELECT MAX(sent_ts) FROM lovemails WHERE status='sent'"),
                "rel_line": q("SELECT MAX(id) FROM relations"), "rel_chat": q("SELECT MAX(id) FROM rel_items"),
                "rel_detail": q("SELECT MAX(id) FROM rel_details"), "steal": q("SELECT MAX(id) FROM gift_steals")}


def count_since(old: dict, high: dict, role_id: int | None = None) -> dict[int, dict[str, int]]:
    """old→high 之间每个角色收到了什么：{角色id: {种类: 数量}}。role_id 给了就只算这一个人。"""
    from . import extensions
    out: dict[int, dict[str, int]] = {}

    def add(rid, w, n=1):
        if role_id is None or rid == role_id:
            d = out.setdefault(rid, {})
            d[w] = d.get(w, 0) + n
    with get_conn() as c:
        from .messaging import NOT_RECEIVED_SQL
        for r in c.execute(f"SELECT kind, from_role_id, to_role_id FROM messages WHERE id>? AND id<=? AND error!='已撤回' AND {NOT_RECEIVED_SQL}", (old["msg"], high["msg"])):
            if r["to_role_id"] != r["from_role_id"]:
                add(r["to_role_id"], r["kind"])
        if extensions.is_enabled("lovemail"):
            for r in c.execute("SELECT to_role_id FROM lovemails WHERE status='sent' AND sent_ts>? AND sent_ts<=?", (old["lm"], high["lm"])):
                add(r["to_role_id"], "lovemail")
        if extensions.is_enabled("relations"):
            new_lines = set()
            for r in c.execute("SELECT id, role_lo, role_hi, initiator_id FROM relations WHERE id>? AND id<=?", (old["rel_line"], high["rel_line"])):
                new_lines.add(r["id"])
                if r["initiator_id"] in (r["role_lo"], r["role_hi"]):
                    add(r["role_hi"] if r["initiator_id"] == r["role_lo"] else r["role_lo"], "rel_line")
            for tbl, ck in (("rel_items", "rel_chat"), ("rel_details", "rel_detail")):
                extra = " AND i.kind='chat'" if tbl == "rel_items" else ""
                for r in c.execute(f"SELECT i.rel_id, i.from_role_id, x.role_lo, x.role_hi, x.initiator_id FROM {tbl} i JOIN relations x ON x.id=i.rel_id "
                                   f"WHERE i.id>? AND i.id<=?{extra}", (old[ck], high[ck])):
                    if r["rel_id"] in new_lines and r["from_role_id"] == r["initiator_id"]:
                        continue                                          # 新线开头那段不重复算
                    add(r["role_hi"] if r["from_role_id"] == r["role_lo"] else r["role_lo"], "rel_chat")
        for r in c.execute("SELECT victim_id FROM gift_steals WHERE id>? AND id<=?", (old["steal"], high["steal"])):
            add(r["victim_id"], "steal")
    return out


def body_of(d: dict[str, int]) -> str:
    return "你收到了 " + "，".join(f"{n} {WORDS[w]}" for w, n in d.items() if w in WORDS)


def scan_once() -> int:
    """扫一次，给有新东西的人各推一条。返回推了几个人。"""
    raw = get_config("push_cursor", "")
    high = high_marks()
    if not raw:                                                      # 第一次：只记起点
        set_config("push_cursor", json.dumps(high))
        return 0
    try:
        old = {**{k: 0 for k in CURSORS}, **json.loads(raw)}
    except ValueError:
        old = high
    set_config("push_cursor", json.dumps(high))
    with get_conn() as c:
        if not c.execute("SELECT 1 FROM push_subs LIMIT 1").fetchone():
            return 0
    n = 0
    for rid, d in count_since(old, high).items():
        subs = subs_of(rid)
        if not subs:
            continue
        payload = {"title": "长日小手机", "body": body_of(d), "url": "/p/me", "tag": "phone-msg"}
        if any([send(s, payload) for s in subs]):
            n += 1
    return n
