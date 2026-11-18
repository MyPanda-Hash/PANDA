-- 轻 MES：结构化业务表预留 30 个扩展列（2026-10-22）
--
-- 目的：新增结构化业务字段时占用空闲 ext_* 列，不为单个字段临时 ALTER TABLE，
--       避免改表与实体/接口不同步导致的运行错误。
-- 规则来源：docs/backend/后端逻辑设计.md「预留扩展列」。
--
-- 幂等：表不存在或列已存在时跳过，可重复执行；只新增可空列，不改现有列和索引。
-- 用法（执行前备份目标库）：
--   mysql -uroot -p --default-character-set=utf8mb4 --execute="source tools/update-2026-10-22-reserved-columns.sql"
--
-- 覆盖表：manu_order、manu_order_item。今后新建结构化业务表按同一规则预留。

-- 默认目标库；若测试实例库名不同，改这一行或先手动 USE。
USE light_mes;

DROP PROCEDURE IF EXISTS mes_add_reserved_column;

DELIMITER $$
CREATE PROCEDURE mes_add_reserved_column(
  IN p_table    VARCHAR(64),
  IN p_column   VARCHAR(64),
  IN p_definition VARCHAR(255)
)
BEGIN
  DECLARE v_table_exists  INT DEFAULT 0;
  DECLARE v_column_exists INT DEFAULT 0;

  SELECT COUNT(*) INTO v_table_exists
    FROM information_schema.TABLES
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = p_table;

  SELECT COUNT(*) INTO v_column_exists
    FROM information_schema.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = p_table AND COLUMN_NAME = p_column;

  IF v_table_exists > 0 AND v_column_exists = 0 THEN
    SET @ddl = CONCAT('ALTER TABLE `', p_table, '` ADD COLUMN `', p_column, '` ', p_definition);
    PREPARE stmt FROM @ddl;
    EXECUTE stmt;
    DEALLOCATE PREPARE stmt;
  END IF;
END$$
DELIMITER ;

-- ===== manu_order（生产加工单主表）=====
CALL mes_add_reserved_column('manu_order', 'ext_str1',  'VARCHAR(200)  COMMENT ''预留扩展-文本1''');
CALL mes_add_reserved_column('manu_order', 'ext_str2',  'VARCHAR(200)  COMMENT ''预留扩展-文本2''');
CALL mes_add_reserved_column('manu_order', 'ext_str3',  'VARCHAR(200)  COMMENT ''预留扩展-文本3''');
CALL mes_add_reserved_column('manu_order', 'ext_str4',  'VARCHAR(200)  COMMENT ''预留扩展-文本4''');
CALL mes_add_reserved_column('manu_order', 'ext_str5',  'VARCHAR(200)  COMMENT ''预留扩展-文本5''');
CALL mes_add_reserved_column('manu_order', 'ext_str6',  'VARCHAR(200)  COMMENT ''预留扩展-文本6''');
CALL mes_add_reserved_column('manu_order', 'ext_str7',  'VARCHAR(200)  COMMENT ''预留扩展-文本7''');
CALL mes_add_reserved_column('manu_order', 'ext_str8',  'VARCHAR(200)  COMMENT ''预留扩展-文本8''');
CALL mes_add_reserved_column('manu_order', 'ext_str9',  'VARCHAR(200)  COMMENT ''预留扩展-文本9''');
CALL mes_add_reserved_column('manu_order', 'ext_str10', 'VARCHAR(200)  COMMENT ''预留扩展-文本10''');
CALL mes_add_reserved_column('manu_order', 'ext_num1',  'DECIMAL(18,4) COMMENT ''预留扩展-数值1''');
CALL mes_add_reserved_column('manu_order', 'ext_num2',  'DECIMAL(18,4) COMMENT ''预留扩展-数值2''');
CALL mes_add_reserved_column('manu_order', 'ext_num3',  'DECIMAL(18,4) COMMENT ''预留扩展-数值3''');
CALL mes_add_reserved_column('manu_order', 'ext_num4',  'DECIMAL(18,4) COMMENT ''预留扩展-数值4''');
CALL mes_add_reserved_column('manu_order', 'ext_num5',  'DECIMAL(18,4) COMMENT ''预留扩展-数值5''');
CALL mes_add_reserved_column('manu_order', 'ext_num6',  'DECIMAL(18,4) COMMENT ''预留扩展-数值6''');
CALL mes_add_reserved_column('manu_order', 'ext_num7',  'DECIMAL(18,4) COMMENT ''预留扩展-数值7''');
CALL mes_add_reserved_column('manu_order', 'ext_num8',  'DECIMAL(18,4) COMMENT ''预留扩展-数值8''');
CALL mes_add_reserved_column('manu_order', 'ext_num9',  'DECIMAL(18,4) COMMENT ''预留扩展-数值9''');
CALL mes_add_reserved_column('manu_order', 'ext_num10', 'DECIMAL(18,4) COMMENT ''预留扩展-数值10''');
CALL mes_add_reserved_column('manu_order', 'ext_date1', 'DATETIME     COMMENT ''预留扩展-日期1''');
CALL mes_add_reserved_column('manu_order', 'ext_date2', 'DATETIME     COMMENT ''预留扩展-日期2''');
CALL mes_add_reserved_column('manu_order', 'ext_date3', 'DATETIME     COMMENT ''预留扩展-日期3''');
CALL mes_add_reserved_column('manu_order', 'ext_date4', 'DATETIME     COMMENT ''预留扩展-日期4''');
CALL mes_add_reserved_column('manu_order', 'ext_date5', 'DATETIME     COMMENT ''预留扩展-日期5''');
CALL mes_add_reserved_column('manu_order', 'ext_flag1', 'TINYINT      COMMENT ''预留扩展-标志1''');
CALL mes_add_reserved_column('manu_order', 'ext_flag2', 'TINYINT      COMMENT ''预留扩展-标志2''');
CALL mes_add_reserved_column('manu_order', 'ext_flag3', 'TINYINT      COMMENT ''预留扩展-标志3''');
CALL mes_add_reserved_column('manu_order', 'ext_flag4', 'TINYINT      COMMENT ''预留扩展-标志4''');
CALL mes_add_reserved_column('manu_order', 'ext_flag5', 'TINYINT      COMMENT ''预留扩展-标志5''');

