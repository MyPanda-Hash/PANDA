-- 轻 MES：存货档案列表补「品牌」列（2026-11-03）
--
-- 为什么：存货(INV)的明细字段里本来就有 品牌，但列表列没列出来 —— 用户看存货档案看不到、
-- 也改不了品牌，于是销售订单里 `存货名称.品牌` 这一列永远是空的（以为是"存货没有这个字段"）。
-- 本次把品牌列进存货列表；销售订单/其它单据的 `参照字段.属性` 列由前端按档案带出显示
-- （core/views/ref-carry.js），存量单据不用回填数据也能看到品牌。
--
-- 幂等：已经含「品牌」列时整条 UPDATE 不命中；可重复执行。
-- 用法：mysql -uroot -p --default-character-set=utf8mb4 --execute="source backend/src/main/resources/db/migrate_inv_brand_20261103.sql"

USE light_mes;

UPDATE panel_config
   SET config = CAST(
         JSON_SET(
           CAST(config AS JSON),
           '$.metadata.panelPageDto.tablePages[0].gridTabs[0].columns',
           JSON_ARRAY('存货编码', '所属类别', '存货名称', '规格型号', '品牌', '计量单位',
                      '属性', '参考成本', '最新成本', '停用')
         ) AS CHAR
       )
 WHERE panel_code = 'INV'
   AND config IS NOT NULL
   AND JSON_VALID(config)
   AND JSON_SEARCH(config, 'one', '品牌', NULL,
                   '$.metadata.panelPageDto.tablePages[0].gridTabs[0].columns[*]') IS NULL;
