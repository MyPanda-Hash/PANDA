-- 2026-11-04 移除进货单（PU_IN），发票改由采购入库单/采购退货单生成
-- 背景：进货单是 T+ 兼容遗留单据，金蝶标准链没有该单据；采购发票正规来源为
--       采购入库单（正票）/采购退货单（红字）。本迁移幂等，可重复执行。
-- 适用库：light_mes（MySQL 8.x）
--
-- 说明：
--   1) 删除面板、菜单、角色授权；form_data 历史单据保留（不再展示），
--      如需彻底清理请人工备份后执行：DELETE FROM form_data WHERE panel_code='PU_IN';
--   2) 按钮/选单配置由后端启动时的注册器升级（PurchasePanelRegistry / MenuPanelRegistry /
--      ReturnPanelRegistry），执行本脚本后需重启后端。

USE light_mes;

-- 1) 菜单与角色授权（sys_menu 无 panel_code 列，按菜单编码/路由删除）
DELETE FROM sys_menu WHERE code = 'puIn' OR path IN ('/panelx/list/PU_IN', '/scm/purchase/puIn');
DELETE FROM sys_role_panel WHERE panel_code = 'PU_IN';

-- 2) 面板配置（历史 form_data 保留）
DELETE FROM panel_config WHERE panel_code = 'PU_IN';

-- 3) 列偏好（表可能尚未创建，按存在性执行，保持幂等）
SET @has_column_pref := (
  SELECT COUNT(*) FROM information_schema.tables
  WHERE table_schema = DATABASE() AND table_name = 'px_column_pref'
);
SET @sql := IF(@has_column_pref > 0,
  'DELETE FROM px_column_pref WHERE panel_code = ''PU_IN''',
  'SELECT 1');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 4) 采购订单：摘掉「生成进货单」动作（注册器启动时也会再做一次，双保险）
UPDATE panel_config
SET config = REPLACE(config, '"生成进货单",', '')
WHERE panel_code = 'PU_ORDER' AND config LIKE '%"生成进货单"%';

-- 5) 采购发票：来源改为采购入库单（选单配置由 MenuPanelRegistry 从 JSON 覆盖）
UPDATE panel_config
SET config = REPLACE(config, '选进货单', '选采购入库单')
WHERE panel_code = 'PU_INVOICE' AND config LIKE '%选进货单%';

SELECT panel_code, panel_name
FROM panel_config
WHERE panel_code IN ('PU_ORDER', 'PURCHASE_IN', 'PURCHASE_RETURN', 'PU_INVOICE')
ORDER BY panel_code;
