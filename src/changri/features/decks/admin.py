"""设置平台 · 牌堆（侧栏「跑团」组）：写 / 贴 / 上传牌堆文件、下载模板、看全部、改、删、试抽。跟小手机没有关系。
由 changri.admin.create_app 调用 register(app, login_required)。"""
from flask import Response, flash, redirect, render_template, request, url_for

from changri.core import decks


def register(app, login_required):
    def _page(edit_id=0, draft=None, draft_title=""):
        edit = decks.get_file(edit_id)
        name = request.args.get("try", "").strip()
        return render_template("decks.html", files=decks.files(), edit=edit, tpl_text=decks.TEMPLATE, ai_prompt=decks.AI_PROMPT,
                               try_name=name, try_text=decks.draw(name) if name else "", draft=draft, draft_title=draft_title)

    @app.route("/decks")
    @login_required
    def decks_page():
        return _page(request.args.get("edit", type=int) or 0)

    @app.route("/decks/template.txt")
    @login_required
    def decks_template():
        return Response(decks.TEMPLATE.encode("utf-8"), mimetype="text/plain; charset=utf-8",
                        headers={"Content-Disposition": "attachment; filename*=UTF-8''%E9%95%BF%E6%97%A5%E7%89%8C%E5%A0%86%E6%A8%A1%E6%9D%BF.txt"})

    @app.post("/decks/save")
    @login_required
    def decks_save():
        fid = request.form.get("file_id", type=int) or 0
        raw = request.form.get("raw", "")
        up = request.files.get("file")
        if up and up.filename:
            try:
                raw = up.read(decks.MAX_RAW * 4 + 1).decode("utf-8-sig")
            except UnicodeDecodeError:
                flash("文件要存成 UTF-8 编码（记事本「另存为」里能选）。")
                return redirect(url_for("decks_page", edit=fid) if fid else url_for("decks_page"))
        if not raw.strip():
            flash("还没写内容，也没选文件。")
            return redirect(url_for("decks_page", edit=fid) if fid else url_for("decks_page"))
        title = request.form.get("title", "").strip()
        ok, msg, _ = decks.save(None, raw, file_id=fid, title=title)
        flash(msg)
        if ok:
            return redirect(url_for("decks_page"))
        return _page(fid, draft=raw, draft_title=title)        # 没存上：把刚贴的内容留在输入框里，改一改再存，不用重新贴

    @app.post("/decks/<int:fid>/delete")
    @login_required
    def decks_delete(fid):
        flash("删掉了（这份里的牌堆都没了）" if decks.delete(fid) else "已经没有这份了")
        return redirect(url_for("decks_page"))
