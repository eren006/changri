"""跑团 log 的着色与网页渲染（不依赖 QQ / NoneBot）。同一份 HTML 给后台预览、分享链接、以后的 zip 导出用：
单文件、样式和脚本全内联，离线也能打开。"""
from __future__ import annotations

import hashlib
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


_CMD = re.compile(r"^\s*[.。/!！]\s*[A-Za-z\u4e00-\u9fff]")      # 骰子 / 机器人指令：.ra 侦查、。r、/jrrp


def is_command(text: str) -> bool:
    return bool(_CMD.match(text or ""))


def is_ooc(text: str) -> bool:
    """场外（戏外）的话：整句包在一对括号里（「（我去吃个饭）」），或者 // 开头。
    「（笑）你好（转身）」这种头尾都是括号、中间是戏的不算：第一个括号得一直括到最后一个字。"""
    t = (text or "").strip()
    if t.startswith("//"):
        return True
    if len(t) < 2 or t[0] not in "（(" or t[-1] not in "）)":
        return False
    depth = 0
    for i, ch in enumerate(t):
        if ch in "（(":
            depth += 1
        elif ch in "）)":
            depth -= 1
            if depth == 0 and i != len(t) - 1:
                return False
    return depth == 0


def _merge_commands(lines: list[dict]) -> dict[int, dict]:
    """骰点 / 回复 → 触发它的那句指令（下标 → 那行）。按 ref 找；没有 ref 的（老 log、导入的）看紧挨着的上一句。
    只认指令（.ra 这种），普通的话不并。并进去的指令不再单独占一行，在结果卡片上写一个小标签。"""
    by_id = {l["id"]: l for l in lines if l.get("id") is not None}
    out = {}
    for i, l in enumerate(lines):
        if l["kind"] not in ("dice", "bot"):
            continue
        src = by_id.get(l.get("ref")) if l.get("ref") else None
        if src is None and i and lines[i - 1]["kind"] == "say":
            src = lines[i - 1]
        if src is not None and src["kind"] == "say" and is_command(src["text"]):
            out[i] = src
    return out


def speakers(lines: list[dict]) -> list[str]:
    """按第一次发言的顺序，发言人（不含骰子 / 系统）。"""
    out: list[str] = []
    for l in lines:
        n = l["speaker_name"]
        if l["kind"] == "say" and n and n not in out:
            out.append(n)
    return out


def _hash(name: str) -> int:
    return int(hashlib.md5(name.encode("utf-8")).hexdigest(), 16)


def _home(name: str) -> int:
    """名字 → 调色板里固定的一格。同一个角色在每份 log 里都先拿这一格，颜色跟着角色走，不看谁先说话。
    最后两格（靛蓝离蓝色太近、灰色像没上色）不当起点，只在前面 8 格都占满时才用。"""
    return _hash(name) % (len(PALETTE) - 2)


def color_map(lines: list[dict], style: dict) -> dict[str, tuple[str, str]]:
    """发言人 → (浅色底颜色, 深色底颜色)。自己改过的颜色两套都用同一个。
    没改过的按名字取固定的一格；同一份 log 里撞了就往后挪到空着的格子（超过 10 人才会重复）。
    撞格子时谁让位按名字定、不按谁先说话，同样这几个人换个顺序颜色也不变。"""
    order = speakers(lines)
    out, used = {}, set()
    for name in sorted(order, key=_hash):
        c = style["colors"].get(name)
        if c:
            out[name] = (c, c)
            continue
        h = _home(name)
        j = next(((h + k) % len(PALETTE) for k in range(len(PALETTE)) if (h + k) % len(PALETTE) not in used), h)
        used.add(j)
        out[name] = PALETTE[j]
    return {n: out[n] for n in order}      # 照说话先后排：筛选按钮、着色器的顺序不变


