-- 采购发票改回通用表单布局（2026-11-17）
-- 原 metadata.formSections 把表头拆成「基本信息/发票信息」两段并启用独立操作栏，
-- formSummary 额外渲染「金额信息」块；发票本质上与其他单据一样：字段是普通表头，
-- 商品是普通明细行。移除这两个 metadata 后由 PanelxForm 走通用布局。
UPDATE panel_config
SET config = (config::jsonb #- '{metadata,formSections}' #- '{metadata,formSummary}')::text
WHERE panel_code = 'PU_INVOICE';
