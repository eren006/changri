"""命令入口。安装包里用启动脚本调用（Windows：Start-Changri.bat；Linux：./changri），开发时 python -m changri。

  run                 前台运行机器人 + 设置平台 + 小手机
  supervise           看门进程：跑 run，崩了自动拉起，更新后换版本，新版本起不来自动回滚
  service install     设为开机自启（Linux：sudo；Windows：以管理员身份运行）
  service uninstall   取消开机自启
  service status      看开机自启装没装、在不在跑
  doctor              自检：端口、外网、公网 IP、数据目录
  backup              马上备份所有数据库到 data/backups/
  reset-owner         主账号忘了密码：随机设一个新密码并打印出来（只能在服务器上运行）
  update              检查并安装新版本（和设置平台「更新」页一样；装完要重启才生效）
  migrate-legacy DIR  从老版本（0.19 及以前）的安装目录搬数据过来
  version             打印版本号
"""
from __future__ import annotations

import argparse
import sys


def _utf8_console() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")       # Windows 控制台默认不是 UTF-8
        except (AttributeError, ValueError):
            pass


def _port_busy() -> int | None:
    """启动前看设置平台的端口：被占了就说清楚是谁、怎么办，记一条「配置」报错，退出码 PORT_BUSY（看门进程不再重启）。
    不自动换端口，原因见 platform/portcheck.py。能用返回 None。"""
    from changri import config
    from changri.platform import portcheck
    try:
        config.ensure()
        host, port = str(config.get("host")), config.admin_port()
    except Exception:  # noqa: BLE001  配置读不了：让后面正常启动的流程去报
        return None
    if portcheck.can_bind(host, port):
        return None
    mine = portcheck.changri_on(port)
    msg = portcheck.busy_message(port, "" if mine else portcheck.who_listens(port), changri=mine)
    print(msg, flush=True)
    if mine:
        return 0
    try:
        from changri.core import errlog
        errlog.record("config", "启动:端口", msg)
    except Exception:  # noqa: BLE001
        pass
    return portcheck.PORT_BUSY


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    ap = argparse.ArgumentParser(prog="changri", description="长日bot")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("run", help="前台运行（默认）")
    sub.add_parser("supervise", help="看门进程：崩了自动拉起、更新后换版本")
    sv = sub.add_parser("service", help="开机自启")
    sv.add_argument("action", choices=["install", "uninstall", "status"])
    sub.add_parser("doctor", help="自检")
    sub.add_parser("backup", help="备份所有数据库")
    sub.add_parser("reset-owner", help="主账号忘了密码：重设一个新密码")
    ml = sub.add_parser("migrate-legacy", help="从老版本安装目录搬数据")
    ml.add_argument("old_dir")
    sub.add_parser("update", help="检查并安装新版本")
    sub.add_parser("version", help="版本号")
    a = ap.parse_args(argv)

    if a.cmd in (None, "run"):
        if (code := _port_busy()) is not None:
            return code
        from changri import app
        app.run()
        return 0
    if a.cmd == "supervise":
        from changri.platform import supervisor
        return supervisor.run()
    if a.cmd == "service":
        from changri.platform import service
        try:
            print({"install": service.install, "uninstall": service.uninstall, "status": service.status}[a.action]())
        except RuntimeError as e:
            print(f"没有成功：{e}")
            return 1
        if a.action == "install":
            _print_first_run_hints()
        return 0
    if a.cmd == "doctor":
        from changri.platform import doctor
        text, ok = doctor.report()
        print(text)
        return 0 if ok else 1
    if a.cmd == "backup":
        from changri.platform import backup
        print(f"已备份到 {backup.make('manual')}")
        return 0
    if a.cmd == "reset-owner":
        from changri.core import webusers
        from changri.core.db import DB_PATH, init_db
        if not DB_PATH.exists():
            print(f"找不到数据库 {DB_PATH}。请在长日bot 的安装文件夹里运行这个命令；如果从没打开过设置平台，直接打开它按首次设置建主账号就行。")
            return 1
        init_db()
        res = webusers.reset_owner()
        if not res:
            print("还没有主账号：直接打开设置平台，按首次设置建一个就行。")
            return 1
        name, password = res
        print(f"主账号 {name} 的密码已重设为：{password}")
        print("旧密码已经失效。用这个密码登录设置平台后，到「账号」页改成你自己记得住的密码。")
        print("之前输错太多次被锁住的，现在也可以直接登录了。")
        return 0
    if a.cmd == "migrate-legacy":
        from pathlib import Path

        from changri.platform import legacy
        try:
            for line in legacy.migrate(Path(a.old_dir)):
                print("已搬：" + line)
        except RuntimeError as e:
            print(f"没有搬：{e}")
            return 1
        print("老目录没有改动，确认新版本没问题后可以自己删掉。")
        return 0
    if a.cmd == "update":
        from changri.core import updater
        try:
            res = updater.update_now()
        except updater.UpdateError as e:
            print(f"没有更新：{e}")
            return 1
        print(f"已装好 {res['to']}（原来是 {res['from']}，数据库已备份到 {res['backup']}）。")
        print("重启后生效：开机自启的 Linux 用 sudo systemctl restart changri-bot；Windows 和前台运行的，关掉再打开。")
        return 0
    if a.cmd == "version":
        from changri.platform import layout
        print(layout.package_version())
        return 0
    ap.print_help()
    return 2


def _print_first_run_hints() -> None:
    """刚装好：告诉装的人去哪打开、首次设置验证码在哪。"""
    import time

    from changri import config
    from changri.paths import DATA_DIR
    code_file = DATA_DIR / ".setup_code"
    for _ in range(20):                       # 程序第一次启动时才生成验证码，等它一会儿
        if code_file.exists():
            break
        time.sleep(1)
    try:
        from changri.core.netcheck import public_ip
        ip = public_ip()
    except Exception:  # noqa: BLE001
        ip = ""
    port = config.admin_port()
    print()
    print(f"设置平台：http://{ip or '服务器公网IP'}:{port}　（在这台机器上也可以开 http://127.0.0.1:{port}）")
    if code_file.exists():
        print(f"首次设置验证码：{code_file.read_text().strip()}　（只用这一次，建好账号后失效）")
    print(f"还要到云服务商控制台的「安全组」放行 {port} 端口（以后开小手机再放行 5092）。")


if __name__ == "__main__":
    sys.exit(main())
