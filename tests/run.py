"""跑测试：每个 test_*.py 单独开一个进程（可以并行），最后汇总。

为什么不直接 `python -m unittest discover`：
- 各测试文件在导入时设 CHANGRI_DB_PATH 等环境变量，而 core/db.py 第一次导入就把库路径定死了。
  discover 先把所有文件导入一遍，结果大家共用第一个文件的库，一个文件开的开关（比如故事历）会漏到别的文件里。
- 有些测试文件不自己加 src 路径。在 worktree 里用主目录的 .venv（装的是主目录那份代码）跑，会测到旧代码。
这个脚本给每个进程设 PYTHONPATH=<本目录>/src，并且一个文件一个进程，两件事都解决。
另外每个进程的 CHANGRI_HOME 指到一个新的临时目录，测试再怎么写也碰不到真的 data/。

用法（在 长日bot/ 下）：
    .venv/bin/python tests/run.py                 # 全部
    .venv/bin/python tests/run.py storycal play   # 只跑文件名里带这些词的
    .venv/bin/python tests/run.py --changed       # 只跑跟改动有关的（相对 main 的提交 + 没提交的改动），平时开发用这个
    .venv/bin/python tests/run.py --changed --dry # 只列出会跑哪些，不跑
    .venv/bin/python tests/run.py -j 1            # 不并行（默认 = CPU 核数）
    .venv/bin/python tests/run.py -v              # 每个文件的输出都打印（默认只打印失败的）
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# 只测 tools/ 里作者自用工具的测试（主站服务器上的 console / errors / wishes 服务、发版脚本）：这些东西只在 Linux 主站
# 和作者的 Mac 上跑，不进安装包。Windows 上跳过，免得为用不到的平台改它们（文件锁、改名覆盖、sh 脚本都和 Linux 不一样）。
POSIX_ONLY = {"test_console_service", "test_errors_service", "test_wishes_service", "test_release_fetch"}


def run_one(path: Path) -> dict:
    env = dict(os.environ)
    env.pop("CHANGRI_DB_PATH", None)                  # 别带进外面设的库
    env["CHANGRI_HOME"] = tempfile.mkdtemp(prefix=f"changri-{path.stem}-")   # 兜底：没自己设库的测试也碰不到真的 data/
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src"), str(ROOT)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"                # 子进程的输出按 UTF-8 写、按 UTF-8 读；不然中文 Windows / CI 的控制台编码对不上，读输出就崩
    t = time.time()
    p = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", str(HERE), "-t", str(HERE), "-p", path.name],
                       cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = p.stdout + p.stderr
    ran = re.search(r"^Ran (\d+) tests?", out, re.M)
    counts = {k: int(v) for k, v in re.findall(r"(failures|errors|skipped)=(\d+)", out)}
    return {"name": path.stem, "ok": p.returncode == 0, "ran": int(ran.group(1)) if ran else 0,
            "failures": counts.get("failures", 0), "errors": counts.get("errors", 0) + (0 if ran else 1),
            "skipped": counts.get("skipped", 0), "secs": time.time() - t, "out": out}


def main() -> int:
    ap = argparse.ArgumentParser(description="长日bot 测试：一个文件一个进程")
    ap.add_argument("words", nargs="*", help="只跑文件名里带这些词的")
    ap.add_argument("-j", type=int, default=os.cpu_count() or 4, help="同时跑几个文件")
    ap.add_argument("-v", action="store_true", help="打印每个文件的输出")
    ap.add_argument("--changed", nargs="?", const="main", metavar="BASE",
                    help="只跑跟改动有关的测试（相对 BASE，默认 main）。规则见 tests/_affected.py")
    ap.add_argument("--dry", action="store_true", help="只列出要跑的文件")
    a = ap.parse_args()

    files = sorted(HERE.glob("test_*.py"))
    if a.changed:
        sys.path.insert(0, str(HERE))
        import _affected
        changed = _affected.changed_files(a.changed)
        names, notes = _affected.pick(changed)
        print(f"相对 {a.changed} 改了 {len(changed)} 个文件，挑出 {len(names)} 个测试文件")
        for n in notes:
            print(f"  · {n}")
        files = [f for f in files if f.stem in names]
        if not files:
            print("没有要跑的测试（发版时 tools/release.py 会跑全量）")
            return 0
    if a.dry:
        print("\n".join(f.stem for f in files))
        return 0
    if sys.platform == "win32":
        skipped = [f.stem for f in files if f.stem in POSIX_ONLY]
        files = [f for f in files if f.stem not in POSIX_ONLY]
        if skipped:
            print(f"Windows 上跳过只测作者工具的：{'、'.join(skipped)}")
    if a.words:
        files = [f for f in files if any(w in f.stem for w in a.words)]
    if not files:
        print("没有匹配的测试文件")
        return 1

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max(1, a.j)) as ex:
        results = list(ex.map(run_one, files))

    bad = [r for r in results if not r["ok"]]
    for r in results:
        if a.v or not r["ok"]:
            print(f"\n{'=' * 70}\n{r['name']}\n{'=' * 70}\n{r['out'].rstrip()}")
    tot = {k: sum(r[k] for r in results) for k in ("ran", "failures", "errors", "skipped")}
    slow = sorted(results, key=lambda r: -r["secs"])[:3]
    print(f"\n{len(files)} 个文件，{tot['ran']} 个测试，用时 {time.time() - t0:.0f} 秒"
          f"（最慢：{'、'.join(f'{r['name']} {r['secs']:.0f}s' for r in slow)}）")
    if bad:
        print(f"失败 {tot['failures']}、出错 {tot['errors']}、跳过 {tot['skipped']}；有问题的文件：{'、'.join(r['name'] for r in bad)}")
        return 1
    print(f"全部通过（跳过 {tot['skipped']}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
