-- V11：会计期间 + 所有者权益科目
--
-- 背景（实测缺口，见 2026-11-16 ERP 评估）：
--   ① accounting_entry 只有 biz_date，没有「会计期间」——无法按月取数（三大报表的前置），
--      也无法封账：历史期间的凭证可以随时被改、被弃审重生成。
--   ② accounting_account 只有 资产/负债/收入/费用 四类，没有权益类科目，
--      「资产 = 负债 + 所有者权益」永远配不平，资产负债表就算做出来也立不住。
--
-- 本脚本只补地基，不做报表也不做结转损益（那两件事依赖这里的期间与科目）。

-- ---------- 1. 凭证加期间 ----------
ALTER TABLE accounting_entry ADD COLUMN IF NOT EXISTS period varchar(7);

-- 按业务日期回填历史凭证；没有业务日期的留空，由应用侧按创建时间兜底
UPDATE accounting_entry
   SET period = to_char(biz_date, 'YYYY-MM')
 WHERE period IS NULL AND biz_date IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_accounting_entry_period ON accounting_entry (period);

COMMENT ON COLUMN accounting_entry.period IS '会计期间 yyyy-MM，由业务日期派生；所属期间封账后禁止写入';

-- ---------- 2. 期间状态表 ----------
CREATE TABLE IF NOT EXISTS accounting_period (
  period       varchar(7)  NOT NULL,
  status       varchar(10) NOT NULL DEFAULT 'open',
  closed_at    timestamp(0),
  closed_by    varchar(50),
  factory_code varchar(20) NOT NULL DEFAULT 'F01',
  create_time  timestamp(0) DEFAULT now(),
  CONSTRAINT pk_accounting_period PRIMARY KEY (period, factory_code)
);

CREATE INDEX IF NOT EXISTS ix_accounting_period_factory ON accounting_period (factory_code);

COMMENT ON TABLE  accounting_period            IS '会计期间：记录每个月的开账/封账状态';
COMMENT ON COLUMN accounting_period.period     IS '会计期间 yyyy-MM';
COMMENT ON COLUMN accounting_period.status     IS 'open 可记账 / closed 已封账，封账后凭证不可增删改';
COMMENT ON COLUMN accounting_period.closed_at  IS '封账时间';
COMMENT ON COLUMN accounting_period.closed_by  IS '封账操作人';

-- 已有凭证的期间先登记为开账状态
INSERT INTO accounting_period (period, status, factory_code)
SELECT DISTINCT period, 'open', 'F01'
  FROM accounting_entry
 WHERE period IS NOT NULL
    ON CONFLICT (period, factory_code) DO NOTHING;

-- ---------- 3. 所有者权益科目（企业会计准则 4xxx） ----------
INSERT INTO accounting_account (account_code, account_name, category, direction, enabled, factory_code)
VALUES
  ('4001', '实收资本',   '权益', '贷', 1, 'F01'),
  ('4103', '本年利润',   '权益', '贷', 1, 'F01'),
  ('4104', '利润分配',   '权益', '贷', 1, 'F01')
    ON CONFLICT (account_code) DO NOTHING;

COMMENT ON TABLE accounting_account IS '会计科目表（资产 1xxx / 负债 2xxx / 权益 4xxx / 成本 5xxx / 损益 6xxx）';
