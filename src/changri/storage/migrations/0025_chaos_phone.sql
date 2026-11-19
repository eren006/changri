-- 0025：混乱短信和小手机对齐。数据库里存的是「出了岔子之后」的样子（收件人看到的），发件人的小手机要显示他原本写的、原本想发给谁。
-- orig_to：误投 / 礼物送错人时，发件人原本想发给谁（0 = 没出岔子，就是 to_role_id）。
-- orig_content：短信被抹字、撕成两半时发件人写的原文（NULL = 没改过）。
ALTER TABLE messages ADD COLUMN orig_to INTEGER NOT NULL DEFAULT 0;
ALTER TABLE messages ADD COLUMN orig_content TEXT;
-- 撕走的后半截飘到了谁那里、写的什么、落款，第三个人的小手机里「飘来的半张纸」会话读这里。
ALTER TABLE chaos_letters ADD COLUMN torn_to INTEGER NOT NULL DEFAULT 0;
ALTER TABLE chaos_letters ADD COLUMN torn_text TEXT NOT NULL DEFAULT '';
ALTER TABLE chaos_letters ADD COLUMN torn_sign TEXT NOT NULL DEFAULT '';