CSS = """
:root{--bg:#f7f7f4;--ink:#1d1f23;--mute:#6a6f78;--line:#e1e1da;--card:#fff;--ok:#17653d;--bad:#a52a2a;--crit:#8f6208;--fumble:#7a1c1c}
@media(prefers-color-scheme:dark){:root:not([data-theme]),:root[data-theme=auto]{--bg:#14161a;--ink:#e8e9ec;--mute:#9aa0aa;--line:#2a2e35;--card:#1c1f25;--ok:#6fd3a0;--bad:#ff9b9b;--crit:#f0c45a;--fumble:#ff7a7a}}
:root[data-theme=dark]{--bg:#14161a;--ink:#e8e9ec;--mute:#9aa0aa;--line:#2a2e35;--card:#1c1f25;--ok:#6fd3a0;--bad:#ff9b9b;--crit:#f0c45a;--fumble:#ff7a7a}
:root[data-theme=print]{--bg:#fff;--ink:#000;--mute:#444;--line:#999;--card:#fff;--ok:#000;--bad:#000;--crit:#000;--fumble:#000}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.75 -apple-system,BlinkMacSystemFont,"PingFang SC","Noto Sans SC","Microsoft YaHei",sans-serif;-webkit-font-smoothing:antialiased}
.wrap{max-width:680px;margin:0 auto;padding:28px 20px 72px}
h1{font-size:22px;line-height:1.35;margin:0 0 6px}.meta{color:var(--mute);font-size:13px;margin:0 0 18px}
.tools{position:sticky;top:0;background:var(--bg);padding:10px 0 12px;border-bottom:1px solid var(--line);z-index:2}
.tools input[type=search]{width:100%;padding:9px 14px;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--ink);font:inherit;font-size:15px;outline:none}
.tools input[type=search]:focus{border-color:var(--mute)}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.chip{border:1px solid var(--line);border-color:color-mix(in srgb,var(--c,var(--mute)) 30%,var(--line));border-radius:999px;padding:2px 11px;font-size:13px;background:var(--card);background:color-mix(in srgb,var(--c,var(--mute)) 8%,var(--card));color:var(--ink);cursor:pointer}
.chip[aria-pressed=false]{opacity:.4;text-decoration:line-through;background:var(--card);border-color:var(--line)}
.chip.tog,.chip.tog[aria-pressed=false]{opacity:1;text-decoration:none;background:var(--card);border-color:var(--line)}.chip.tog[aria-pressed=true]{background:var(--ink);border-color:var(--ink);color:var(--bg)}
.chip i{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;vertical-align:1px}
.day{display:flex;align-items:center;gap:12px;color:var(--mute);font-size:12px;letter-spacing:.04em;margin:30px 0 2px;font-variant-numeric:tabular-nums}
.day::before,.day::after{content:"";flex:1;height:1px;background:var(--line)}
.ln{margin:6px 0 0;scroll-margin-top:130px}.ln.hdr{margin-top:24px}.ln:not(.hdr)>.hd{display:none}
.hd{display:flex;align-items:baseline;gap:8px;margin-bottom:4px}.who{font-weight:600;font-size:14px;letter-spacing:.02em}.when{color:var(--mute);font-size:12px;font-variant-numeric:tabular-nums;text-decoration:none}.when:hover{text-decoration:underline}
.txt{white-space:pre-wrap;word-break:break-word}
.ln.say .txt{color:color-mix(in srgb,var(--c,var(--ink)) 55%,var(--ink));padding:1px 0 1px 14px;border-left:2px solid var(--line);border-left-color:color-mix(in srgb,var(--c,var(--line)) 50%,transparent)}
.ln.dice .txt,.ln.bot .txt{margin:4px 0 2px 16px;padding:6px 12px;border-left:2px solid var(--line);border-left-color:color-mix(in srgb,var(--c,var(--mute)) 50%,transparent);background:var(--card);border-radius:0 8px 8px 0;font-size:14px}
.dim .ln.bot{opacity:.62}
.ln.ooc .txt{color:var(--mute);font-size:14px}
.cmd{display:block;font-size:12px;color:var(--mute);line-height:1.5;margin-bottom:2px}.cmd .who{font-size:12px}
mark{background:color-mix(in srgb,var(--crit) 28%,transparent);color:inherit;border-radius:2px;padding:0 1px}
.ln.cur .txt{outline:2px solid color-mix(in srgb,var(--crit) 55%,transparent);outline-offset:3px;border-radius:2px}
@keyframes flash{from{background:color-mix(in srgb,var(--c,var(--crit)) 22%,transparent)}to{background:transparent}}
.ln:target .txt,.ln.flash .txt{animation:flash 2.4s ease-out}
.srow{display:flex;align-items:center;gap:6px}.srow input{flex:1;min-width:0}
.cnt{color:var(--mute);font-size:13px;white-space:nowrap;font-variant-numeric:tabular-nums}
.nav,.sbtn,.totop{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:8px;cursor:pointer;font:inherit;line-height:1}
.nav{width:32px;height:32px;font-size:14px}
.crow{display:flex;align-items:center;gap:6px;margin-top:10px}.crow .chips{margin-top:0;flex:1;min-width:0}
.sbtn{display:none;flex:none;width:30px;height:28px;border-radius:999px;align-items:center;justify-content:center;color:var(--mute)}
.sbtn svg{width:15px;height:15px}
.totop{position:fixed;right:16px;bottom:calc(20px + env(safe-area-inset-bottom));width:40px;height:40px;border-radius:50%;font-size:18px;box-shadow:0 2px 10px rgba(0,0,0,.12);z-index:3}
.toast{position:fixed;left:50%;bottom:calc(28px + env(safe-area-inset-bottom));transform:translateX(-50%);background:var(--ink);color:var(--bg);font-size:13px;padding:6px 14px;border-radius:999px;z-index:4}
@media(max-width:600px){.wrap{padding:20px 16px 72px}.tools:not(.open) .srow{display:none}.tools.open .crow{margin-top:8px}
.sbtn{display:inline-flex}.crow .chips{flex-wrap:nowrap;overflow-x:auto;scrollbar-width:none;-webkit-overflow-scrolling:touch}.crow .chips::-webkit-scrollbar{display:none}.chip{flex:none}
.crow .chips{-webkit-mask-image:linear-gradient(90deg,#000 85%,transparent);mask-image:linear-gradient(90deg,#000 85%,transparent);padding-right:24px}
.tools input[type=search]{font-size:16px}}
:root[data-theme=print] .totop,:root[data-theme=print] .toast{display:none}
@media print{.totop,.toast{display:none}}
.lv{display:inline-block;margin-left:6px;padding:0 8px;border-radius:5px;font-size:13px;font-weight:700;border:1px solid currentColor}
.lv-ok{color:var(--ok)}.lv-bad{color:var(--bad)}.lv-crit{color:var(--crit)}.lv-fumble{color:var(--fumble);text-decoration:underline}
.hl .ln.dice .txt{border-left:3px solid var(--c,var(--mute))}
:root[data-theme=print] .ln{--c:#999}:root[data-theme=print] .ln .txt{color:#000}
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
(function(){
var $=function(i){return document.getElementById(i)},q=$('q'),main=$('log'),tools=$('tools'),cnt=$('cnt'),pv=$('prev'),nx=$('next'),none=$('none'),top=$('totop');
if(!main)return;
var kids=[].slice.call(main.children),off={},only=false,ooc=false,hits=[],cur=-1,timer=0;
function unmark(){[].forEach.call(main.querySelectorAll('mark'),function(m){var p=m.parentNode;p.replaceChild(document.createTextNode(m.textContent),m);p.normalize()})}
function mark(el,t){var w=document.createTreeWalker(el,NodeFilter.SHOW_TEXT),ns=[],x;while((x=w.nextNode()))ns.push(x);
ns.forEach(function(nd){var s=nd.nodeValue,lo=s.toLowerCase(),i=lo.indexOf(t);if(i<0)return;var f=document.createDocumentFragment(),j=0;
while(i>=0){f.appendChild(document.createTextNode(s.slice(j,i)));var m=document.createElement('mark');m.textContent=s.slice(i,i+t.length);f.appendChild(m);j=i+t.length;i=lo.indexOf(t,j)}
f.appendChild(document.createTextNode(s.slice(j)));nd.parentNode.replaceChild(f,nd)})}
function go(d){if(!hits.length)return;if(cur>=0)hits[cur].classList.remove('cur');cur=(cur+d+hits.length)%hits.length;var e=hits[cur];e.classList.add('cur');
e.scrollIntoView({block:'center',behavior:'smooth'});cnt.textContent=(cur+1)+' / '+hits.length}
function apply(){unmark();var t=(q.value||'').trim().toLowerCase(),n=0,prev=null,day=null,dayHas=false;hits=[];cur=-1;
kids.forEach(function(e){var c=e.classList;
if(c.contains('day')){if(day)day.classList.toggle('hidden',!dayHas);day=e;dayHas=false;prev=e;return}
if(!c.contains('ln'))return;c.remove('cur');var tx=e.querySelector('.txt');
var hide=!!off[e.dataset.sp]||(only&&!c.contains('dice'))||(ooc&&c.contains('ooc'))||(t&&tx.textContent.toLowerCase().indexOf(t)<0);
c.toggle('hidden',hide);if(hide)return;dayHas=true;n++;
if(c.contains('say'))c.toggle('hdr',e.dataset.h==='1'||!prev||!prev.classList.contains('say')||prev.dataset.sp!==e.dataset.sp);
prev=e;if(t){mark(tx,t);hits.push(e)}});
if(day)day.classList.toggle('hidden',!dayHas);
none.hidden=n>0||!kids.length;cnt.hidden=pv.hidden=nx.hidden=!t;cnt.textContent=hits.length?'找到 '+hits.length+' 条':'没找到';pv.disabled=nx.disabled=!hits.length}
q.addEventListener('input',function(){clearTimeout(timer);timer=setTimeout(apply,140)});
q.addEventListener('keydown',function(ev){if(ev.key==='Enter'){ev.preventDefault();clearTimeout(timer);if(cur<0&&!hits.length)apply();go(ev.shiftKey?-1:1)}});
pv.addEventListener('click',function(){go(-1)});nx.addEventListener('click',function(){go(1)});
$('sbtn').addEventListener('click',function(){var o=tools.classList.toggle('open');if(o)q.focus();else if(q.value){q.value='';apply()}});
[].forEach.call(document.querySelectorAll('.chip[data-sp]'),function(c){c.addEventListener('click',function(){var k=c.dataset.sp;off[k]=!off[k];c.setAttribute('aria-pressed',off[k]?'false':'true');apply()})});
function tog(id,fn){var b=$(id);b&&b.addEventListener('click',function(){var v=fn();b.setAttribute('aria-pressed',v?'true':'false');apply()})}
tog('only',function(){return only=!only});tog('ooc',function(){return ooc=!ooc});
var shown=false;window.addEventListener('scroll',function(){var s=window.scrollY>700;if(s!==shown){shown=s;top.hidden=!s}},{passive:true});
top.addEventListener('click',function(){window.scrollTo({top:0,behavior:'smooth'})});
function toast(msg){var d=document.createElement('div');d.className='toast';d.textContent=msg;document.body.appendChild(d);setTimeout(function(){d.remove()},1600)}
function old(s){var a=document.createElement('textarea'),ok=false;a.value=s;a.style.position='fixed';a.style.opacity='0';document.body.appendChild(a);a.select();
try{ok=document.execCommand('copy')}catch(e){}a.remove();return ok?Promise.resolve():Promise.reject()}
function copy(s){if(navigator.clipboard&&window.isSecureContext)return navigator.clipboard.writeText(s).catch(function(){return old(s)});return old(s)}
main.addEventListener('click',function(ev){var a=ev.target.closest&&ev.target.closest('a.when');if(!a)return;ev.preventDefault();
var h=a.getAttribute('href'),ln=document.getElementById(h.slice(1));try{history.replaceState(null,'',h)}catch(e){}
if(ln){ln.classList.remove('flash');void ln.offsetWidth;ln.classList.add('flash')}
copy(location.href.split('#')[0]+h).then(function(){toast('已复制这一行的链接')},function(){toast('复制不了，地址栏里就是这一行的链接')})});
})();
"""


