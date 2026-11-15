"""/b/<AppID>/xxx → 去掉前缀交给后台，并记下这是哪个机器人。
做成 WSGI 的 SCRIPT_NAME，这样页面里所有 url_for 自动带上前缀，不用改每一条路由。"""
import re

_RE = re.compile(r"^/b/(\d{4,24})(/.*)?$")


class BotPrefix:
    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        m = _RE.match(environ.get("PATH_INFO", ""))
        if m:
            environ["changri.bot"] = m.group(1)
            environ["SCRIPT_NAME"] = environ.get("SCRIPT_NAME", "") + f"/b/{m.group(1)}"
            environ["PATH_INFO"] = m.group(2) or "/"
        return self.app(environ, start_response)
