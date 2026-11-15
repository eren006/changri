"""packaging/requirements.lock 必须包含程序真正用到的全部依赖（安装包里的运行环境只装锁定文件里的东西）。
拿开发环境里实际装好的依赖树对比：少了就失败（漏掉 exceptiongroup 的教训）。"""
import importlib.metadata as md
import re
import unittest
from pathlib import Path

LOCK = Path(__file__).resolve().parent.parent / "packaging" / "requirements.lock"
ROOTS = ["nonebot2[fastapi,httpx,websockets]", "nonebot-adapter-qq", "nonebot-plugin-apscheduler", "Flask", "python-dotenv",
         "a2wsgi", "pillow", "cryptography"]


def norm(n: str) -> str:
    return re.sub(r"[-_.]+", "-", n).lower()


def closure() -> dict[str, str]:
    try:
        from packaging.requirements import Requirement
    except ImportError:
        from pip._vendor.packaging.requirements import Requirement
    seen: dict[str, str] = {}
    done: set[tuple[str, frozenset]] = set()          # 同一个包、不同的 extras 要各处理一次（nonebot2 和 nonebot2[fastapi] 不一样）
    todo = [Requirement(r) for r in ROOTS]
    while todo:
        r = todo.pop()
        key = (norm(r.name), frozenset(r.extras))
        if key in done:
            continue
        done.add(key)
        try:
            d = md.distribution(r.name)
        except md.PackageNotFoundError:
            continue
        seen.setdefault(norm(r.name), d.version)
        for q in d.requires or []:
            q = Requirement(q)
            if q.marker and not any(q.marker.evaluate({"extra": e}) for e in (r.extras or {""})):
                continue
            todo.append(q)
    return seen


class LockTests(unittest.TestCase):
    def test_lock_covers_installed_dependencies(self):
        locked = {}
        for line in LOCK.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].split(";", 1)[0].strip()
            if line:
                name, _, ver = line.partition("==")
                locked[norm(name)] = ver
        need = closure()
        missing = sorted(set(need) - set(locked))
        self.assertEqual(missing, [], f"requirements.lock 里少了：{missing}")
        differ = {k: (locked[k], v) for k, v in need.items() if locked.get(k) != v}
        self.assertEqual(differ, {}, "锁定版本和开发环境里装的不一样（锁定, 开发）：先统一，免得测的和发的不是同一套")


if __name__ == "__main__":
    unittest.main()
