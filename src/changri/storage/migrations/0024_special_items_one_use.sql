-- 特殊道具（SPEC_001–009）每件用一次，定死不能改（作者定的）。以前建进物品库时没填次数，列表里显示成「不限次数」；
-- 实际上用一次就从背包扣掉一件，这里把显示和数据对齐。背包里已有的道具也按「用 1 次」记。
UPDATE rpg_items SET max_uses = 1 WHERE code IN ('SPEC_001','SPEC_002','SPEC_003','SPEC_004','SPEC_005','SPEC_006','SPEC_007','SPEC_008','SPEC_009');
UPDATE rpg_inventory SET remaining_uses = 1 WHERE item_code IN ('SPEC_001','SPEC_002','SPEC_003','SPEC_004','SPEC_005','SPEC_006','SPEC_007','SPEC_008','SPEC_009');
