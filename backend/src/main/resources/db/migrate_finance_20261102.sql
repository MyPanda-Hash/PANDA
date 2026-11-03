-- 2026-11-02 应收应付往来单据表（幂等，可重复执行）
-- 单厂单账套、人民币；AR/AP 共表，用 voucher_type 区分
-- 依赖：与 stock_txn 同批审核触发，来源面板 + 来源单号 唯一

CREATE TABLE IF NOT EXISTS finance_voucher (
  id             BIGINT PRIMARY KEY AUTO_INCREMENT,
  voucher_type   VARCHAR(10)   NOT NULL COMMENT '往来类型：AR 应收 / AP 应付',
  partner        VARCHAR(100)  NOT NULL DEFAULT '' COMMENT '往来单位',
  source_panel   VARCHAR(50)   NOT NULL COMMENT '来源面板编码',
  source_no      VARCHAR(50)   NOT NULL COMMENT '来源单据编号',
  amount         DECIMAL(18,4) NOT NULL DEFAULT 0 COMMENT '原始金额',
  settled_amount DECIMAL(18,4) NOT NULL DEFAULT 0 COMMENT '已核销金额',
  status         VARCHAR(20)   NOT NULL DEFAULT '未结算' COMMENT '结算状态',
  biz_date       DATE          NULL COMMENT '业务日期',
  remark         VARCHAR(500)  NULL COMMENT '备注',
  create_time    DATETIME      DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_source (voucher_type, source_panel, source_no),
  KEY idx_partner (partner, voucher_type),
  KEY idx_status (status),
  KEY idx_biz_date (biz_date)
) COMMENT '应收应付往来单据表';

-- 核销记录：一次收付款可核销多张往来单，逐条留痕
CREATE TABLE IF NOT EXISTS finance_write_off (
  id             BIGINT PRIMARY KEY AUTO_INCREMENT,
  voucher_id     BIGINT        NOT NULL COMMENT '往来单据 ID',
  payment_no     VARCHAR(50)   NOT NULL COMMENT '收款单/付款单编号',
  amount         DECIMAL(18,4) NOT NULL DEFAULT 0 COMMENT '本次核销金额',
  write_off_time DATETIME      DEFAULT CURRENT_TIMESTAMP,
  operator       VARCHAR(50)   NULL COMMENT '操作人',
  KEY idx_writeoff_voucher (voucher_id),
  KEY idx_writeoff_payment (payment_no)
) COMMENT '应收应付核销记录表';
