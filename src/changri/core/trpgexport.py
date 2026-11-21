"""跑团 log 的导出（zip / html / txt / json）和骰点汇总（不依赖 QQ / NoneBot）。"""
from __future__ import annotations

import io
import json
import re
import time
import zipfile

from . import trpglog
from .trpgrender import parse_style, render_html, verdict

_LABEL = re.compile(r"^(.*?)\s*检定：")


def _t(ts: int | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else ""


def roll_label(text: str) -> str:
    """从一条骰点回复里认出技能名；认不出就是「（未标）」。"""
    if text.startswith("理智检定"):
        return "理智"
    m = _LABEL.match(text)
    return (m.group(1).strip() if m else "") or "（未标）"


def summary(log: dict, lines: list[dict]) -> dict:
    """每位玩家的检定次数 / 成功失败 / 大成功大失败，以及每个技能的成功率。骰点靠 ref 归到触发它的发言人。"""
    by_id = {l["id"]: l for l in lines}
    people: dict[str, dict] = {}
    skills: dict[str, dict] = {}
    for l in lines:
        if l["kind"] != "dice":
            continue
        v = verdict(l["text"])
        if not v:
            continue
        src = by_id.get(l["ref"]) if l["ref"] else None
        who = src["speaker_name"] if src else "（未知）"
        for table, key in ((people, who), (skills, roll_label(l["text"]))):
            d = table.setdefault(key, {"n": 0, "ok": 0, "bad": 0, "crit": 0, "fumble": 0})
            d["n"] += 1
            if v[1] in ("crit", "ok"):
                d["ok"] += 1
            else:
                d["bad"] += 1
            if v[1] == "crit":
                d["crit"] += 1
            if v[1] == "fumble":
                d["fumble"] += 1
    return {"people": people, "skills": skills}


def summary_text(log: dict, lines: list[dict]) -> str:
    sm = summary(log, lines)
    rate = lambda d: f"{d['ok'] * 100 // d['n']}%" if d["n"] else "—"
    out = [f"【{log['name']}】骰点汇总", f"共 {sum(d['n'] for d in sm['people'].values())} 次有结论的检定", ""]
    out.append("按玩家：")
    for n, d in sorted(sm["people"].items(), key=lambda x: -x[1]["n"]):
        out.append(f"  {n}　{d['n']} 次，成功 {d['ok']}、失败 {d['bad']}（成功率 {rate(d)}），大成功 {d['crit']}、大失败 {d['fumble']}")
    out += ["", "按技能："]
    for n, d in sorted(sm["skills"].items(), key=lambda x: -x[1]["n"]):
        out.append(f"  {n}　{d['n']} 次，成功率 {rate(d)}，大成功 {d['crit']}、大失败 {d['fumble']}")
    return "\n".join(out) + "\n"


def txt(lines: list[dict]) -> str:
    return "\n".join(f"{l['speaker_name'] or '骰子'}({_t(l['ts'])})\n{l['text']}\n" for l in lines)


def as_json(log: dict, lines: list[dict]) -> str:
    return json.dumps({
        "name": log["name"], "status": log["status"], "started": _t(log["started_ts"]), "ended": _t(log["ended_ts"]),
        "lines": [{"id": l["id"], "time": _t(l["ts"]), "speaker": l["speaker_name"], "kind": l["kind"], "text": l["text"], "ref": l["ref"]} for l in lines],
        "summary": summary(log, lines),
    }, ensure_ascii=False, indent=1)


def html(log: dict, lines: list[dict]) -> str:
    from . import trpglog
    st = trpglog.stats(log["id"])
    return render_html(log, lines, parse_style(log.get("palette", "")), stats=st, highlights=trpglog.highlights(st))


def build(log: dict, fmt: str) -> tuple[bytes, str, str]:
    """返回 (内容, 文件名, MIME)。fmt：zip / html / txt / json / summary。"""
    ls = trpglog.lines(log["id"])
    name = re.sub(r'[\\/:*?"<>|\s]+', "_", log["name"]) or "log"
    if fmt == "html":
        return html(log, ls).encode(), f"{name}.html", "text/html; charset=utf-8"
    if fmt == "txt":
        return txt(ls).encode(), f"{name}.txt", "text/plain; charset=utf-8"
    if fmt == "json":
        return as_json(log, ls).encode(), f"{name}.json", "application/json"
    if fmt == "summary":
        return summary_text(log, ls).encode(), f"{name}_骰点汇总.txt", "text/plain; charset=utf-8"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("log.html", html(log, ls))
        z.writestr("log.txt", txt(ls))
        z.writestr("log.json", as_json(log, ls))
        z.writestr("骰点汇总.txt", summary_text(log, ls))
    return buf.getvalue(), f"{name}.zip", "application/zip"
