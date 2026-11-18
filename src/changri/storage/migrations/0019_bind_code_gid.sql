-- 0019：后台「换绑群号」登记的新群号先记在认群码上，认群时才写到个人群上。
-- 以前一登记就改了个人群的群号标签，旧群还绑着，页面上新群号立刻显示「已认群」，其实还没换过去。
ALTER TABLE bind_codes ADD COLUMN real_gid TEXT NOT NULL DEFAULT '';
