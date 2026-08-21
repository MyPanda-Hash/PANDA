-- 库存看板种子数据（幂等：form_no 已存在则跳过）
-- 对齐演示风格：华东铝业/铝棒等基础档案；执行：mysql -uroot -proot light_mes < seed_stock.sql

INSERT INTO form_data (panel_code, form_no, data, detail_data, status, create_by, create_time, audit_by, audit_time)
SELECT 'FINISH_IN', 'FI-2026-08-0001',
'{"编号":"FI-2026-08-0001","单据日期":"2026-08-16","单据编号":"FI-2026-08-0001","单据状态":"已审核","业务类型":"产成品入库","入库类别":"自制加工入库","生产车间":"熔铸车间","加工单号":"MO-2026-08-0009","经手人":"张伟","仓库":"成品仓","制单人":"admin","审核人":"系统管理员","审核日期":"2026-08-16"}',
'{"detail":[{"产品名称":"铝棒 Φ80","仓库":"成品仓","规格型号":"Φ80×3000","计量单位":"件","实收数量":200,"单价":15.5,"金额":3100}]}',
'已审核', 'admin', NOW(), 'admin', NOW()
WHERE NOT EXISTS (SELECT 1 FROM form_data WHERE panel_code='FINISH_IN' AND form_no='FI-2026-08-0001');

INSERT INTO form_data (panel_code, form_no, data, detail_data, status, create_by, create_time, audit_by, audit_time)
SELECT 'FINISH_IN', 'FI-2026-08-0002',
'{"编号":"FI-2026-08-0002","单据日期":"2026-08-19","单据编号":"FI-2026-08-0002","单据状态":"草稿","业务类型":"产成品入库","入库类别":"自制加工入库","生产车间":"轧制车间","加工单号":"MO-2026-08-0008","经手人":"李娜","仓库":"成品仓","制单人":"admin"}',
'{"detail":[{"产品名称":"铝板 6061","仓库":"成品仓","规格型号":"1500×3000×2","计量单位":"件","实收数量":500,"单价":12.8,"金额":6400}]}',
'草稿', 'admin', NOW(), NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM form_data WHERE panel_code='FINISH_IN' AND form_no='FI-2026-08-0002');

INSERT INTO form_data (panel_code, form_no, data, detail_data, status, create_by, create_time, audit_by, audit_time)
SELECT 'SALE_OUT', 'SO2026-08-0001',
'{"编号":"SO2026-08-0001","单据日期":"2026-08-16","单据编号":"SO2026-08-0001","单据状态":"已审核","业务类型":"销售出库","出库类别":"销售出库","客户":"华东铝业","客户编码":"KH001","经手人":"张伟","仓库":"成品仓","制单人":"admin","审核人":"系统管理员","审核日期":"2026-08-16"}',
'{"detail":[{"仓库":"成品仓","存货名称":"铝棒 Φ80","存货编码":"CP001","规格型号":"Φ80×3000","计量单位":"件","数量":200,"成本价":10,"售价":15.5,"销售金额":3100,"含税销售金额":3503}]}',
'已审核', 'admin', NOW(), 'admin', NOW()
WHERE NOT EXISTS (SELECT 1 FROM form_data WHERE panel_code='SALE_OUT' AND form_no='SO2026-08-0001');

INSERT INTO form_data (panel_code, form_no, data, detail_data, status, create_by, create_time, audit_by, audit_time)
SELECT 'MATERIAL_OUT', 'MOUT-2026-08-0001',
'{"编号":"MOUT-2026-08-0001","单据日期":"2026-08-15","单据编号":"MOUT-2026-08-0001","单据状态":"已审核","业务类型":"材料出库","出库类别":"直接领料","生产车间":"熔铸车间","领用人":"张伟","仓库":"原料仓","来源单据":"生产加工单","来源单号":"MO-2026-08-0009","制单人":"admin","审核人":"系统管理员","审核日期":"2026-08-15"}',
'{"detail":[{"加工单号":"MO-2026-08-0009","材料名称":"6061铝锭","规格型号":"A00","计量单位":"kg","数量":315,"单价":12.8,"金额":4032}]}',
'已审核', 'admin', NOW(), 'admin', NOW()
WHERE NOT EXISTS (SELECT 1 FROM form_data WHERE panel_code='MATERIAL_OUT' AND form_no='MOUT-2026-08-0001');

INSERT INTO form_data (panel_code, form_no, data, detail_data, status, create_by, create_time, audit_by, audit_time)
SELECT 'PURCHASE_IN', 'PIN-2026-08-0001',
'{"编号":"PIN-2026-08-0001","单据日期":"2026-08-15","单据编号":"PIN-2026-08-0001","单据状态":"已审核","业务类型":"采购入库","入库类别":"采购入库","供应商":"华东铝业","供应商编码":"KH001","经手人":"张伟","仓库":"原料仓","来源单号":"PO-2026-08-0001","制单人":"admin","审核人":"系统管理员","审核日期":"2026-08-15"}',
'{"detail":[{"仓库":"原料仓","存货名称":"铝棒 Φ80","存货编码":"CP001","规格型号":"Φ80×3000","计量单位":"件","实收数量":200,"单价":15.5,"金额":3100},{"仓库":"原料仓","存货名称":"6061铝锭","存货编码":"CL002","规格型号":"A00","计量单位":"kg","实收数量":500,"单价":12.8,"金额":6400}]}',
'已审核', 'admin', NOW(), 'admin', NOW()
WHERE NOT EXISTS (SELECT 1 FROM form_data WHERE panel_code='PURCHASE_IN' AND form_no='PIN-2026-08-0001');

INSERT INTO form_data (panel_code, form_no, data, detail_data, status, create_by, create_time, audit_by, audit_time)
SELECT 'OTHER_IN', 'OI-2026-08-0001',
'{"编号":"OI-2026-08-0001","单据日期":"2026-08-17","单据编号":"OI-2026-08-0001","单据状态":"已审核","业务类型":"其他入库","入库类别":"盘盈入库","经手人":"王芳","仓库":"辅料仓","制单人":"admin","审核人":"系统管理员","审核日期":"2026-08-17"}',
'{"detail":[{"仓库":"辅料仓","存货名称":"切削液","规格型号":"20L/桶","计量单位":"升","数量":10,"单价":45,"金额":450}]}',
'已审核', 'admin', NOW(), 'admin', NOW()
WHERE NOT EXISTS (SELECT 1 FROM form_data WHERE panel_code='OTHER_IN' AND form_no='OI-2026-08-0001');

INSERT INTO form_data (panel_code, form_no, data, detail_data, status, create_by, create_time, audit_by, audit_time)
SELECT 'OTHER_OUT', 'OO-2026-08-0001',
'{"编号":"OO-2026-08-0001","单据日期":"2026-08-18","单据编号":"OO-2026-08-0001","单据状态":"已审核","业务类型":"其他出库","出库类别":"调整出库","经手人":"陈强","仓库":"不良品仓","制单人":"admin","审核人":"系统管理员","审核日期":"2026-08-18"}',
'{"detail":[{"仓库":"不良品仓","存货名称":"铝棒 Φ80","规格型号":"Φ80×3000","计量单位":"件","数量":5,"单价":10,"金额":50}]}',
'已审核', 'admin', NOW(), 'admin', NOW()
WHERE NOT EXISTS (SELECT 1 FROM form_data WHERE panel_code='OTHER_OUT' AND form_no='OO-2026-08-0001');