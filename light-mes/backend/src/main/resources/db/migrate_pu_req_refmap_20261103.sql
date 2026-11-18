-- 2026-11-03 请购单明细「存货」参照回填修正（幂等，可重复执行）
-- 问题：
--   1) 存货编码列 displayField=存货名称，编码列显示成名称；
--   2) 存货编码 refMap 只带出规格型号/采购单位，未带出存货名称；
--   3) 存货名称字段没有 refMap，单独点它不会带出编码/规格/单位。
-- 处理：
--   只带出名称/规格/采购单位；单价不映射（存货档案无「单价」字段，保持 0 由用户填写）。
-- 适用库：light_mes（MySQL 8.x）

USE light_mes;

SELECT config INTO @cfg FROM panel_config WHERE panel_code='PU_REQ';

-- 存货编码：显示编码本身，带出名称/规格/采购单位
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
WHERE f.name='存货编码' LIMIT 1;
SET @base = CONCAT('$.detail.tabs[', @tab_idx - 1, '].fields[', @field_idx - 1, ']');
SET @cfg = JSON_SET(@cfg, CONCAT(@base, '.displayField'), '存货编码');
SET @cfg = JSON_SET(@cfg, CONCAT(@base, '.refColumns'),
  JSON_ARRAY('存货编码', '存货名称', '规格型号', '计量单位', '停用'));
SET @cfg = JSON_SET(@cfg, CONCAT(@base, '.refMap'), JSON_ARRAY(
  JSON_OBJECT('to', '存货名称', 'from', '存货名称'),
  JSON_OBJECT('to', '规格型号', 'from', '规格型号'),
  JSON_OBJECT('to', '采购单位', 'from', '计量单位')
));

-- 存货名称：单独点名称参照时也带出编码/规格/采购单位
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
WHERE f.name='存货名称' LIMIT 1;
SET @base = CONCAT('$.detail.tabs[', @tab_idx - 1, '].fields[', @field_idx - 1, ']');
SET @cfg = JSON_SET(@cfg, CONCAT(@base, '.refColumns'),
  JSON_ARRAY('存货编码', '存货名称', '规格型号', '计量单位', '停用'));
SET @cfg = JSON_SET(@cfg, CONCAT(@base, '.refMap'), JSON_ARRAY(
  JSON_OBJECT('to', '存货编码', 'from', '存货编码'),
  JSON_OBJECT('to', '规格型号', 'from', '规格型号'),
  JSON_OBJECT('to', '采购单位', 'from', '计量单位')
));

UPDATE panel_config
SET config = @cfg, version = '1.1', update_time = NOW()
WHERE panel_code = 'PU_REQ';

SELECT jt.dataName, jt.displayField, jt.refMap
FROM panel_config p, JSON_TABLE(p.config, '$.detail.tabs[*].fields[*]' COLUMNS (
  dataName VARCHAR(80) PATH '$.dataName',
  displayField VARCHAR(80) PATH '$.displayField',
  refMap JSON PATH '$.refMap'
)) jt
WHERE p.panel_code='PU_REQ' AND jt.dataName IN ('存货编码', '存货名称');
