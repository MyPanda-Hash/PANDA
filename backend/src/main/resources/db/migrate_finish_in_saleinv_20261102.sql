-- 2026-11-02 产成品入库单增加「生成销货单」生单按钮（幂等）
-- 补齐 O2C 的 FINISH_IN → SALE_INV 缺环前端入口
-- 后端动作已在 PxService.callButton 实现（createSaleInvFromFinishIn）
--
-- 说明：MySQL 的 JSON_CONTAINS 不支持通配路径，统一用 JSON_SEARCH 判定是否已存在。

-- 1) 是否已存在该动作（分组或按钮任一命中即跳过）
SET @cfg_g = (SELECT config FROM panel_config WHERE panel_code = 'FINISH_IN');
SET @in_groups = JSON_SEARCH(@cfg_g, 'one', '生成销货单', NULL, '$.metadata.buttonGroups[*].actions');
SET @in_buttons = JSON_SEARCH(@cfg_g, 'one', '生成销货单', NULL, '$.metadata.panelButtons[*].buttonName');

-- 2) 有「生单」组：把动作追加到该组末尾
SET @idx = (SELECT MIN(t.i) - 1 FROM
    JSON_TABLE(JSON_EXTRACT(@cfg_g, '$.metadata.buttonGroups'), '$[*]'
      COLUMNS (i FOR ORDINALITY, nm VARCHAR(64) PATH '$.name')) AS t
    WHERE t.nm = '生单');

UPDATE panel_config
SET config = JSON_ARRAY_APPEND(config, CONCAT('$.metadata.buttonGroups[', @idx, '].actions'), '生成销货单')
WHERE panel_code = 'FINISH_IN' AND @in_groups IS NULL AND @idx IS NOT NULL;

-- 3) 无「生单」组：新建一组
UPDATE panel_config
SET config = JSON_ARRAY_APPEND(config, '$.metadata.buttonGroups',
      JSON_OBJECT('name', '生单', 'actions', JSON_ARRAY('生成销货单')))
WHERE panel_code = 'FINISH_IN' AND @in_groups IS NULL AND @idx IS NULL;

-- 4) 同步 panelButtons（前端按钮可见性读这里）
UPDATE panel_config
SET config = JSON_ARRAY_APPEND(config, '$.metadata.panelButtons', JSON_OBJECT('buttonName', '生成销货单'))
WHERE panel_code = 'FINISH_IN' AND @in_buttons IS NULL;

-- 5) 校验
SELECT panel_code,
       JSON_SEARCH(config, 'one', '生成销货单', NULL, '$.metadata.buttonGroups[*].actions') AS in_groups,
       JSON_SEARCH(config, 'one', '生成销货单', NULL, '$.metadata.panelButtons[*].buttonName') AS in_buttons
FROM panel_config WHERE panel_code = 'FINISH_IN';