_SEARCH_ICON = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true">'
                '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>')


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
    merged = _merge_commands(lines)
    folded = {id(src) for src in merged.values()}        # 并进卡片的指令，不再单独一行
    labeled: set[int] = set()
    body, prev_sp, prev_ts, prev_day, owner, any_ooc = [], None, 0, "", "", False
    for i, l in enumerate(lines):
        if id(l) in folded:
            continue
        day = time.strftime("%Y-%m-%d", time.localtime(l["ts"]))
        if day != prev_day:
            body.append(f'<div class="day">{day}</div>')
            prev_day, prev_sp = day, None
        hhmm = time.strftime("%H:%M", time.localtime(l["ts"]))
        lid = l.get("id") or f"x{i}"
        name = l["speaker_name"] or ("骰子" if l["kind"] != "say" else "")
        sp = name if l["kind"] == "say" else ""
        text = esc(l["text"])
        if l["kind"] == "dice" and st["dice_hl"]:
            v = verdict(l["text"])
            if v:
                a, b = v[2]
                text = f'{esc(l["text"][:a])}<b class="lv lv-{v[1]}">{v[0]}</b>{esc(l["text"][b:])}'     # 先切开再转义，标记里不会混进原文
        src = merged.get(i)
        if src is not None:
            owner = src["speaker_name"] or owner                # 卡片归发指令的人：颜色、筛选都跟着他
            if id(src) not in labeled:
                labeled.add(id(src))
                text = (f'<span class="cmd"><span class="who {cls.get(owner, "")}">{esc(owner)}</span> {esc(src["text"].strip())}</span>' + text)
        cls_extra = ""
        if l["kind"] == "say" and is_ooc(l["text"]):
            cls_extra, any_ooc = " ooc", True
        if l["kind"] == "say":
            forced = l["ts"] - prev_ts > 300
            show_hdr = sp != prev_sp or forced
            head = (f'<div class="hd"><span class="who {cls.get(sp, "")}">{esc(name)}</span>'
                    f'<a class="when" href="#L{lid}" title="复制这一行的链接">{hhmm}</a></div>')
        else:
            forced, show_hdr, head = False, False, ""
        data = esc(sp if l["kind"] == "say" else owner, quote=True)       # 骰点跟着发指令（或它前面那位）的人，筛掉他时一起收起
        tint = cls.get(sp if l["kind"] == "say" else owner, "")
        body.append(f'<div class="ln {l["kind"]}{cls_extra} {tint}{" hdr" if show_hdr else ""}" id="L{lid}" data-sp="{data}"'
                    f'{" data-h=1" if forced else ""}>{head}<div class="txt">{text}</div></div>')
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
            f'<div class="tools" id="tools"><div class="srow"><input id="q" type="search" placeholder="搜索内容" enterkeyhint="search">'
            f'<span class="cnt" id="cnt" hidden></span><button class="nav" id="prev" type="button" aria-label="上一条" hidden>↑</button>'
            f'<button class="nav" id="next" type="button" aria-label="下一条" hidden>↓</button></div>'
            f'<div class="crow"><button class="sbtn" id="sbtn" type="button" aria-label="搜索">{_SEARCH_ICON}</button><div class="chips">{chips}'
            f'<button class="chip tog" id="only" type="button" aria-pressed="false">只看骰点</button>'
            + ('<button class="chip tog" id="ooc" type="button" aria-pressed="false">隐藏场外</button>' if any_ooc else "")
            + '</div></div></div>'
            f'<main id="log">{"".join(body) or "<p class=empty>这个 log 还没有内容。</p>"}</main>'
            f'<p class="empty" id="none" hidden>没有符合的内容。</p></div>'
            f'<button class="totop" id="totop" type="button" aria-label="回到顶部" hidden>↑</button><script>{JS}</script></body></html>')
    return page
