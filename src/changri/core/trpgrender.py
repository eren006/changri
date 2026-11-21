"""跑团 log 的着色与网页渲染（不依赖 QQ / NoneBot）。同一份 HTML 给后台预览、分享链接、以后的 zip 导出用：
单文件、样式和脚本全内联，离线也能打开。"""
from __future__ import annotations

import html
import json
import re
import time

THEMES = {"auto": "跟随系统", "light": "浅色纸张", "dark": "夜间", "print": "打印（黑白）"}
# 每位发言人的默认色：(浅色底, 深色底)。色相拉开，深浅两套都保证能读
PALETTE = [
    ("#1f5fbf", "#7fb0ff"), ("#b5401f", "#ff9b78"), ("#1d7a4d", "#6fd3a0"), ("#8a3fbf", "#d2a4ff"), ("#8f6208", "#f0c45a"),
    ("#0f7c8a", "#62d3e0"), ("#b3265c", "#ff8fb8"), ("#566c1e", "#b6d36a"), ("#5b4bc4", "#a9a0ff"), ("#5f6670", "#aab0bb"),
]
DEFAULT_STYLE = {"theme": "auto", "colors": {}, "dice_hl": True, "dim_bot": True}
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
# 检定结论：先看「—— 结论」，没有再在整行里找。长的词排前面，免得「大成功」被「成功」抢先匹配
_WORDS = ["大成功", "极难成功", "困难成功", "大失败", "极难失败", "困难失败", "未通过", "通过", "成功", "失败"]
_VERDICT = re.compile(r"——\s*(%s)" % "|".join(_WORDS))
_ANY = re.compile("(%s)" % "|".join(_WORDS))
_LV_CLASS = {"大成功": "crit", "极难成功": "ok", "困难成功": "ok", "成功": "ok", "通过": "ok",
             "失败": "bad", "极难失败": "bad", "困难失败": "bad", "未通过": "bad", "大失败": "fumble"}


def verdict(text: str) -> tuple[str, str, tuple[int, int]] | None:
    """一条骰点回复的结论：(词, 类别 crit/ok/bad/fumble, 词在原文里的位置)。看不出就 None。"""
    m = _VERDICT.search(text)
    if m:
        return m.group(1), _LV_CLASS[m.group(1)], m.span(1)
    m = _ANY.search(text)
    return (m.group(1), _LV_CLASS[m.group(1)], m.span(1)) if m else None


def parse_style(raw: str) -> dict:
    try:
        d = json.loads(raw or "{}")
    except ValueError:
        d = {}
    st = {**DEFAULT_STYLE, "colors": {}}
    if d.get("theme") in THEMES:
        st["theme"] = d["theme"]
    if isinstance(d.get("colors"), dict):
        st["colors"] = {str(k)[:20]: v for k, v in d["colors"].items() if isinstance(v, str) and _HEX.match(v)}
    for k in ("dice_hl", "dim_bot"):
        if k in d:
            st[k] = bool(d[k])
    return st


def speakers(lines: list[dict]) -> list[str]:
    """按第一次发言的顺序，发言人（不含骰子 / 系统）。"""
    out: list[str] = []
    for l in lines:
        n = l["speaker_name"]
        if l["kind"] == "say" and n and n not in out:
            out.append(n)
    return out


def color_map(lines: list[dict], style: dict) -> dict[str, tuple[str, str]]:
    """发言人 → (浅色底颜色, 深色底颜色)。自己改过的颜色两套都用同一个。"""
    out = {}
    for i, name in enumerate(speakers(lines)):
        c = style["colors"].get(name)
        out[name] = (c, c) if c else PALETTE[i % len(PALETTE)]
    return out


