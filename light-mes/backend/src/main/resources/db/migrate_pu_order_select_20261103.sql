-- 2026-11-03 采购订单「选请购单」（幂等，可重复执行）
-- 采购订单原配置缺少 selectConfig/selectConfigs，点「选单-选请购单」只会提示未实现。
-- 选单弹窗勾选已审核请购单后，调用请购单的「生成采购订单」按钮，复用已打通的推式生单与来源校验。
-- 适用库：light_mes（MySQL 8.x）

USE light_mes;

SET @sc = JSON_OBJECT(
  'title', '选请购单',
  'tip', '仅显示已审核且未中止的请购单，选中后按请购明细生成采购订单',
  'source', 'PU_REQ',
  'columns', JSON_ARRAY('单据编号', '单据日期', '建议供应商', '请购人', '部门', '需求日期'),
  'detailKey', 'items',
  'generateButton', '生成采购订单',
  'generateLabel', '生成采购订单'
);
SET @scs = JSON_OBJECT('选请购单', CAST(@sc AS JSON));

UPDATE panel_config
SET config = JSON_SET(config,
      '$.selectConfig', CAST(@sc AS JSON),
      '$.selectConfigs', CAST(@scs AS JSON)),
    version = '1.1',
    update_time = NOW()
WHERE panel_code = 'PU_ORDER';

SELECT panel_code,
       JSON_UNQUOTE(JSON_EXTRACT(config, '$.selectConfig.source')) AS source,
       JSON_UNQUOTE(JSON_EXTRACT(config, '$.selectConfigs."选请购单".generateButton')) AS generate_button
FROM panel_config
WHERE panel_code = 'PU_ORDER';
