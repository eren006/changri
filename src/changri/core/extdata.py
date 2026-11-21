"""扩展自己存的数据（changri_api 的 data_get / data_set / today_count / today_add 落在这里）。

每个机器人一份：bots/<AppID>/ext_data/<扩展id>.json，跟这个机器人的角色、季度放在一起。
角色编号是每个机器人各自从 1 排的，以前所有机器人共用 DATA_DIR/ext_data/ 一份，A 团的 3 号和 B 团的 3 号
就会共用同一条记录（比如签到：A 团 3 号签了，B 团 3 号再签就说「今天已经签过了」）。

升级上来的：第一次读写扩展数据时，把原来共用的那份复制给当时已有的每个机器人（各自的记录照旧都在，之后各写各的），
再把共用的文件夹改名成 ext_data_共用备份/ 留底。之后新加的机器人从空的开始，不会拿到别的团的数据。
一个机器人都没有、或者测试用的单库（CHANGRI_DB_PATH）时，照旧用 DATA_DIR/ext_data/。

按玩家本人存的（player_data_*，接口版本 4）放在 <扩展id>.player.json，{玩家编号: {键: 值}}，跨季度一直留着，收尾不清。
按季度存的（season_data_*）放在旁边的 <扩展id>.season.json，「收尾」时 clear_season() 整个删掉：
本季角色的连签、积分这类东西不会带进下一季，也不会一季季堆到 2MB 上限。扩展 id 里不能有点，不会和别的扩展撞名。
老插件（接口版本 3 之前写的）按角色存进 data_set 的记录（streak_3、连签:张三……），收尾时 clear_role_keys() 也清掉：
键拆开以后有一段正好是这季某个角色的编号或名字，就算这个角色的。猜错的可能有（比如 奖池_3），所以删之前先存进
<扩展id>.上季清理备份.json（每次收尾覆盖上一份）。下划线开头的键（today_count 的 _today:…）不动。
"""
import json
import re
import shutil
import threading
import time
from pathlib import Path

from . import db as _db
from ..paths import DATA_DIR

ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
MAX_BYTES = 2 * 1024 * 1024
LEGACY_BACKUP = "ext_data_共用备份"

lock = threading.RLock()


def _shared_dir() -> Path:
    return DATA_DIR / "ext_data"


def folder() -> Path:
    """当前机器人的扩展数据文件夹。"""
    bid = _db.current_bot()
    if not bid or _db.LEGACY_SINGLE:
        return _shared_dir()
    _split_shared()
    return _db.BOTS_DIR / bid / "ext_data"


def _split_shared() -> None:
    """把升级前共用的 ext_data/ 复制给每个已有的机器人，只做一次（做完共用文件夹就改名了）。"""
    shared = _shared_dir()
    if not shared.is_dir():
        return
    with lock:
        if not shared.is_dir():
            return
        bots = [b for b in _db.list_bot_ids(False) if b]
        if not bots:
            return
        files = [p for p in shared.iterdir() if p.is_file() and p.suffix == ".json"]
        for bid in bots:
            dest = _db.BOTS_DIR / bid / "ext_data"
            dest.mkdir(parents=True, exist_ok=True)
            for p in files:
                if not (dest / p.name).exists():          # 已经有自己的就不覆盖
                    shutil.copy2(p, dest / p.name)
        backup = DATA_DIR / LEGACY_BACKUP
        if backup.exists():
            backup = DATA_DIR / f"{LEGACY_BACKUP}_{int(time.time())}"
        shared.rename(backup)


def path(ext_id: str, season: bool = False, player: bool = False) -> Path:
    if not ID_RE.match(str(ext_id)):
        raise ValueError(f"扩展 id 不对：{ext_id!r}")
    return folder() / (f"{ext_id}.season.json" if season else f"{ext_id}.player.json" if player else f"{ext_id}.json")


def load(ext_id: str, season: bool = False, player: bool = False) -> dict:
    p = path(ext_id, season, player)                    # 扩展 id 不对直接报错（在 try 外面，不能当成「没存过」吞掉）
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save(ext_id: str, d: dict, season: bool = False, player: bool = False) -> None:
    p = path(ext_id, season, player)
    raw = json.dumps(d, ensure_ascii=False)
    if len(raw.encode("utf-8")) > MAX_BYTES:
        raise ValueError("扩展数据超过 2MB，请清理旧数据")
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(raw, encoding="utf-8")
    tmp.replace(p)


def clear_season() -> int:
    """「收尾」时调用：删掉当前机器人所有扩展按季度存的数据。返回删了几个扩展的。"""
    n = 0
    with lock:
        d = folder()
        if not d.is_dir():
            return 0
        for p in d.glob("*.season.json"):
            try:
                p.unlink()
                n += 1
            except OSError:
                pass
    return n


BACKUP_SUFFIX = ".上季清理备份.json"
_SPLIT = re.compile(r"[\s_:\-#@./|，、]+")


def _is_role_key(key: str, ids: set[str], names: set[str]) -> bool:
    if key.startswith("_"):
        return False
    return any(t in ids or t in names for t in _SPLIT.split(key) if t)


def clear_role_keys(role_ids, role_names) -> dict[str, int]:
    """收尾时调用（角色删掉之前把编号、名字传进来）：删掉当前机器人各扩展 data_set 里属于这些角色的记录，删之前先备份。
    返回 {扩展id: 删了几条}。"""
    ids = {str(i) for i in role_ids}
    names = {str(n) for n in role_names if n}
    out: dict[str, int] = {}
    if not ids and not names:
        return out
    with lock:
        d = folder()
        if not d.is_dir():
            return out
        for p in sorted(d.glob("*.json")):
            if p.name.endswith((".season.json", ".player.json", BACKUP_SUFFIX)):       # 按玩家存的是长期的，收尾不碰
                continue
            ext_id = p.name[:-len(".json")]
            if not ID_RE.match(ext_id):
                continue
            data = load(ext_id)
            gone = {k: v for k, v in data.items() if _is_role_key(k, ids, names)}
            if not gone:
                continue
            (d / f"{ext_id}{BACKUP_SUFFIX}").write_text(json.dumps(gone, ensure_ascii=False, indent=1), encoding="utf-8")
            save(ext_id, {k: v for k, v in data.items() if k not in gone})
            out[ext_id] = len(gone)
    return out