CSS = """
:root{--bg:#f7f7f4;--ink:#1d1f23;--mute:#6a6f78;--line:#e1e1da;--card:#fff;--ok:#17653d;--bad:#a52a2a;--crit:#8f6208;--fumble:#7a1c1c}
@media(prefers-color-scheme:dark){:root:not([data-theme]),:root[data-theme=auto]{--bg:#14161a;--ink:#e8e9ec;--mute:#9aa0aa;--line:#2a2e35;--card:#1c1f25;--ok:#6fd3a0;--bad:#ff9b9b;--crit:#f0c45a;--fumble:#ff7a7a}}
:root[data-theme=dark]{--bg:#14161a;--ink:#e8e9ec;--mute:#9aa0aa;--line:#2a2e35;--card:#1c1f25;--ok:#6fd3a0;--bad:#ff9b9b;--crit:#f0c45a;--fumble:#ff7a7a}
:root[data-theme=print]{--bg:#fff;--ink:#000;--mute:#444;--line:#999;--card:#fff;--ok:#000;--bad:#000;--crit:#000;--fumble:#000}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.7 -apple-system,BlinkMacSystemFont,"PingFang SC","Noto Sans SC","Microsoft YaHei",sans-serif}
.wrap{max-width:720px;margin:0 auto;padding:20px 16px 64px}
h1{font-size:24px;line-height:1.3;margin:8px 0 4px}.meta{color:var(--mute);font-size:14px;margin:0 0 14px}
.tools{position:sticky;top:0;background:var(--bg);padding:10px 0;border-bottom:1px solid var(--line);z-index:2}
.tools input[type=search]{width:100%;padding:9px 12px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink);font:inherit;font-size:15px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}.chip{border:1px solid var(--line);border-radius:999px;padding:3px 11px;font-size:13px;background:var(--card);color:var(--ink);cursor:pointer}
.chip[aria-pressed=false]{opacity:.45;text-decoration:line-through}.chip.tog[aria-pressed=false]{opacity:1;text-decoration:none}.chip.tog[aria-pressed=true]{background:var(--ink);color:var(--bg)}.chip i{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px}
.day{text-align:center;color:var(--mute);font-size:13px;margin:22px 0 6px}
.ln{margin:0}.ln.hdr{margin-top:14px}.who{font-weight:700;font-size:15px}.when{color:var(--mute);font-size:12px;margin-left:8px;font-weight:400}
.txt{white-space:pre-wrap;word-break:break-word}
.ln.dice .txt,.ln.bot .txt{margin:2px 0 2px 14px;padding:6px 12px;border-left:3px solid var(--line);background:var(--card);border-radius:0 8px 8px 0;font-size:15px}
.dim .ln.bot{opacity:.62}
.lv{display:inline-block;margin-left:6px;padding:0 8px;border-radius:5px;font-size:13px;font-weight:700;border:1px solid currentColor}
.lv-ok{color:var(--ok)}.lv-bad{color:var(--bad)}.lv-crit{color:var(--crit)}.lv-fumble{color:var(--fumble);text-decoration:underline}
.hl .ln.dice .txt{border-left-width:4px}
:root[data-theme=print] .chip,:root[data-theme=print] .tools{display:none}
.meta a{color:inherit}.hidden{display:none!important}.empty{color:var(--mute);padding:30px 0;text-align:center}
.report{margin:0 0 14px;border:1px solid var(--line);border-radius:10px;background:var(--card);padding:4px 14px}
.report summary{cursor:pointer;font-weight:700;padding:8px 0}.report .tw{overflow-x:auto}
.report table{width:100%;border-collapse:collapse;font-size:14px;font-variant-numeric:tabular-nums}
.report th,.report td{padding:5px 6px;border-top:1px solid var(--line);text-align:right;white-space:nowrap}
.report th:first-child,.report td:first-child{text-align:left;white-space:normal}.report th{color:var(--mute);font-weight:400;font-size:13px}
.report ul{list-style:none;margin:10px 0 8px;padding:0;font-size:14px;line-height:1.8}
@media(max-width:420px){.report th,.report td{padding:5px 3px;font-size:13px}}
@media print{.tools{display:none}}
"""

JS = """
(function(){var q=document.getElementById('q'),off={},only=false;
function apply(){var t=(q.value||'').trim().toLowerCase();document.querySelectorAll('.ln').forEach(function(e){
var hide=false;if(off[e.dataset.sp])hide=true;if(only&&!e.classList.contains('dice'))hide=true;
if(t&&e.textContent.toLowerCase().indexOf(t)<0)hide=true;e.classList.toggle('hidden',hide)})}
q&&q.addEventListener('input',apply);
document.querySelectorAll('.chip[data-sp]').forEach(function(c){c.addEventListener('click',function(){var k=c.dataset.sp;off[k]=!off[k];c.setAttribute('aria-pressed',off[k]?'false':'true');apply()})});
var d=document.getElementById('only');d&&d.addEventListener('click',function(){only=!only;d.setAttribute('aria-pressed',only?'true':'false');apply()})})();
"""


def _report(stats: dict | None, highlights: list[str] | None, cls: dict[str, str]) -> str:
    """按人战报（trpglog.stats 的 people + highlights）。没有对得上人的骰点就不出这一块。名字用发言人自己的颜色。"""
    people = (stats or {}).get("people") or []
    if not any(p["name"] != "（不明）" for p in people):        # 一个骰点都对不上人（比如导入的聊天记录）：表里只有「不明」，不出
        return ""
    esc = html.escape
    rows = "".join(
        f'<tr><td><span class="who {cls.get(p["name"], "")}">{esc(p["name"])}</span></td><td>{p["dice"]}</td><td>{p["crit"]}</td>'
        f'<td>{p["ok"]}</td><td>{p["bad"]}</td><td>{p["fumble"]}</td><td>{"—" if p.get("rate") is None else str(p["rate"]) + "%"}</td></tr>'
        for p in people)
    hl = "".join(f"<li>{esc(h)}</li>" for h in highlights or [])
    return ('<details class="report" open><summary>本场战报</summary><div class="tw"><table><thead><tr><th>发言人</th><th>骰点</th>'
            '<th>大成功</th><th>成功</th><th>失败</th><th>大失败</th><th>成功率</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>' + (f"<ul>{hl}</ul>" if hl else "") + "</details>")


