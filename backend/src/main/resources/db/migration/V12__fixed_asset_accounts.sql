-- V12：固定资产相关科目
--
-- 背景：2026-11-16 ERP 评估确认固定资产完全缺失（无卡片、无折旧）。
-- 卡片本身走通用面板（form_data），不需要新表；这里只补三个会计科目，
-- 否则折旧凭证没有落点：固定资产 1601 / 累计折旧 1602 / 折旧费用 6602。

INSERT INTO accounting_account (account_code, account_name, category, direction, enabled, factory_code)
VALUES
  ('1601', '固定资产',   '资产', '借', 1, 'F01'),
  ('1602', '累计折旧',   '资产', '贷', 1, 'F01'),
  ('6602', '折旧费用',   '费用', '借', 1, 'F01')
    ON CONFLICT (account_code) DO NOTHING;
