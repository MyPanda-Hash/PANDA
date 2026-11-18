-- 2026-11-05 采购订单明细「物料编码」参照回填修正（幂等，可重复执行）
-- 问题：
--   1) 物料编码 displayField=存货名称，参照下拉选中后编码列显示成名称；
--   2) 物料编码没有 refMap，选择后物料名称/规格型号/单位都不回填。
-- 处理：
--   编码列显示编码本身；refMap 带出名称/规格/单位。物料名称字段保持原映射（编码/规格/单位）。
-- 适用库：light_mes（MySQL 8.x）

USE light_mes;

SELECT config INTO @cfg FROM panel_config WHERE panel_code='PU_ORDER';

-- 物料编码：显示编码本身，带出名称/规格/单位
SET @tab_idx = NULL; SET @field_idx = NULL;
SELECT t.tab_idx, f.field_idx INTO @tab_idx, @field_idx
FROM JSON_TABLE(@cfg, '$.detail.tabs[*]' COLUMNS (
  tab_idx FOR ORDINALITY,
  fields JSON PATH '$.fields'
)) t,
JSON_TABLE(t.fields, '$[*]' COLUMNS (
  field_idx FOR ORDINALITY,
  name VARCHAR(80) PATH '$.dataName'
)) f
WHERE f.name='物料编码' LIMIT 1;
SET @base = CONCAT('$.detail.tabs[', @tab_idx - 1, '].fields[', @field_idx - 1, ']');
SET @cfg = JSON_SET(@cfg, CONCAT(@base, '.displayField'), '存货编码');
SET @cfg = JSON_SET(@cfg, CONCAT(@base, '.refColumns'),
  JSON_ARRAY('存货编码', '存货名称', '规格型号', '计量单位', '停用'));
SET @cfg = JSON_SET(@cfg, CONCAT(@base, '.refMap'), JSON_ARRAY(
  JSON_OBJECT('to', '物料名称', 'from', '存货名称'),
  JSON_OBJECT('to', '规格型号', 'from', '规格型号'),
  JSON_OBJECT('to', '单位', 'from', '计量单位')
));

UPDATE panel_config
SET config = @cfg, version = '1.1', update_time = NOW()
WHERE panel_code = 'PU_ORDER';

SELECT panel_code,
       JSON_UNQUOTE(JSON_EXTRACT(config, CONCAT('$.detail.tabs[', @tab_idx - 1, '].fields[', @field_idx - 1, '].displayField'))) AS display_field
FROM panel_config
WHERE panel_code = 'PU_ORDER';