-- ===== manu_order_item（生产加工单工序分录）=====
CALL mes_add_reserved_column('manu_order_item', 'ext_str1',  'VARCHAR(200)  COMMENT ''预留扩展-文本1''');
CALL mes_add_reserved_column('manu_order_item', 'ext_str2',  'VARCHAR(200)  COMMENT ''预留扩展-文本2''');
CALL mes_add_reserved_column('manu_order_item', 'ext_str3',  'VARCHAR(200)  COMMENT ''预留扩展-文本3''');
CALL mes_add_reserved_column('manu_order_item', 'ext_str4',  'VARCHAR(200)  COMMENT ''预留扩展-文本4''');
CALL mes_add_reserved_column('manu_order_item', 'ext_str5',  'VARCHAR(200)  COMMENT ''预留扩展-文本5''');
CALL mes_add_reserved_column('manu_order_item', 'ext_str6',  'VARCHAR(200)  COMMENT ''预留扩展-文本6''');
CALL mes_add_reserved_column('manu_order_item', 'ext_str7',  'VARCHAR(200)  COMMENT ''预留扩展-文本7''');
CALL mes_add_reserved_column('manu_order_item', 'ext_str8',  'VARCHAR(200)  COMMENT ''预留扩展-文本8''');
CALL mes_add_reserved_column('manu_order_item', 'ext_str9',  'VARCHAR(200)  COMMENT ''预留扩展-文本9''');
CALL mes_add_reserved_column('manu_order_item', 'ext_str10', 'VARCHAR(200)  COMMENT ''预留扩展-文本10''');
CALL mes_add_reserved_column('manu_order_item', 'ext_num1',  'DECIMAL(18,4) COMMENT ''预留扩展-数值1''');
CALL mes_add_reserved_column('manu_order_item', 'ext_num2',  'DECIMAL(18,4) COMMENT ''预留扩展-数值2''');
CALL mes_add_reserved_column('manu_order_item', 'ext_num3',  'DECIMAL(18,4) COMMENT ''预留扩展-数值3''');
CALL mes_add_reserved_column('manu_order_item', 'ext_num4',  'DECIMAL(18,4) COMMENT ''预留扩展-数值4''');
CALL mes_add_reserved_column('manu_order_item', 'ext_num5',  'DECIMAL(18,4) COMMENT ''预留扩展-数值5''');
CALL mes_add_reserved_column('manu_order_item', 'ext_num6',  'DECIMAL(18,4) COMMENT ''预留扩展-数值6''');
CALL mes_add_reserved_column('manu_order_item', 'ext_num7',  'DECIMAL(18,4) COMMENT ''预留扩展-数值7''');
CALL mes_add_reserved_column('manu_order_item', 'ext_num8',  'DECIMAL(18,4) COMMENT ''预留扩展-数值8''');
CALL mes_add_reserved_column('manu_order_item', 'ext_num9',  'DECIMAL(18,4) COMMENT ''预留扩展-数值9''');
CALL mes_add_reserved_column('manu_order_item', 'ext_num10', 'DECIMAL(18,4) COMMENT ''预留扩展-数值10''');
CALL mes_add_reserved_column('manu_order_item', 'ext_date1', 'DATETIME     COMMENT ''预留扩展-日期1''');
CALL mes_add_reserved_column('manu_order_item', 'ext_date2', 'DATETIME     COMMENT ''预留扩展-日期2''');
CALL mes_add_reserved_column('manu_order_item', 'ext_date3', 'DATETIME     COMMENT ''预留扩展-日期3''');
CALL mes_add_reserved_column('manu_order_item', 'ext_date4', 'DATETIME     COMMENT ''预留扩展-日期4''');
CALL mes_add_reserved_column('manu_order_item', 'ext_date5', 'DATETIME     COMMENT ''预留扩展-日期5''');
CALL mes_add_reserved_column('manu_order_item', 'ext_flag1', 'TINYINT      COMMENT ''预留扩展-标志1''');
CALL mes_add_reserved_column('manu_order_item', 'ext_flag2', 'TINYINT      COMMENT ''预留扩展-标志2''');
CALL mes_add_reserved_column('manu_order_item', 'ext_flag3', 'TINYINT      COMMENT ''预留扩展-标志3''');
CALL mes_add_reserved_column('manu_order_item', 'ext_flag4', 'TINYINT      COMMENT ''预留扩展-标志4''');
CALL mes_add_reserved_column('manu_order_item', 'ext_flag5', 'TINYINT      COMMENT ''预留扩展-标志5''');

DROP PROCEDURE IF EXISTS mes_add_reserved_column;

-- 自检：每张表应返回 30 行预留列
SELECT TABLE_NAME AS '表', COUNT(*) AS '预留列数'
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = DATABASE()
   AND TABLE_NAME IN ('manu_order', 'manu_order_item')
   AND REGEXP_LIKE(COLUMN_NAME, '^ext_(str|num|date|flag)[0-9]+$')
 GROUP BY TABLE_NAME
 ORDER BY TABLE_NAME;