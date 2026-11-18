-- 2026-11-01 一期：库存余额四维化 + form_data 版本字段
-- 幂等执行，可重复运行；不回算历史单据（历史数据为测试数据，最终删除）
-- 注意：MySQL 8 不支持 ALTER TABLE ... ADD COLUMN IF NOT EXISTS（那是 MariaDB 语法），
-- 因此用 information_schema 判断后再动态执行，保证可重复运行。

-- 1. form_data 增加版本与父单号
SET @ddl = (
  SELECT IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'form_data' AND COLUMN_NAME = 'version') = 0,
    'ALTER TABLE form_data ADD COLUMN version INT DEFAULT 1 COMMENT ''单据版本号，变更单递增''',
    'DO 0')
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = (
  SELECT IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'form_data' AND COLUMN_NAME = 'parent_no') = 0,
    'ALTER TABLE form_data ADD COLUMN parent_no VARCHAR(50) NULL COMMENT ''父单号：变更单指向被变更原单''',
    'DO 0')
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = (
  SELECT IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'form_data' AND COLUMN_NAME = 'current_version') = 0,
    'ALTER TABLE form_data ADD COLUMN current_version TINYINT DEFAULT 1 COMMENT ''是否当前生效版本：1 生效，0 已被取代''',
    'DO 0')
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = (
  SELECT IF(
    (SELECT COUNT(*) FROM information_schema.STATISTICS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'form_data' AND INDEX_NAME = 'idx_parent_no') = 0,
    'ALTER TABLE form_data ADD KEY idx_parent_no (panel_code, parent_no)',
    'DO 0')
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 2. 库存余额表（四维）
CREATE TABLE IF NOT EXISTS stock_balance (
  id              BIGINT PRIMARY KEY AUTO_INCREMENT,
  item_code       VARCHAR(50)   NOT NULL COMMENT '存货编码',
  item_name       VARCHAR(200)  NULL COMMENT '存货名称',
  spec            VARCHAR(200)  NULL COMMENT '规格型号',
  unit            VARCHAR(20)   NULL COMMENT '计量单位',
  warehouse       VARCHAR(50)   NOT NULL COMMENT '仓库',
  location        VARCHAR(50)   NOT NULL DEFAULT '' COMMENT '库位',
  batch_no        VARCHAR(50)   NOT NULL DEFAULT '' COMMENT '批次号',
  quantity        DECIMAL(18,4) NOT NULL DEFAULT 0 COMMENT '结存数量',
  locked_quantity DECIMAL(18,4) NOT NULL DEFAULT 0 COMMENT '锁定量（已占用未出库）',
  amount          DECIMAL(18,4) NOT NULL DEFAULT 0 COMMENT '结存金额',
  update_time     DATETIME      DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  ext_str1      VARCHAR(200)  COMMENT '预留扩展-文本1',
  ext_str2      VARCHAR(200)  COMMENT '预留扩展-文本2',
  ext_str3      VARCHAR(200)  COMMENT '预留扩展-文本3',
  ext_str4      VARCHAR(200)  COMMENT '预留扩展-文本4',
  ext_str5      VARCHAR(200)  COMMENT '预留扩展-文本5',
  ext_str6      VARCHAR(200)  COMMENT '预留扩展-文本6',
  ext_str7      VARCHAR(200)  COMMENT '预留扩展-文本7',
  ext_str8      VARCHAR(200)  COMMENT '预留扩展-文本8',
  ext_str9      VARCHAR(200)  COMMENT '预留扩展-文本9',
  ext_str10     VARCHAR(200)  COMMENT '预留扩展-文本10',
  ext_num1      DECIMAL(18,4) COMMENT '预留扩展-数值1',
  ext_num2      DECIMAL(18,4) COMMENT '预留扩展-数值2',
  ext_num3      DECIMAL(18,4) COMMENT '预留扩展-数值3',
  ext_num4      DECIMAL(18,4) COMMENT '预留扩展-数值4',
  ext_num5      DECIMAL(18,4) COMMENT '预留扩展-数值5',
  ext_num6      DECIMAL(18,4) COMMENT '预留扩展-数值6',
  ext_num7      DECIMAL(18,4) COMMENT '预留扩展-数值7',
  ext_num8      DECIMAL(18,4) COMMENT '预留扩展-数值8',
  ext_num9      DECIMAL(18,4) COMMENT '预留扩展-数值9',
  ext_num10     DECIMAL(18,4) COMMENT '预留扩展-数值10',
  ext_date1     DATETIME      COMMENT '预留扩展-日期1',
  ext_date2     DATETIME      COMMENT '预留扩展-日期2',
  ext_date3     DATETIME      COMMENT '预留扩展-日期3',
  ext_date4     DATETIME      COMMENT '预留扩展-日期4',
  ext_date5     DATETIME      COMMENT '预留扩展-日期5',
  ext_flag1     TINYINT       COMMENT '预留扩展-标记1',
  ext_flag2     TINYINT       COMMENT '预留扩展-标记2',
  ext_flag3     TINYINT       COMMENT '预留扩展-标记3',
  ext_flag4     TINYINT       COMMENT '预留扩展-标记4',
  ext_flag5     TINYINT       COMMENT '预留扩展-标记5',
  UNIQUE KEY uk_stock_dim (item_code, warehouse, location, batch_no),
  KEY idx_stock_item (item_code)
) COMMENT '库存余额表';

-- 3. 库存事务表
CREATE TABLE IF NOT EXISTS stock_txn (
  id          BIGINT PRIMARY KEY AUTO_INCREMENT,
  panel_code  VARCHAR(50)   NOT NULL COMMENT '来源面板编码',
  form_no     VARCHAR(50)   NOT NULL COMMENT '来源单据编号',
  row_no      INT           NOT NULL DEFAULT 0 COMMENT '来源行号',
  direction   VARCHAR(10)   NOT NULL COMMENT '方向：IN/OUT/INITIAL',
  item_code   VARCHAR(50)   NOT NULL COMMENT '存货编码',
  item_name   VARCHAR(200)  NULL COMMENT '存货名称',
  spec        VARCHAR(200)  NULL COMMENT '规格型号',
  unit        VARCHAR(20)   NULL COMMENT '计量单位',
  warehouse   VARCHAR(50)   NOT NULL COMMENT '仓库',
  location    VARCHAR(50)   NOT NULL DEFAULT '' COMMENT '库位',
  batch_no    VARCHAR(50)   NOT NULL DEFAULT '' COMMENT '批次号',
  quantity    DECIMAL(18,4) NOT NULL DEFAULT 0 COMMENT '数量（正数）',
  price       DECIMAL(18,4) NOT NULL DEFAULT 0 COMMENT '单价',
  amount      DECIMAL(18,4) NOT NULL DEFAULT 0 COMMENT '金额（正数）',
  biz_date    DATE          NULL COMMENT '业务日期',
  create_time DATETIME      DEFAULT CURRENT_TIMESTAMP,
  KEY idx_txn_doc (panel_code, form_no),
  KEY idx_txn_item (item_code, warehouse),
  KEY idx_txn_date (biz_date)
) COMMENT '库存事务表';
