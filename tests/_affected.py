"""根据改了哪些文件，挑出该跑的测试文件（tests/run.py --changed 用）。

规则（宁可多跑，不漏跑）：
- 改了测试文件本身 → 跑它；改了 tests/ 下的帮手文件（_linkup.py 等）→ 跑导入它的测试。
- 改了 src/changri/ 下的 .py → 跑「直接导入这个模块」的测试。
  没有测试直接导入它（比如 plugins/giftshop.py）→ 顺着「谁导入了它」往上找一层、再一层，
  直到找到有测试的模块为止。
- 改了模板、样式、.sql 迁移等非 .py 文件 → 当成它所在的包里所有模块都改了
  （admin/templates/*.html 算 changri.admin 包，storage/migrations/*.sql 算 changri.storage 包）。
- 按名字动态加载的也算：测试里写了 "changri.plugins.giftshop" 这种字符串；
  扩展 features/<id>/ 下的文件，测试里出现 ext:<id>、"<id>" 或页面路径 /<id> 也算。
- 文件名里带模块名的测试也算（改 core/storycal.py 会跑 test_storycal）。
- 改了 src/ 下任何 .py 都顺带跑 test_static（pyflakes，几秒）。
- 改了 src/ 和 tests/ 以外的东西（docs、packaging、tools）不挑测试，只提示一句。

只看静态 import（包括函数里面的 import），不看运行时动态导入。漏掉的发版时全量测试会兜住。
"""
import ast
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SRC = ROOT / "src"


def _modules() -> dict[str, Path]:
    mods = {}
    for p in SRC.rglob("*.py"):
        parts = list(p.relative_to(SRC).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            mods[".".join(parts)] = p
    return mods


def _imports(path: Path, pkg: str, mods: dict) -> set[str]:
    """path 里 import 到的 src 模块（解析到最近的、真实存在的模块名）。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return set()
    names = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            names.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom):
            base = n.module or ""
            if n.level:
                up = pkg.split(".")[: max(0, len(pkg.split(".")) - n.level + 1)] if pkg else []
                base = ".".join(up + ([base] if base else []))
            names.add(base)
            names.update(f"{base}.{a.name}" for a in n.names)   # from changri.core import gifts
    out = set()
    for m in names:
        while m and m not in mods:
            m = m.rpartition(".")[0]
        if m:
            out.add(m)
    return out


def changed_files(base: str = "main") -> list[Path]:
    """相对 base 改过的文件 + 没提交的改动 + 新文件，只算本目录（长日bot/）下的。"""
    def git(*args):
        r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"git {' '.join(args)} 出错：{r.stderr.strip()}")
        return [ln for ln in r.stdout.splitlines() if ln]
    files = set(git("diff", "--relative", "--name-only", f"{base}...HEAD"))
    files |= set(git("diff", "--relative", "--name-only", "HEAD"))
    files |= set(git("ls-files", "--others", "--exclude-standard"))
    return sorted(ROOT / f for f in files)


def pick(changed: list[Path]) -> tuple[list[str], list[str]]:
    """返回 (要跑的测试文件名 stem 列表, 说明)。"""
    mods = _modules()
    graph = {}
    for m, p in mods.items():
        pkg = m if p.name == "__init__.py" else m.rpartition(".")[0]
        graph[m] = _imports(p, pkg, mods)
    users: dict[str, set[str]] = {}            # 模块 → 导入它的模块
    for m, deps in graph.items():
        for d in deps:
            users.setdefault(d, set()).add(m)

    test_files = sorted(HERE.glob("test_*.py"))
    helpers = {p.stem for p in HERE.glob("_*.py")}
    t_imports = {t.stem: _imports(t, "", mods) for t in test_files}
    t_text = {t.stem: t.read_text(encoding="utf-8") for t in test_files}

    def direct_tests(m: str) -> set[str]:
        leaf = m.rpartition(".")[2]
        hit = {t for t, imps in t_imports.items() if m in imps}
        # 按名字动态加载的：nonebot.load_plugin("changri.plugins.giftshop")
        pat = re.compile(re.escape(m) + r"(?![\w.])")
        hit |= {t for t, txt in t_text.items() if pat.search(txt)}
        # 扩展（features/<id>/）靠 id 开关：ext:bottle、"bottle"
        fp = m.split(".")
        if len(fp) >= 3 and fp[:2] == ["changri", "features"]:
            fid = re.compile(r"(ext:|[\"'])" + re.escape(fp[2]) + r"[\"']"          # ext:bottle、"bottle"
                             + r"|/" + re.escape(fp[2]) + r"s?(?=[/\"'?])")           # 页面路径 /b/111/collect、/p/me/bottles
            hit |= {t for t, txt in t_text.items() if fid.search(txt)}
        if leaf not in ("__init__", "admin", "app", "phone", "core", "changri"):
            hit |= {t for t in t_imports if leaf in t.removeprefix("test_").split("_") or t == f"test_{leaf}"}
        return hit

    picked, notes = set(), []
    for f in changed:
        try:
            rel = f.relative_to(ROOT)
        except ValueError:
            continue
        if rel.parts[0] == "tests":
            if rel.name.startswith("test_") and rel.suffix == ".py":
                if f.exists():
                    picked.add(rel.stem)
            elif rel.stem in helpers:
                hit = {t for t, txt in t_text.items() if rel.stem in txt}
                picked |= hit
                notes.append(f"{rel} → {len(hit)} 个用到它的测试")
            elif rel.name == "run.py" or rel.name == "_affected.py":
                notes.append(f"{rel}：测试工具本身，自己试一下 run.py 就行")
            continue
        if rel.parts[0] != "src":
            notes.append(f"{rel}：不在 src/ 里，没有对应的测试")
            continue
        if f.suffix == ".py":
            picked.add("test_static")          # pyflakes 查没导入的名字，几秒钟，改了 .py 都跑
            parts = list(f.relative_to(SRC).with_suffix("").parts)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            start = {".".join(parts)}
        else:
            d = f.parent
            while d != SRC and not (d / "__init__.py").exists():
                d = d.parent
            pkg = ".".join(d.relative_to(SRC).parts)
            start = {m for m in mods if m == pkg or m.startswith(pkg + ".")}
        # 一层一层往「谁导入了它」找，直到有测试
        seen, frontier, depth = set(start), set(start), 0
        while frontier:
            hit = set().union(*(direct_tests(m) for m in frontier))
            if hit:
                picked |= hit
                if depth:
                    notes.append(f"{rel}：没有测试直接测它，往上找了 {depth} 层（{'、'.join(sorted(frontier))[:80]}）")
                break
            nxt = set().union(*(users.get(m, set()) for m in frontier)) - seen
            seen |= nxt
            frontier, depth = nxt, depth + 1
        else:
            notes.append(f"{rel}：找不到相关测试，建议给它补一个")
    return sorted(picked), notes
