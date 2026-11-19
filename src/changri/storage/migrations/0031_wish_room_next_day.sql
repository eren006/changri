-- 0.22.7：跨午夜的心愿被摘后，房间的结束时间误存成了显示用的「次日01:00」，撞时检查、禁约时段、超时提醒都认不出来。
-- 去掉「次日」两个字，改回「01:00」（跨午夜是按结束早于开始来认的，不需要标记）。
UPDATE rooms SET end_hm = substr(end_hm, 3) WHERE end_hm LIKE '次日%';
UPDATE rooms SET start_hm = substr(start_hm, 3) WHERE start_hm LIKE '次日%';
