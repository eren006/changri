-- 0026：短信链路修补。
-- delivered_img_ids：跟在短信后面发到对方群里的图片消息 id（逗号分隔），撤回时一起撤。
ALTER TABLE messages ADD COLUMN delivered_img_ids TEXT NOT NULL DEFAULT '';
-- 混乱短信送到之后才做的事（撕走的后半截送给第三人、公告群播报）：当场没送到、后来自动重试送到的也要补做。
-- msg_id：对应哪条短信；after：还没做的后续（JSON，做完清空）。
ALTER TABLE chaos_letters ADD COLUMN msg_id INTEGER NOT NULL DEFAULT 0;
ALTER TABLE chaos_letters ADD COLUMN after TEXT NOT NULL DEFAULT '';
