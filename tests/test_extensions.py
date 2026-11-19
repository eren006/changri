import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
_tmp = Path(tempfile.mkdtemp())
os.environ["CHANGRI_DB_PATH"] = str(_tmp / "x.db")
EXT_TMP = _tmp / "extensions"
os.environ["CHANGRI_EXT_DIR"] = str(EXT_TMP)

import changri.admin as admin_mod  # noqa: E402

os.environ.pop("ADMIN_PASSWORD", None)
os.environ.pop("FLASK_SECRET_KEY", None)
from changri.admin import create_app  # noqa: E402
from changri.core import extensions as E, updater, webusers  # noqa: E402
from changri.core.db import get_config, get_conn, init_db  # noqa: E402

DEMO = ROOT / "src" / "changri" / "extdocs" / "示例扩展" / "hello_demo"


def demo_zip(manifest_patch=None, drop=None, extra=None, flat=False) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        m = json.loads((DEMO / "manifest.json").read_text(encoding="utf-8"))
        m.update(manifest_patch or {})
        prefix = "" if flat else "hello_demo/"
        if drop != "manifest":
            z.writestr(prefix + "manifest.json", json.dumps(m, ensure_ascii=False))
        if drop != "init":
            z.writestr(prefix + "__init__.py", (DEMO / "__init__.py").read_text(encoding="utf-8"))
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return buf.getvalue()


def token(client, path):
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="_csrf" value="([0-9a-f]+)"', html).group(1)


class _PatchedDir(unittest.TestCase):
    """changri.core.extensions 在导入时就定了 USER_DIR，一起跑多个测试文件时环境变量来不及生效，所以这里直接替换，别写进真实的 extensions/。"""
    def setUp(self):
        p = mock.patch.object(E, "USER_DIR", EXT_TMP)
        p.start()
        self.addCleanup(p.stop)


