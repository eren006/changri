"""旧入口（老的 systemd 服务 / start.bat 还在用 `python bot.py`）。新安装用 `python -m changri run`。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from changri.__main__ import main  # noqa: E402

sys.exit(main(["run"]))
