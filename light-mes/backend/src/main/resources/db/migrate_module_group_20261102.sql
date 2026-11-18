-- 2026-11-02 面板模块分组修正（幂等，可重复执行）
-- 目标：按五合一蓝图把核心出入库单归入仓存，PDM 主数据脱离生产组
-- 依据：docs/design/面板全景与五合一对照.md 第 5 节
--
-- 变更点：
--   1) 四个核心出入库单 + 配货单 → 统一归 wms（原分散在 prod/sales/purchase）
--   2) 工艺路线/工序/工作中心/班组 → 归 pdm（原 prod），BOM 相关已属独立组
--   3) DISPATCH 派工单归正（原 other → prod）
--
-- 注意：module_group 是权限矩阵的折叠维度，改动会影响组织架构页分组展示，
-- 不影响 role_panel 已有的授权记录（授权以 panel_code 为键）。

-- 1. 统一建立 wms 分组：核心出入库与仓内作业
UPDATE panel_config SET module_group = 'wms' WHERE panel_code IN (
  'PURCHASE_IN', 'PURCHASE_IN_DETAIL', 'PURCHASE_IN_STATS',
  'FINISH_IN', 'FINISH_IN_DETAIL', 'FINISH_IN_STATS',
  'MATERIAL_OUT', 'MATERIAL_OUT_DETAIL', 'MATERIAL_OUT_STATS',
  'SALE_OUT', 'SALE_OUT_DETAIL', 'SALE_OUT_STATS',
  'OTHER_IN', 'OTHER_IN_DETAIL', 'OTHER_IN_STATS',
  'OTHER_OUT', 'OTHER_OUT_DETAIL', 'OTHER_OUT_STATS',
  'TRANSFER', 'STOCK_CHECK', 'LOCATION_ADJUST',
  'PICK_ORDER', 'PICK_ORDER_DETAIL', 'PICK_ORDER_STATS', 'PICK_ORDER_SUMMARY',
  'SERIAL_NO', 'SERIAL_STATUS', 'SERIAL_TRACE',
  'STOCK_STATUS', 'STOCK_SUMMARY', 'STOCK_LEDGER'
);

-- 2. PDM 主数据：工艺与制造基础数据脱离生产组
UPDATE panel_config SET module_group = 'pdm' WHERE panel_code IN (
  'BOM', 'BOM_FWD', 'BOM_REV',
  'ROUTE', 'OP', 'OP_CONV', 'WC', 'TEAM', 'EQUIP'
);

-- 3. 派工单归正：other → prod
UPDATE panel_config SET module_group = 'prod' WHERE panel_code = 'DISPATCH';

-- 4. 校验输出
SELECT module_group, COUNT(*) AS panels
FROM panel_config
GROUP BY module_group
ORDER BY panels DESC;
