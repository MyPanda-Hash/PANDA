-- ============================================================
-- 2026-09-01 阶段 B：11 项权限矩阵 + 模块分组后端化
--  1) sys_role_panel 加 perms 列（逗号分隔权限码），存量按 can_approve 回填
--  2) panel_config 加 module_group 列，按组织架构 12 模块组播种，未映射兜底 other
-- 幂等：可重复执行（列存在时跳过 ALTER；回填/种子按条件执行）
-- ============================================================
-- 适用库：light_mes（MySQL 8.x）

-- ---------- 1. sys_role_panel.perms ----------
SET @col_exists := (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'sys_role_panel' AND COLUMN_NAME = 'perms'
);
SET @ddl := IF(@col_exists = 0,
  'ALTER TABLE sys_role_panel ADD COLUMN perms VARCHAR(500) NULL COMMENT ''逗号分隔权限码 view,query,add,edit,delete,export,print,audit,price,review,adjust'' AFTER panel_code',
  'SELECT ''sys_role_panel.perms 已存在，跳过'' AS msg');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 存量回填：可见面板给 view，可审批的补 audit（perms 已有值的行不动）
UPDATE sys_role_panel
   SET perms = CONCAT('view', IF(can_approve = 1, ',audit', ''))
 WHERE perms IS NULL OR perms = '';

-- ---------- 2. panel_config.module_group ----------
SET @col_exists := (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'panel_config' AND COLUMN_NAME = 'module_group'
);
SET @ddl := IF(@col_exists = 0,
  'ALTER TABLE panel_config ADD COLUMN module_group VARCHAR(50) NULL COMMENT ''所属业务模块分组'' AFTER category',
  'SELECT ''panel_config.module_group 已存在，跳过'' AS msg');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 模块组种子（与前端原 PANEL_GROUPS 12 组一致；重复执行仅补空值）
UPDATE panel_config SET module_group = 'prod' WHERE (module_group IS NULL OR module_group = '') AND panel_code IN (
  'MANU_ORDER','PROCESS_REPORT','REWORK_REPORT','MATERIAL_REQ','MATERIAL_OUT','FINISH_IN','TRANSFER',
  'MANU_ORDER_EXEC','MANU_ORDER_TRACKER','MANU_ORDER_PRODUCT_DETAIL','MANU_ORDER_MATERIAL_DETAIL','MANU_ORDER_DETAIL','PROC_DETAIL',
  'MANU_ORDER_PRODUCT_STATS','MANU_ORDER_MATERIAL_STATS','MANU_ORDER_STATS','MANU_PROC_STATS','PROC_STATS','SALARY_STATS','SALARY_DETAIL',
  'FINISH_IN_DETAIL','FINISH_IN_STATS','MATERIAL_OUT_DETAIL','MATERIAL_OUT_STATS',
  'ROUTE','OP','TEAM','WC','OP_CONV');
UPDATE panel_config SET module_group = 'outsource' WHERE (module_group IS NULL OR module_group = '') AND panel_code IN (
  'OUTSOURCE_ORDER','OUTSOURCE_ISSUE','OUTSOURCE_IN','OUTSOURCE_FEE',
  'OUTSOURCE_ISSUE_BALANCE','OUTSOURCE_ORDER_EXEC','OUTSOURCE_ORDER_PRODUCT_DETAIL','OUTSOURCE_ORDER_MATERIAL_DETAIL','OUTSOURCE_FEE_DETAIL',
  'OUTSOURCE_ORDER_PRODUCT_STATS','OUTSOURCE_ORDER_MATERIAL_STATS','OUTSOURCE_FEE_STATS');
UPDATE panel_config SET module_group = 'sales' WHERE (module_group IS NULL OR module_group = '') AND panel_code IN (
  'QUOTE_ORDER','SO_ORDER','SALE_INV','SALE_OUT','SALE_INVOICE','EXPENSE','SALE_COST_ALLOC',
  'SALES_ORDER_DETAIL','SALES_ORDER_STATS','SALES_ORDER_EXEC','SALES_ORDER_PROGRESS','SALE_OUT_DETAIL','SALE_OUT_STATS');