class ExtensionCoreTests(_PatchedDir):
    def setUp(self):
        super().setUp()
        init_db()
        import shutil
        shutil.rmtree(EXT_TMP, ignore_errors=True)
        with get_conn() as c:
            c.execute("DELETE FROM config")

    def test_manifest_validation(self):
        ok = json.loads((DEMO / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(E.validate_manifest(ok, "hello_demo"), "")
        self.assertIn("一致", E.validate_manifest(ok, "other"))
        self.assertIn("重名", E.validate_manifest({**ok, "id": "dice"}))
        self.assertIn("id", E.validate_manifest({**ok, "id": "Bad-Id"}))
        self.assertIn("name", E.validate_manifest({**ok, "name": ""}))
        self.assertIn("更新", E.validate_manifest({**ok, "api": 99}))
        self.assertIn("type", E.validate_manifest({**ok, "settings": [{"key": "a", "type": "weird"}]}))
        self.assertIn("key", E.validate_manifest({**ok, "settings": [{"key": "a b", "type": "int"}]}))

    def test_install_ok_and_default_off(self):
        ok, msg = E.install_zip(demo_zip())
        self.assertTrue(ok, msg)
        self.assertTrue((EXT_TMP / "hello_demo" / "__init__.py").is_file())
        [m] = E.scan_user()
        self.assertEqual(m["id"], "hello_demo")
        self.assertEqual(m["error"], "")
        item = next(i for i in E.list_all() if i["id"] == "hello_demo")
        self.assertFalse(item["enabled"])                                  # 上传的默认关
        E.set_enabled("hello_demo", True)
        self.assertTrue(next(i for i in E.list_all() if i["id"] == "hello_demo")["enabled"])
        self.assertTrue(E.pending_restart())

    def test_install_flat_zip_and_replace(self):
        self.assertTrue(E.install_zip(demo_zip(flat=True))[0])
        ok, msg = E.install_zip(demo_zip())
        self.assertFalse(ok)
        self.assertIn("覆盖", msg)
        self.assertTrue(E.install_zip(demo_zip(), replace=True)[0])

    def test_install_rejections(self):
        self.assertIn("不是有效", E.install_zip(b"not a zip")[1])
        self.assertIn("manifest", E.install_zip(demo_zip(drop="manifest"))[1])
        self.assertIn("__init__", E.install_zip(demo_zip(drop="init"))[1])
        self.assertIn("不安全", E.install_zip(demo_zip(extra={"../evil.py": "x"}))[1])
        self.assertIn("不安全", E.install_zip(demo_zip(extra={"/abs.py": "x"}))[1])
        self.assertIn("重名", E.install_zip(demo_zip({"id": "season"}))[1])
        self.assertFalse(EXT_TMP.exists() and any(EXT_TMP.iterdir()))      # 失败不留残渣
        big = io.BytesIO()
        with zipfile.ZipFile(big, "w") as z:
            z.writestr("a/manifest.json", "{}")
        self.assertIn("太大", E.install_zip(b"x" * (E.MAX_ZIP + 1))[1])

    def test_settings_typed(self):
        E.install_zip(demo_zip())
        self.assertEqual(E.get_setting("hello_demo", "greeting"), "你好呀")        # 默认值
        self.assertEqual(E.get_setting("hello_demo", "times"), 1)
        self.assertIs(E.get_setting("hello_demo", "loud"), False)
        n = E.save_settings("hello_demo", {"greeting": "嗨", "times": "3", "loud": "1"})
        self.assertEqual(n, 3)
        self.assertEqual(E.get_setting("hello_demo", "greeting"), "嗨")
        self.assertEqual(E.get_setting("hello_demo", "times"), 3)
        self.assertIs(E.get_setting("hello_demo", "loud"), True)
        E.save_settings("hello_demo", {"greeting": "嗨", "times": "不是数字"})      # 非法的 int 不覆盖
        self.assertEqual(E.get_setting("hello_demo", "times"), 3)
        self.assertIs(E.get_setting("hello_demo", "loud"), False)                 # 没勾选 = 关

    def test_delete(self):
        E.install_zip(demo_zip())
        self.assertTrue(E.delete_user("hello_demo"))
        self.assertFalse((EXT_TMP / "hello_demo").exists())
        self.assertFalse(E.delete_user("hello_demo"))
        self.assertFalse(E.delete_user("../x"))

    def test_nav_and_groups_follow_switches(self):
        self.assertEqual(E.disabled_nav(), {"rewards_page", "alarm_page", "giftshop_page", "gacha_page", "sighting_page", "bottles_page", "precall_admin", "paidan_page"})      # 结戏奖励、加百列闹钟默认关（小手机 0.22.4 起默认开）；排单是个性化模块，默认关
        E.set_enabled("rewards", True)
        E.set_enabled("alarm", True)
        E.set_enabled("phone", True)
        E.set_enabled("giftshop", True)
        E.set_enabled("gacha", True)
        E.set_enabled("moments", True)
        E.set_enabled("masquerade", True)
        E.set_enabled("sighting", True)
        E.set_enabled("precall", True)
        E.set_enabled("bottle", True)
        E.set_enabled("paidan", True)
        self.assertEqual(E.disabled_nav(), set())
        self.assertEqual(E.disabled_groups(), {"心愿", "混乱短信", "晚餐", "点歌", "写信综", "踩点", "申请加入", "超时提醒"})   # 这些默认关（朋友圈、目击已在上面打开）
        E.set_enabled("lovemail", False)
        E.set_enabled("dice", False)
        self.assertIn("lovemail_page", E.disabled_nav())
        self.assertEqual(E.disabled_groups(), {"心动信", "跑团骰子", "心愿", "混乱短信", "晚餐", "点歌", "写信综", "踩点", "申请加入", "超时提醒"})
        E.set_enabled("entrance", False)
        self.assertNotIn("season_page", E.disabled_nav())                      # 季度是核心，页面一直在
        self.assertNotIn("season", [e["id"] for e in E.OFFICIAL])
        self.assertNotIn("decks", [e["id"] for e in E.OFFICIAL])               # 牌堆 0.22.6 起是核心，一直开着
        self.assertIn("changri.plugins.decks", E.CORE_MODULES)

    # ---------- 规范检查 / 崩溃保护 / 模板 ----------
    def test_check_code(self):
        bad = {"__init__.py": b"import sys\nsys.exit(1)\n"}
        self.assertTrue(E.check_code(bad)[0])
        self.assertTrue(E.check_code({"__init__.py": b"def x(:\n"})[0])
        self.assertTrue(E.check_code({"__init__.py": "from changri_api import on_command\nc = on_command('约会')\n".encode()})[0])      # 和主程序重名
        self.assertTrue(E.check_code({"__init__.py": "from changri_api import on_command\nc = on_command('小游戏', aliases={'时间线'})\n".encode()})[0])
        errs, warns = E.check_code({"__init__.py": b"import time, requests\nfrom core import db\ntime.sleep(1)\n"})
        self.assertEqual(errs, [])
        self.assertTrue(len(warns) >= 3)
        errs, warns = E.check_code({"__init__.py": b"from changri_api import on_message\nm = on_message(priority=1, block=True)\n"})
        self.assertEqual(errs, []); self.assertTrue(len(warns) >= 2)
        self.assertEqual(E.check_code({"__init__.py": "from changri_api import on_command\nc = on_command('小游戏', priority=10)\n".encode()}), ([], []))

    def test_install_rejects_bad_code_and_warns(self):
        z = demo_zip()
        import zipfile as zf
        buf = io.BytesIO()
        with zf.ZipFile(buf, "w") as w:
            w.writestr("hello_demo/manifest.json", json.dumps({"id": "hello_demo", "name": "x", "version": "1"}))
            w.writestr("hello_demo/__init__.py", "import os\nos._exit(1)\n")
        ok, msg = E.install_zip(buf.getvalue())
        self.assertFalse(ok); self.assertIn("没有安装", msg); self.assertFalse((E.USER_DIR / "hello_demo").exists())
        buf = io.BytesIO()
        with zf.ZipFile(buf, "w") as w:
            w.writestr("hello_demo/manifest.json", json.dumps({"id": "hello_demo", "name": "x", "version": "1"}))
            w.writestr("hello_demo/__init__.py", "import time\ntime.sleep(0)\n")
        ok, msg = E.install_zip(buf.getvalue())
        self.assertTrue(ok); self.assertIn("要留意", msg)
        self.assertTrue(E.scan_user()[0]["warnings"])

    def test_boot_guard_disables_suspects(self):
        from changri.core.db import set_config as sc, get_config as gc
        E.install_zip(demo_zip())
        sc("ext:hello_demo", "1")
        E.confirm_boot()
        self.assertEqual(gc("ext_boot_guard", ""), "")
        sc("ext_boot_guard", "hello_demo")       # 模拟：上次启动加载了它，但没来得及确认就崩了
        with mock.patch("nonebot.load_plugin", return_value=object()):       # 只看崩溃保护的逻辑，不真的加载插件
            failed = E.load_all()
        self.assertIn("hello_demo", failed)
        self.assertIn("自动停用", failed["hello_demo"])
        self.assertEqual(gc("ext:hello_demo", ""), "0")

    def test_ai_prompt_and_skeleton(self):
        t = E.ai_prompt()
        for key in ("规范", "manifest.json", "changri_api", "我想要的扩展", "sys.exit"):
            self.assertIn(key, t)
        import zipfile as zf
        names = zf.ZipFile(io.BytesIO(E.skeleton_zip())).namelist()
        self.assertIn("my_extension/manifest.json", names)
        ok, _ = E.install_zip(E.skeleton_zip())          # 骨架本身必须能通过检查
        self.assertTrue(ok)

    def test_user_extensions_live_in_data(self):
        """用户扩展放在 data/extensions：更新只装新的版本目录，碰不到它。"""
        from changri.paths import DATA_DIR
        self.assertTrue(str(E.USER_DIR).startswith(str(DATA_DIR)) or os.environ.get("CHANGRI_EXT_DIR"))


class LoadInFreshProcessTests(unittest.TestCase):
    """NoneBot 的插件是进程级的，加载与否要在新进程里看。"""

    def run_py(self, code: str, env_extra: dict) -> str:
        env = {**os.environ, **env_extra, "QQ_BOTS": "[]", "DRIVER": "~fastapi+~httpx+~websockets"}
        r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        return r.stdout.strip().splitlines()[-1]

    def _tmp(self):
        """NoneBot 按「相对项目根的路径」给插件起模块名，所以测试用的扩展目录要建在项目里面，用完删掉。"""
        import shutil
        import uuid
        d = ROOT / "tests" / f"_ext_{uuid.uuid4().hex[:8]}"
        d.mkdir()
        self.addCleanup(shutil.rmtree, d, True)
        return d

    def test_disabled_official_and_user_ext(self):
        d = self._tmp()
        ext_dir = d / "extensions"
        (ext_dir).mkdir()
        import shutil
        shutil.copytree(DEMO, ext_dir / "hello_demo")
        (ext_dir / "broken").mkdir()
        (ext_dir / "broken" / "manifest.json").write_text('{"id": "broken", "name": "坏的", "version": "1", "api": 1}', encoding="utf-8")
        (ext_dir / "broken" / "__init__.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
        code = '''
import nonebot
from changri.core import extensions as E
from changri.core.db import init_db, set_config
init_db()
set_config("ext:dice", "0")
set_config("ext:hello_demo", "1")
set_config("ext:broken", "1")
nonebot.init(qq_bots=[], log_level="ERROR")
failed = E.load_all()
names = sorted(p.name for p in nonebot.get_loaded_plugins())
print("RESULT", "dice" in names, "season" in names, "social" in names, "hello_demo" in names, sorted(failed))
'''
        out = self.run_py(code, {"CHANGRI_DB_PATH": str(d / "f.db"), "CHANGRI_EXT_DIR": str(ext_dir)})
        self.assertEqual(out, "RESULT False True True True ['broken']")      # 骰子没加载；核心和别的官方扩展在；示例扩展加载；坏的只影响它自己

    def test_default_user_ext_not_loaded(self):
        d = self._tmp()
        ext_dir = d / "extensions"
        ext_dir.mkdir()
        import shutil
        shutil.copytree(DEMO, ext_dir / "hello_demo")
        code = '''
import nonebot
from changri.core import extensions as E
from changri.core.db import init_db
init_db()
nonebot.init(qq_bots=[], log_level="ERROR")
E.load_all()
print("RESULT", "hello_demo" in [p.name for p in nonebot.get_loaded_plugins()])
'''
        self.assertEqual(self.run_py(code, {"CHANGRI_DB_PATH": str(d / "g.db"), "CHANGRI_EXT_DIR": str(ext_dir)}), "RESULT False")


class ExtensionAdminTests(_PatchedDir):
    def setUp(self):
        super().setUp()
        init_db()
        import shutil
        shutil.rmtree(EXT_TMP, ignore_errors=True)
        with get_conn() as c:
            for t in ("web_users", "config"):
                c.execute(f"DELETE FROM {t}")
        admin_mod._fails.clear()
        self.c = create_app().test_client()
        webusers.create_user("boss", "longenough1", "owner")
        webusers.create_user("helper", "longenough2", "admin")

    def login(self, name="boss", pw="longenough1"):
        self.c.get("/logout")
        self.c.post("/login", data={"username": name, "password": pw, "_csrf": token(self.c, "/login")})

    def test_page_flow(self):
        self.login()
        core = self.c.get("/extensions").get_data(as_text=True)                 # 核心功能页：开不了关的
        aux = self.c.get("/extensions?tab=aux").get_data(as_text=True)          # 辅助功能页：可开关的
        self.assertIn("核心功能", core); self.assertIn("辅助功能", core)
        self.assertNotIn("跑团骰子", core); self.assertIn("跑团骰子", aux)
        page = core + aux
        for name in ("出场", "心动信", "关系线", "跑团骰子"):
            self.assertIn(name, page)
        r = self.c.post("/extensions/upload", data={"file": (io.BytesIO(demo_zip()), "x.zip"), "_csrf": token(self.c, "/extensions")},
                        content_type="multipart/form-data", follow_redirects=True)
        self.assertIn("已安装", r.get_data(as_text=True))
        self.assertIn("打招呼示例", r.get_data(as_text=True))
        r = self.c.post("/extensions/toggle", data={"id": "hello_demo", "on": "1", "_csrf": token(self.c, "/extensions")}, follow_redirects=True)
        self.assertIn("有改动要重启才生效", r.get_data(as_text=True))
        r = self.c.post("/extensions/hello_demo/settings", data={"greeting": "嗨", "times": "2", "_csrf": token(self.c, "/extensions?tab=settings")},
                        follow_redirects=True)
        self.assertIn("已保存", r.get_data(as_text=True))
        html = self.c.get("/extensions?tab=aux").get_data(as_text=True)
        self.assertIn('value="嗨"', html)
        with mock.patch("changri.core.extensions.restart_soon") as rs:
            r = self.c.post("/extensions/restart", data={"_csrf": token(self.c, "/extensions")}, follow_redirects=True)
            rs.assert_called_once()
        self.assertIn("正在重启", r.get_data(as_text=True))
        r = self.c.post("/extensions/hello_demo/delete", data={"_csrf": token(self.c, "/extensions")}, follow_redirects=True)
        self.assertIn("已删除", r.get_data(as_text=True))

    def test_settings_live_inside_each_card(self):
        self.login()
        page = self.c.get("/extensions").get_data(as_text=True) + self.c.get("/extensions?tab=aux").get_data(as_text=True)
        for card in ("set-base", "set-social", "set-gift", "set-play", "set-lovemail", "set-dice", "set-relations"):
            self.assertIn(f'id="{card}"', page)                                  # 每张卡片自带「进入设置」弹窗
        self.assertIn("进入设置", page)
        self.assertNotIn('id="set-season"', page)                                # 季度没有设置项，只有管理页入口
        self.assertEqual(self.c.get("/settings").status_code, 302)               # 旧的设置页跳到功能页
        r = self.c.post("/settings/心动信", data={"lovemail_default_limit": "7", "_csrf": token(self.c, "/extensions")}, follow_redirects=True)
        self.assertIn("已保存", r.get_data(as_text=True))
        from changri.core.db import get_config as gc
        self.assertEqual(gc("lovemail_default_limit", ""), "7")
        self.assertEqual(self.c.post("/settings/不存在", data={"_csrf": token(self.c, "/extensions")}).status_code, 404)

    def test_disabled_extension_hides_nav_and_route(self):
        self.login()
        self.c.post("/extensions/toggle", data={"id": "lovemail", "on": "0", "_csrf": token(self.c, "/extensions")})
        page = self.c.get("/extensions?tab=aux").get_data(as_text=True)
        self.assertNotIn('href="/lovemail"', page)
        self.assertEqual(self.c.get("/lovemail").status_code, 404)             # 关了之后直接输网址也进不去
        self.assertEqual(self.c.get("/season").status_code, 200)               # 季度是核心，一直在
        self.c.post("/extensions/toggle", data={"id": "lovemail", "on": "1", "_csrf": token(self.c, "/extensions")})
        self.assertEqual(self.c.get("/lovemail").status_code, 200)

    def test_ai_tab_and_downloads(self):
        self.login()
        page = self.c.get("/extensions?tab=ai").get_data(as_text=True)
        self.assertIn("复制全部", page); self.assertIn("我想要的扩展", page)
        self.assertEqual(self.c.get("/extensions/skeleton.zip").status_code, 200)
        self.assertIn("规范", self.c.get("/extensions/ai-prompt.md").get_data(as_text=True))

    def test_pool_page(self):
        from changri.core import pool
        pool.register("B1", "OPENID_ABC", real_gid="555", set_name="A组")
        self.login()
        page = self.c.get("/backup-pool").get_data(as_text=True)
        self.assertIn("A组", page); self.assertIn("555", page)
        gid = pool.is_pool_group("B1", "OPENID_ABC")["id"]
        self.c.post(f"/backup-pool/{gid}/save", data={"real_gid": "777", "label": "一号", "set_name": "B组", "_csrf": token(self.c, "/backup-pool")})
        self.assertEqual(pool.get(gid)["real_gid"], "777")
        self.assertIn("群名改成「长日备用」", self.c.get("/backup-pool?view=guide").get_data(as_text=True))
        self.c.post("/backup-pool/free-name", data={"free_name": "候场室", "_csrf": token(self.c, "/backup-pool")})
        self.assertIn("群名改成「候场室」", self.c.get("/backup-pool?view=guide").get_data(as_text=True))
        from changri.core import qqpool
        self.assertEqual(qqpool.free_name(), "候场室")                          # 小号的群也用同一个名字
        self.c.post("/backup-pool/free-name", data={"free_name": "", "_csrf": token(self.c, "/backup-pool")})
        self.assertEqual(qqpool.free_name(), "长日备用")                        # 清空就回到默认
        self.c.post(f"/backup-pool/{gid}/toggle", data={"on": "0", "_csrf": token(self.c, "/backup-pool")})
        self.assertEqual(pool.get(gid)["in_pool"], 0)
        self.c.post(f"/backup-pool/{gid}/delete", data={"_csrf": token(self.c, "/backup-pool")})
        self.assertIsNone(pool.get(gid))

    def test_forum_page(self):
        from changri.core import forum, roles as R
        R.create_role("发帖人", "B1", "FG", "FM")
        rid = R.get_role_by_name("发帖人")["id"]
        pid = forum.new_post(rid, "匿名路人", "你好论坛")
        forum.new_reply(pid, rid, "路人乙", "沙发")
        self.login()
        page = self.c.get("/forum").get_data(as_text=True)
        self.assertIn("匿名路人", page); self.assertIn("发帖人", page); self.assertIn("沙发", page)
        self.c.post(f"/forum/{pid}/2/delete", data={"_csrf": token(self.c, "/forum")})
        self.assertIn("该楼层已被删除", self.c.get("/forum").get_data(as_text=True))
        self.c.post(f"/forum/{pid}/delete", data={"_csrf": token(self.c, "/forum")})
        self.assertNotIn("你好论坛", self.c.get("/forum").get_data(as_text=True))
        R.delete_role(rid)

    def test_non_owner_can_view_but_not_change(self):
        self.login("helper", "longenough2")
        self.assertEqual(self.c.get("/extensions").status_code, 200)
        t = token(self.c, "/extensions")
        for path, data in (("/extensions/toggle", {"id": "dice", "on": "0"}), ("/extensions/restart", {}), ("/extensions/hello_demo/delete", {})):
            self.assertEqual(self.c.post(path, data={**data, "_csrf": t}).status_code, 403, path)
        self.assertEqual(get_config("ext:dice", "1"), "1")

    def test_unknown_extension_toggle_404(self):
        self.login()
        self.assertEqual(self.c.post("/extensions/toggle", data={"id": "nope", "on": "1", "_csrf": token(self.c, "/extensions")}).status_code, 404)

    def test_storycal_page_and_personal_card(self):
        self.login()
        self.assertIn("故事历", self.c.get("/extensions?tab=custom").get_data(as_text=True))
        self.assertEqual(self.c.get("/storycal").status_code, 404)             # 关着时页面 404
        from changri.core import extensions as ec
        ec.set_enabled("storycal", True)
        self.assertIn("年号", self.c.get("/storycal").get_data(as_text=True))
        r = self.c.post("/storycal", data={"era": "康熙十四年", "d0": "3月1日-3月31日", "_csrf": token(self.c, "/storycal")}, follow_redirects=True)
        html = r.get_data(as_text=True)
        self.assertIn("已保存", html); self.assertIn('data-s="3,1" data-e="3,31"', html)

    def test_personal_tab_default_off_and_reset(self):
        self.login()
        from changri.core import extensions as ec
        demo = {"id": "tiny_demo", "name": "小定制演示", "module": "changri.plugins.tiny_demo", "settings_group": "", "nav": [], "links": [],
                "description": "d", "personal": True, "default_on": False, "kind": "official", "version": "v", "author": "a", "homepage": "", "license": "l"}
        with mock.patch.object(ec, "OFFICIAL", ec.OFFICIAL + [demo]):
            self.assertFalse(ec.is_enabled("tiny_demo"))                           # 默认关
            page = self.c.get("/extensions?tab=custom").get_data(as_text=True)
            self.assertIn("个性化模块", page); self.assertIn("小定制演示", page)
            self.assertNotIn("小定制演示", self.c.get("/extensions?tab=aux").get_data(as_text=True))
            self.assertNotIn("小定制演示", self.c.get("/extensions").get_data(as_text=True))
            ec.set_enabled("tiny_demo", True)
            self.assertTrue(ec.is_enabled("tiny_demo"))
            self.c.post("/init", data={"mode": "reset", "_csrf": token(self.c, "/extensions")})
            self.assertFalse(ec.is_enabled("tiny_demo"))                           # 重置后又关上


if __name__ == "__main__":
    unittest.main()
