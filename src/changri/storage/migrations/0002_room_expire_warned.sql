-- 0002：房间「结戏前提醒」发过没有（到时间强制结群开着时，提前 N 小时在房间群里 @ 参与者，只发一次）
ALTER TABLE rooms ADD COLUMN expire_warned INTEGER NOT NULL DEFAULT 0;