def render_html(log: dict, lines: list[dict], style: dict | None = None, download_url: str = "",
                stats: dict | None = None, highlights: list[str] | None = None) -> str:
    """stats / highlights 是 trpglog.stats() 和 trpglog.highlights() 的结果：给了就在最上面加「本场战报」（这里不读数据库）。"""
    st = style or parse_style(log.get("palette", ""))
    cmap = color_map(lines, st)
    esc = html.escape
    # 每位发言人的颜色：浅色底 / 深色底各一份 CSS 变量，类名 s0 s1 …
    cls = {n: f"s{i}" for i, n in enumerate(cmap)}
    colors_css = ""
    for n, (lc, dc) in cmap.items():
        k = cls[n]
        colors_css += f".{k}{{--c:{lc}}}"
        if lc != dc:
            colors_css += f"@media(prefers-color-scheme:dark){{:root:not([data-theme]) .{k},:root[data-theme=auto] .{k}{{--c:{dc}}}}}:root[data-theme=dark] .{k}{{--c:{dc}}}"
    colors_css += ".who{color:var(--c,var(--ink))}.chip i{background:var(--c,var(--mute))}:root[data-theme=print] .who{color:#000}"
    body, prev_sp, prev_ts, prev_day, owner = [], None, 0, "", ""
    for l in lines:
        day = time.strftime("%Y-%m-%d", time.localtime(l["ts"]))
        if day != prev_day:
            body.append(f'<div class="day">{day}</div>')
            prev_day, prev_sp = day, None
        hhmm = time.strftime("%H:%M", time.localtime(l["ts"]))
        name = l["speaker_name"] or ("骰子" if l["kind"] != "say" else "")
        sp = name if l["kind"] == "say" else ""
        text = esc(l["text"])
        if l["kind"] == "dice" and st["dice_hl"]:
            v = verdict(l["text"])
            if v:
                a, b = v[2]
                text = f'{esc(l["text"][:a])}<b class="lv lv-{v[1]}">{v[0]}</b>{esc(l["text"][b:])}'     # 先切开再转义，标记里不会混进原文
        show_hdr = l["kind"] == "say" and (sp != prev_sp or l["ts"] - prev_ts > 300)
        head = f'<div><span class="who {cls.get(sp, "")}">{esc(name)}</span><span class="when">{hhmm}</span></div>' if show_hdr else ""
        data = esc(sp if l["kind"] == "say" else owner, quote=True)       # 骰点跟着它前面那位发言人，筛掉他时一起收起
        body.append(f'<div class="ln {l["kind"]}{" hdr" if show_hdr else ""}" data-sp="{data}">{head}<div class="txt">{text}</div></div>')
        if l["kind"] == "say":
            prev_sp, prev_ts, owner = sp, l["ts"], sp
        else:
            prev_sp, prev_ts = None, l["ts"]
    chips = "".join(f'<button class="chip {cls[n]}" type="button" data-sp="{esc(n, quote=True)}" aria-pressed="true"><i></i>{esc(n)}</button>' for n in cmap)
    first = time.strftime("%Y-%m-%d %H:%M", time.localtime(lines[0]["ts"])) if lines else ""
    status = {"on": "进行中", "off": "暂停", "ended": "已结束", "halted": "已停止"}.get(log.get("status", ""), "")
    dl = f'　<a href="{esc(download_url, quote=True)}">下载 zip</a>' if download_url else ""
    classes = ("hl " if st["dice_hl"] else "") + ("dim" if st["dim_bot"] else "")
    page = (f'<!doctype html><html lang="zh-CN" data-theme="{st["theme"]}"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">'
            f'<title>{esc(log["name"])}</title><style>{CSS}{colors_css}</style></head><body><div class="wrap {classes}">'
            f'<h1>{esc(log["name"])}</h1><p class="meta">{status}　{len(lines)} 条　{first}{dl}</p>{_report(stats, highlights, cls)}'
            f'<div class="tools"><input id="q" type="search" placeholder="搜索内容"><div class="chips">{chips}'
            f'<button class="chip tog" id="only" type="button" aria-pressed="false">只看骰点</button></div></div>'
            f'<main>{"".join(body) or "<p class=empty>这个 log 还没有内容。</p>"}</main></div><script>{JS}</script></body></html>')
    return page
