-- 采购入库单字段口径修正（2026-11-17）
-- 1) 表头「仓库」改为必填：采购订单直入生成的入库单此前可不带仓库，库存无法落库；
-- 2) 「供应商编码」改为文本：原配置是参照（refField=往来单位编码），界面用名称做标签，
--    编码字段显示成了供应商名称；存储值本身是编码，改文本后按编码显示。
WITH fields AS (
  SELECT jsonb_agg(
           CASE
             WHEN f ->> 'dataName' = '仓库' THEN jsonb_set(f, '{isRequired}', 'true'::jsonb)
             WHEN f ->> 'dataName' = '供应商编码' THEN
               (f - 'refPanel' - 'refField' - 'displayField' - 'refColumns') || '{"dataType":"文本"}'::jsonb
             ELSE f
           END
           ORDER BY ord
         ) AS value
  FROM panel_config p,
       LATERAL jsonb_array_elements(p.config::jsonb -> 'dataSchema' -> 'fields')
         WITH ORDINALITY AS t(f, ord)
  WHERE p.panel_code = 'PURCHASE_IN'
)
UPDATE panel_config
SET config = jsonb_set(config::jsonb, '{dataSchema,fields}', (SELECT value FROM fields))::text
WHERE panel_code = 'PURCHASE_IN';
