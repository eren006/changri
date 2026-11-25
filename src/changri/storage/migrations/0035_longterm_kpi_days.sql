-- 长期群 KPI 自定义周期：kpi_period = 'days' 时，从 kpi_anchor 那天 0 点起，每 kpi_days 天一期，到期自动进下一期。
ALTER TABLE lt_groups ADD COLUMN kpi_days INTEGER NOT NULL DEFAULT 7;      -- 每几天一期（1–366）
ALTER TABLE lt_groups ADD COLUMN kpi_anchor INTEGER NOT NULL DEFAULT 0;    -- 第一期从哪天 0 点开始（本地时间戳）；0 = 还没设，按保存那天算
