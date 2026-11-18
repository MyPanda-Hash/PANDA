-- 多账套隔离：业务数据按 factory_code 归属到具体账套。
-- 背景：factory 表早就有 F01/F02 两个账套，但业务表没有归属字段，
-- 前端切换只写 localStorage，后端完全不感知，所以「切换账套数据不变」。
-- 本迁移做三件事：加字段、加索引、把存量数据归到默认账套。

-- ---------------------------------------------------------------- 1. 业务表加归属字段
-- 只处理业务数据表；sys_menu/sys_role/sys_locale/sys_translation 是系统配置，全局共享，不隔离。
DO $$
DECLARE
  t text;
  tables text[] := ARRAY[
    'form_data', 'form_approval',
    'stock_balance', 'stock_txn',
    'manu_order', 'manu_order_item',
    'finance_voucher', 'finance_write_off',
    'accounting_account', 'accounting_entry',
    'invoice_file', 'wms_task',
    'dc_alarm', 'dc_device', 'dc_gateway', 'dc_point', 'dc_rule', 'dc_sample', 'dc_status_log'
  ];
BEGIN
  FOREACH t IN ARRAY tables LOOP
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema='public' AND table_name=t) THEN
      EXECUTE format(
        'ALTER TABLE %I ADD COLUMN IF NOT EXISTS factory_code varchar(20)', t);
      EXECUTE format(
        'COMMENT ON COLUMN %I.factory_code IS %L', t, '所属账套编码（factory.code）');
      EXECUTE format(
        'CREATE INDEX IF NOT EXISTS %I ON %I (factory_code)',
        'ix_' || t || '_factory', t);
    END IF;
  END LOOP;
END $$;

-- ---------------------------------------------------------------- 2. 存量数据归属
-- 默认账套取 factory 里 id 最小的那条（本地就是 F01 一号工厂）。
-- 这样老数据不会凭空消失：没有归属的记录统一落到默认账套。
DO $$
DECLARE
  default_code varchar(20);
  t text;
  tables text[] := ARRAY[
    'form_data', 'form_approval',
    'stock_balance', 'stock_txn',
    'manu_order', 'manu_order_item',
    'finance_voucher', 'finance_write_off',
    'accounting_account', 'accounting_entry',
    'invoice_file', 'wms_task',
    'dc_alarm', 'dc_device', 'dc_gateway', 'dc_point', 'dc_rule', 'dc_sample', 'dc_status_log'
  ];
BEGIN
  SELECT code INTO default_code FROM factory ORDER BY id LIMIT 1;
  IF default_code IS NULL THEN
    -- 没有任何账套时补一个，避免下面回填全是 NULL
    INSERT INTO factory (code, name) VALUES ('F01', '一号工厂')
    ON CONFLICT (code) DO NOTHING;
    SELECT code INTO default_code FROM factory ORDER BY id LIMIT 1;
  END IF;

  FOREACH t IN ARRAY tables LOOP
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema='public' AND table_name=t AND column_name='factory_code') THEN
      EXECUTE format(
        'UPDATE %I SET factory_code = %L WHERE factory_code IS NULL', t, default_code);
    END IF;
  END LOOP;
END $$;

-- ---------------------------------------------------------------- 3. 关键表设默认值
-- 新写入没显式带账套时落到默认账套，保证历史业务逻辑不会因为 NULL 而丢数据。
DO $$
DECLARE
  default_code varchar(20);
BEGIN
  SELECT code INTO default_code FROM factory ORDER BY id LIMIT 1;
  IF default_code IS NOT NULL THEN
    EXECUTE format('ALTER TABLE form_data ALTER COLUMN factory_code SET DEFAULT %L', default_code);
    EXECUTE format('ALTER TABLE stock_txn ALTER COLUMN factory_code SET DEFAULT %L', default_code);
    EXECUTE format('ALTER TABLE stock_balance ALTER COLUMN factory_code SET DEFAULT %L', default_code);
  END IF;
END $$;

-- form_data 原来没有 (panel_code, form_no) 唯一约束（只有主键），
-- 应用层按 form_no 查重。多账套后同一单号可以出现在不同账套，
-- 这里补一个账套内唯一索引，保证同账套不重复、跨账套互不影响。
CREATE UNIQUE INDEX IF NOT EXISTS uk_form_data_factory_panel_formno
  ON form_data (factory_code, panel_code, form_no)
  WHERE form_no IS NOT NULL;
