-- 0027：匿名短信也会出混乱效果。误投给第三个人的匿名短信不再挂在匿名对话上（第三个人不在这段对话里，按落款进他的署名会话），
-- orig_alias 记下它原本属于哪段匿名对话：发件人那边照旧在匿名对话里看到这条（0 = 不是误投的匿名短信）。
ALTER TABLE messages ADD COLUMN orig_alias INTEGER NOT NULL DEFAULT 0;
