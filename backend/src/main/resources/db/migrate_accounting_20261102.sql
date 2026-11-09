-- 2026-11-02 会计凭证分录表（幂等，可重复执行）
-- 与 finance_voucher（往来单据）区分：本表是复式记账的借贷明细
-- 借贷平衡由 AccountingService.assertBalanced 在落库前校验

CREATE TABLE IF NOT EXISTS accounting_entry (
  id            BIGINT PRIMARY KEY AUTO_INCREMENT,
  voucher_no    VARCHAR(80)   NOT NULL COMMENT '凭证号（记-yyyyMMdd-来源单号）',
  source_panel  VARCHAR(50)   NOT NULL COMMENT '来源面板编码',
  source_no     VARCHAR(50)   NOT NULL COMMENT '来源单据编号',
  account_code  VARCHAR(20)   NOT NULL COMMENT '科目编码',
  account_name  VARCHAR(100)  NOT NULL COMMENT '科目名称',
  direction     VARCHAR(4)    NOT NULL COMMENT '方向：借/贷',
  amount        DECIMAL(18,4) NOT NULL DEFAULT 0 COMMENT '金额',
  partner       VARCHAR(100)  NULL COMMENT '往来单位',
  summary       VARCHAR(500)  NULL COMMENT '摘要',
  biz_date      DATE          NULL COMMENT '业务日期',
  create_time   DATETIME      DEFAULT CURRENT_TIMESTAMP,
  KEY idx_entry_source (source_panel, source_no),
  KEY idx_entry_account (account_code),
  KEY idx_entry_voucher (voucher_no),
  KEY idx_entry_date (biz_date)
) COMMENT '会计凭证分录表';

-- 会计科目基础表：预置小企业会计准则常用科目
CREATE TABLE IF NOT EXISTS accounting_account (
  account_code VARCHAR(20)  PRIMARY KEY COMMENT '科目编码',
  account_name VARCHAR(100) NOT NULL COMMENT '科目名称',
  category     VARCHAR(20)  NOT NULL COMMENT '科目类别：资产/负债/权益/收入/费用',
  direction    VARCHAR(4)   NOT NULL COMMENT '余额方向：借/贷',
  enabled      TINYINT      DEFAULT 1 COMMENT '1 启用 0 停用'
) COMMENT '会计科目表';

INSERT INTO accounting_account (account_code, account_name, category, direction, enabled) VALUES
  ('1002', '银行存款',       '资产', '借', 1),
  ('1122', '应收账款',       '资产', '借', 1),
  ('1405', '库存商品',       '资产', '借', 1),
  ('2202', '应付账款',       '负债', '贷', 1),
  ('6001', '主营业务收入',   '收入', '贷', 1),
  ('6401', '主营业务成本',   '费用', '借', 1),
  ('222101', '应交税费-应交增值税(销项税额)', '负债', '贷', 1)
ON DUPLICATE KEY UPDATE account_name = account_name;

SELECT COUNT(*) AS accounts FROM accounting_account;
