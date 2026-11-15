"""程序里用到的目录，全部从这里取，别的地方不要再自己拼 parent.parent。

PKG_DIR   changri 包本身（代码、模板、字体、黑名单这些随程序走的东西）
APP_ROOT  开发时 = 仓库里的「长日bot/」；打包安装后 = 安装目录（里面有 runtime/、versions/、current）
DATA_DIR  所有会变的东西：数据库、config.toml、密钥、日志、备份、用户扩展。更新程序永远不碰它。
          默认 APP_ROOT/data（Windows 免安装包解压即用，备份就是复制这个文件夹）；
          环境变量 CHANGRI_HOME 可以指到别处（Linux 安装版的服务里设成 /var/lib/changri-bot）。
"""
from __future__ import annotations

import os
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent
ASSETS_DIR = PKG_DIR / "assets"
EXT_DOCS_DIR = PKG_DIR / "extdocs"


def _app_root() -> Path:
    # 打包后代码在 <安装目录>/versions/<版本>/changri/，往上找带 current 文件的那一层；开发时是 src/ 的上一层
    for p in PKG_DIR.parents:
        if (p / "current").is_file() and (p / "versions").is_dir():
            return p
    return PKG_DIR.parent.parent


APP_ROOT = _app_root()
DATA_DIR = Path(os.environ.get("CHANGRI_HOME") or APP_ROOT / "data").resolve()
LEGACY_ENV = APP_ROOT / ".env"          # 老版本的配置文件；第一次启动导入 config.toml 后就不再用
