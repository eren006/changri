"""测试辅助：让所有 QQ 号连接在测试里看起来都「连着、登录着」（真实情况下没连上的号，名下的群不会分给房间）。"""
from core import qqmanager

UP = {"running": True, "connected": True, "online": True, "self_id": "1", "error": "", "count": 0, "last": 0}


def links_up(cls):
    orig = cls.setUp

    def setUp(self):
        orig(self)
        old = qqmanager.link_status
        qqmanager.link_status = lambda app_id, link_id: dict(UP)
        self.addCleanup(setattr, qqmanager, "link_status", old)
    cls.setUp = setUp
    return cls
