-- 0.22.6：「曝光时隐藏收信人」拆成两个开关（作者定的）：礼物公开播报 gift_hide_receiver、心动信曝光 lovemail_hide_receiver。
-- 以前两个共用 drop_hide_receiver。升级时把老开关的值抄给两个新开关，行为不变；后台设置页显示的也是真实的值。
INSERT OR IGNORE INTO config(key, value) SELECT 'gift_hide_receiver', value FROM config WHERE key='drop_hide_receiver';
INSERT OR IGNORE INTO config(key, value) SELECT 'lovemail_hide_receiver', value FROM config WHERE key='drop_hide_receiver';
