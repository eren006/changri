"""设置平台 · 个性化骰子：检定各个成功等级的表情，以及检定、理智、成长、掷骰高低、DND 自然 20 等时机器人说的话。跑团群和恋综群通用。由 changri.admin.create_app 调用 register(app, login_required)。"""
from flask import flash, redirect, render_template, request, url_for
from changri.core import dice, dicecard

LINE_MAX = 80        # 一句最多多少字
LINES_MAX = 20       # 一档最多几句


def register(app, login_required):
    @app.route("/dice/lines")
    @login_required
    def dice_lines_page():
        custom = dice.custom_lines()
        groups = []
        for title, pool, keys in dice.LINE_GROUPS:
            rows = []
            for key, label in keys:
                k = f"{pool}.{key}"
                rows.append({"k": k, "label": label, "lines": custom.get(k, dice.POOLS[pool][key]), "custom": k in custom,
                             "default": dice.POOLS[pool][key]})
            groups.append((title, rows))
        marks = dicecard.custom_marks()
        mark_rows = [{"level": lv, "mark": marks.get(lv, m), "default": m, "custom": lv in marks} for lv, m in dicecard.MARKS.items()]
        return render_template("dice_lines.html", line_groups=groups, mark_rows=mark_rows, any_custom=bool(custom or marks),
                               line_max=LINE_MAX, lines_max=LINES_MAX, mark_max=dicecard.MARK_MAX)

    @app.post("/dice/lines")
    @login_required
    def dice_lines_save():
        data, bad = {}, []
        for _title, pool, keys in dice.LINE_GROUPS:
            for key, label in keys:
                k = f"{pool}.{key}"
                lines = [x.strip() for x in request.form.get(k, "").splitlines() if x.strip()]
                if any(len(x) > LINE_MAX for x in lines) or len(lines) > LINES_MAX:
                    bad.append(label)
                    lines = [x[:LINE_MAX] for x in lines][:LINES_MAX]
                data[k] = lines
        dice.save_custom_lines(data)
        dicecard.save_custom_marks({lv: request.form.get(f"mark.{lv}", "") for lv in dicecard.MARKS})
        flash("已保存" + (f"；「{'、'.join(bad)}」超长的部分已截掉（每句 {LINE_MAX} 字、每档 {LINES_MAX} 句以内）" if bad else ""))
        return redirect(url_for("dice_lines_page"))

    @app.post("/dice/lines/reset")
    @login_required
    def dice_lines_reset():
        k = request.form.get("k", "")
        custom = dice.custom_lines()
        if k == "*":
            custom = {}
            dicecard.save_custom_marks({})
            flash("全部恢复默认")
        elif k == "marks":
            dicecard.save_custom_marks({})
            flash("表情已恢复默认")
        elif custom.pop(k, None) is not None:
            flash("这一档已恢复默认")
        dice.save_custom_lines(custom)
        return redirect(url_for("dice_lines_page") + (f"#{k}" if k != "*" else ""))