UPDATE panel_config SET module_group = 'purchase' WHERE (module_group IS NULL OR module_group = '') AND panel_code IN (
  'PU_REQ','PU_ORDER','PURCHASE_IN','PU_IN','PU_INVOICE','PU_COST_ALLOC','PU_REQ_ANALYSIS',
  'PURCHASE_IN_DETAIL','PURCHASE_IN_STATS');
UPDATE panel_config SET module_group = 'distribution' WHERE (module_group IS NULL OR module_group = '') AND panel_code IN (
  'PICK_ORDER','OTHER_IN','OTHER_OUT',
  'PICK_ORDER_DETAIL','PICK_ORDER_STATS','PICK_ORDER_SUMMARY','OTHER_IN_DETAIL','OTHER_IN_STATS','OTHER_OUT_DETAIL','OTHER_OUT_STATS');
UPDATE panel_config SET module_group = 'inv' WHERE (module_group IS NULL OR module_group = '') AND panel_code IN (
  'STOCK_STATUS','STOCK_SUMMARY','STOCK_LEDGER');
UPDATE panel_config SET module_group = 'pda' WHERE (module_group IS NULL OR module_group = '') AND panel_code IN (
  'STOCK_CHECK','LOCATION_ADJUST');
UPDATE panel_config SET module_group = 'sn' WHERE (module_group IS NULL OR module_group = '') AND panel_code IN (
  'SERIAL_NO','SERIAL_STATUS','SERIAL_TRACE');
UPDATE panel_config SET module_group = 'qc' WHERE (module_group IS NULL OR module_group = '') AND panel_code IN (
  'ARRIVAL_IN','FINISH_INSPECT','FIRST_INSPECT','PROCESS_INSPECT_APPLY','INSPECTION','PROCESS_INSPECTION',
  'ARRIVAL_IN_DETAIL','ARRIVAL_IN_STATS','ARRIVAL_IN_EXEC',
  'FINISH_INSPECT_DETAIL','FINISH_INSPECT_STATS','FINISH_INSPECT_EXEC',
  'FIRST_INSPECT_DETAIL','FIRST_INSPECT_STATS','FIRST_INSPECT_EXEC',
  'PROCESS_INSPECT_APPLY_DETAIL','PROCESS_INSPECT_APPLY_STATS','PROCESS_INSPECT_APPLY_EXEC',
  'QUALITY_STATS_ANALYSIS','INSPECTION_DETAIL','INSPECTION_STATS','QC_ITEM_LIST','QC_ITEM_STATS',
  'COMPANY_TRACE_SETTINGS','CUSTOMER_TRACE_SETTINGS','TRACE_PRINT_TEMPLATE',
  'PRODUCT_FORWARD_TRACE','MATERIAL_REVERSE_TRACE');
UPDATE panel_config SET module_group = 'archives' WHERE (module_group IS NULL OR module_group = '') AND panel_code IN (
  'INV','INV_PRICE','PARTNER','PARTNER_INV','DEPT','EMP','EQUIP','WH','UOM','PROJ','REGION','REJECT','QC_ITEM','QC_PLAN','BOM');
UPDATE panel_config SET module_group = 'query' WHERE (module_group IS NULL OR module_group = '') AND panel_code IN (
  'BOM_FWD','BOM_REV');
UPDATE panel_config SET module_group = 'sys' WHERE (module_group IS NULL OR module_group = '') AND panel_code IN (
  'SYS_ALARM','SYS_BILL_DESIGN','SYS_BOARD_AUTH','SYS_CODE','SYS_MOBILE','SYS_MOBILE_TPL','SYS_OPT','SYS_PRINT','SYS_PRINT_DEFAULT',
  'SYS_SCREEN','SYS_SCREEN_DL','SYS_TASK','COST_MAINTAIN','INIT_AP','INIT_AR','INIT_BALANCE');

-- 兜底：仍无分组的面板归入 other
UPDATE panel_config SET module_group = 'other' WHERE module_group IS NULL OR module_group = '';

-- ---------- 校验 ----------
SELECT module_group, COUNT(*) AS panels FROM panel_config GROUP BY module_group ORDER BY module_group;
SELECT COUNT(*) AS rows_total,
       SUM(perms IS NULL OR perms = '') AS rows_without_perms,
       SUM(perms LIKE '%audit%') AS rows_with_audit
  FROM sys_role_panel;
