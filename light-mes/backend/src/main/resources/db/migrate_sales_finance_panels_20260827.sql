USE light_mes;

-- SQL 模式下补齐销售财务链面板。可重复执行，不写入任何业务单据。
-- 应用启动时 SalesFinancePanelRegistry 会按同一版本再次校准完整配置。

SET @approval_groups = JSON_ARRAY(
  JSON_OBJECT('name', '新增', 'actions', JSON_ARRAY('新增')),
  JSON_OBJECT('name', '保存', 'actions', JSON_ARRAY('保存', '保存新增', '保存为草稿')),
  JSON_OBJECT('name', '删除', 'actions', JSON_ARRAY('删除', '删除单据')),
  JSON_OBJECT('name', '审批', 'actions', JSON_ARRAY('提交审批', '审批通过', '审批驳回', '审批情况', '弃审')),
  JSON_OBJECT('name', '更多', 'actions', JSON_ARRAY('刷新'))
);

SET @panel_state = JSON_OBJECT(
  'dataName', '单据状态',
  'dataType', 'STRING',
  'defaultOptions', JSON_ARRAY('草稿', '审批中', '已审核', '已中止')
);

SET @sale_invoice_head = JSON_ARRAY(
  JSON_OBJECT('dataName', '单据日期', 'dataType', '日期', 'isRequired', TRUE, 'defaultValue', ''),
  JSON_OBJECT('dataName', '单据编号', 'dataType', '文本', 'isRequired', TRUE, 'defaultValue', '', 'autoCode', TRUE),
  JSON_OBJECT('dataName', '业务类型', 'dataType', '下拉框', 'isRequired', TRUE, 'defaultValue', '销售发票', 'options', JSON_ARRAY('销售发票', '销售发票(退货)')),
  JSON_OBJECT('dataName', '客户', 'dataType', '参照', 'isRequired', TRUE, 'defaultValue', '', 'refPanel', 'PARTNER', 'refField', '往来单位名称', 'displayField', '往来单位名称', 'filter', JSON_OBJECT('停用', FALSE, '性质', JSON_ARRAY('客户', '两者'))),
  JSON_OBJECT('dataName', '客户编码', 'dataType', '文本', 'isRequired', FALSE, 'defaultValue', ''),
  JSON_OBJECT('dataName', '结算客户', 'dataType', '参照', 'isRequired', FALSE, 'defaultValue', '', 'refPanel', 'PARTNER', 'refField', '往来单位名称', 'displayField', '往来单位名称'),
  JSON_OBJECT('dataName', '部门', 'dataType', '参照', 'isRequired', FALSE, 'defaultValue', '', 'refPanel', 'DEPT', 'refField', '部门名称', 'displayField', '部门名称'),
  JSON_OBJECT('dataName', '业务员', 'dataType', '参照', 'isRequired', FALSE, 'defaultValue', '', 'refPanel', 'EMP', 'refField', '员工名称', 'displayField', '员工名称'),
  JSON_OBJECT('dataName', '开票类型', 'dataType', '下拉框', 'isRequired', TRUE, 'defaultValue', '增值税专用发票', 'options', JSON_ARRAY('增值税专用发票', '增值税普通发票')),
  JSON_OBJECT('dataName', '销货单号', 'dataType', '文本', 'isRequired', FALSE, 'defaultValue', ''),
  JSON_OBJECT('dataName', '来源单据', 'dataType', '文本', 'isRequired', FALSE, 'defaultValue', ''),
  JSON_OBJECT('dataName', '来源单号', 'dataType', '文本', 'isRequired', FALSE, 'defaultValue', ''),
  JSON_OBJECT('dataName', '价税合计', 'dataType', '小数', 'isRequired', FALSE, 'defaultValue', 0),
  JSON_OBJECT('dataName', '备注', 'dataType', '文本', 'isRequired', FALSE, 'defaultValue', '')
);

SET @sale_invoice_items = JSON_ARRAY(
  JSON_OBJECT('dataName', '存货编码', 'dataType', '参照', 'isRequired', FALSE, 'defaultValue', '', 'refPanel', 'INV', 'refField', '存货编码', 'displayField', '存货编码'),
  JSON_OBJECT('dataName', '存货名称', 'dataType', '参照', 'isRequired', TRUE, 'defaultValue', '', 'refPanel', 'INV', 'refField', '存货名称', 'displayField', '存货名称'),
  JSON_OBJECT('dataName', '规格型号', 'dataType', '文本', 'isRequired', FALSE, 'defaultValue', ''),
  JSON_OBJECT('dataName', '计量单位', 'dataType', '下拉框', 'isRequired', TRUE, 'defaultValue', '件', 'options', JSON_ARRAY('件', 'kg', '套', '升')),
  JSON_OBJECT('dataName', '数量', 'dataType', '小数', 'isRequired', TRUE, 'defaultValue', 0),
  JSON_OBJECT('dataName', '无税单价', 'dataType', '小数', 'isRequired', TRUE, 'defaultValue', 0),
  JSON_OBJECT('dataName', '税率%', 'dataType', '小数', 'isRequired', FALSE, 'defaultValue', 13),
  JSON_OBJECT('dataName', '税额', 'dataType', '小数', 'isRequired', FALSE, 'computed', TRUE),
  JSON_OBJECT('dataName', '价税合计', 'dataType', '小数', 'isRequired', FALSE, 'computed', TRUE)
);

SET @sale_invoice_select = JSON_OBJECT(
  'source', 'SALE_INV', 'title', '选销货单', 'detailKey', 'items',
  'sourceQuantityField', '数量', 'targetQuantityField', '数量',
  'columns', JSON_ARRAY('单据编号', '单据日期', '客户', '存货名称', '数量', '单价', '含税单价'),
  'headerMap', JSON_ARRAY(
    JSON_OBJECT('from', '单据编号', 'to', '销货单号'),
    JSON_OBJECT('from', '单据编号', 'to', '来源单号'),
    JSON_OBJECT('to', '来源单据', 'fixed', 'SALE_INV'),
    JSON_OBJECT('from', '客户', 'to', '客户'),
    JSON_OBJECT('from', '客户编码', 'to', '客户编码'),
    JSON_OBJECT('from', '结算客户', 'to', '结算客户'),
    JSON_OBJECT('from', '部门', 'to', '部门'),
    JSON_OBJECT('from', '经手人', 'to', '业务员')
  ),
  'detailMap', JSON_ARRAY(
    JSON_OBJECT('from', '存货编码', 'to', '存货编码'),
    JSON_OBJECT('from', '存货名称', 'to', '存货名称'),
    JSON_OBJECT('from', '规格型号', 'to', '规格型号'),
    JSON_OBJECT('from', '销售单位', 'to', '计量单位'),
    JSON_OBJECT('from', '数量', 'to', '数量'),
    JSON_OBJECT('from', '单价', 'to', '无税单价'),
    JSON_OBJECT('from', '税率%', 'to', '税率%')
  )
);

SET @sale_invoice_groups = JSON_ARRAY_INSERT(
  JSON_EXTRACT(@approval_groups, '$'), '$[1]',
  JSON_OBJECT('name', '选单', 'actions', JSON_ARRAY('选销货单'))
);

SET @sale_invoice_config = JSON_OBJECT(
  'metadata', JSON_OBJECT(
    'panelCode', 'SALE_INVOICE', 'panelName', '销售发票', 'panelCategory', '单据',
    'autoCodeField', '单据编号', 'panelState', JSON_EXTRACT(@panel_state, '$'),
    'panelPageDto', JSON_OBJECT(
      'tablePages', JSON_ARRAY(JSON_OBJECT(
        'tableName', '销售发票列表',
        'queryFields', JSON_ARRAY(
          JSON_OBJECT('dataName', '单据日期', 'dataType', '日期'),
          JSON_OBJECT('dataName', '单据编号', 'dataType', '文本'),
          JSON_OBJECT('dataName', '客户', 'dataType', '参照', 'refPanel', 'PARTNER', 'refField', '往来单位名称')
        ),
        'gridTabs', JSON_ARRAY(JSON_OBJECT('label', '发票明细', 'rowSource', 'detail', 'columns', JSON_ARRAY('存货编码', '存货名称', '规格型号', '计量单位', '数量', '无税单价', '税率%', '税额', '价税合计'))),
        'topBarBtn', JSON_ARRAY(), 'rowOperationBarBtn', JSON_ARRAY(), 'events', JSON_ARRAY()
      )),
      'formPages', JSON_ARRAY(JSON_OBJECT('formName', '销售发票', 'fieldNames', '单据日期,单据编号,业务类型,客户,客户编码,结算客户,部门,业务员,开票类型,销货单号,来源单据,来源单号,价税合计,备注', 'bottomOperationBarBtn', JSON_ARRAY(), 'events', JSON_ARRAY()))
    ),
    'buttonGroups', JSON_EXTRACT(@sale_invoice_groups, '$'), 'panelButtons', JSON_ARRAY(), 'version', 'sales-finance-2.0'
  ),
  'dataSchema', JSON_OBJECT('type', 'object', 'fields', JSON_EXTRACT(@sale_invoice_head, '$')),
  'detail', JSON_OBJECT('tabs', JSON_ARRAY(JSON_OBJECT(
    'key', 'items', 'label', '发票明细', 'isRequired', TRUE,
    'fields', JSON_EXTRACT(@sale_invoice_items, '$'),
    'summaryItems', JSON_ARRAY(JSON_OBJECT('label', '数量合计', 'field', '数量'), JSON_OBJECT('label', '税额合计', 'field', '税额'), JSON_OBJECT('label', '价税合计', 'field', '价税合计')),
    'calc', JSON_ARRAY(JSON_OBJECT('target', '税额', 'formula', '数量 * 无税单价 * 税率% / 100', 'round', 2), JSON_OBJECT('target', '价税合计', 'formula', '数量 * 无税单价 * (1 + 税率% / 100)', 'round', 2))
  ))),
  'selectConfig', JSON_EXTRACT(@sale_invoice_select, '$'),
  'selectConfigs', JSON_OBJECT('选销货单', JSON_EXTRACT(@sale_invoice_select, '$'))
);

SET @expense_head = JSON_ARRAY(
  JSON_OBJECT('dataName', '单据日期', 'dataType', '日期', 'isRequired', TRUE, 'defaultValue', ''),
  JSON_OBJECT('dataName', '单据编号', 'dataType', '文本', 'isRequired', TRUE, 'defaultValue', '', 'autoCode', TRUE),
  JSON_OBJECT('dataName', '业务类型', 'dataType', '下拉框', 'isRequired', TRUE, 'defaultValue', '费用单', 'options', JSON_ARRAY('费用单')),
  JSON_OBJECT('dataName', '费用类型', 'dataType', '下拉框', 'isRequired', TRUE, 'defaultValue', '销售费用', 'options', JSON_ARRAY('销售费用', '采购费用', '管理费用', '其他费用')),
  JSON_OBJECT('dataName', '往来单位', 'dataType', '参照', 'isRequired', FALSE, 'defaultValue', '', 'refPanel', 'PARTNER', 'refField', '往来单位名称', 'displayField', '往来单位名称'),
  JSON_OBJECT('dataName', '部门', 'dataType', '参照', 'isRequired', FALSE, 'defaultValue', '', 'refPanel', 'DEPT', 'refField', '部门名称', 'displayField', '部门名称'),
  JSON_OBJECT('dataName', '经手人', 'dataType', '参照', 'isRequired', FALSE, 'defaultValue', '', 'refPanel', 'EMP', 'refField', '员工名称', 'displayField', '员工名称'),
  JSON_OBJECT('dataName', '项目', 'dataType', '参照', 'isRequired', FALSE, 'defaultValue', '', 'refPanel', 'PROJ', 'refField', '项目名称', 'displayField', '项目名称'),
  JSON_OBJECT('dataName', '结算方式', 'dataType', '下拉框', 'isRequired', TRUE, 'defaultValue', '转账', 'options', JSON_ARRAY('转账', '现金', '票据', '其他')),
  JSON_OBJECT('dataName', '费用合计', 'dataType', '小数', 'isRequired', FALSE, 'defaultValue', 0),
  JSON_OBJECT('dataName', '备注', 'dataType', '文本', 'isRequired', FALSE, 'defaultValue', '')
);

SET @expense_items = JSON_ARRAY(
  JSON_OBJECT('dataName', '费用项目', 'dataType', '下拉框', 'isRequired', TRUE, 'defaultValue', '运费', 'options', JSON_ARRAY('运费', '装卸费', '包装费', '广告费', '差旅费', '其他')),
  JSON_OBJECT('dataName', '金额', 'dataType', '小数', 'isRequired', TRUE, 'defaultValue', 0),
  JSON_OBJECT('dataName', '税率%', 'dataType', '小数', 'isRequired', FALSE, 'defaultValue', 0),
  JSON_OBJECT('dataName', '税额', 'dataType', '小数', 'isRequired', FALSE, 'computed', TRUE),
  JSON_OBJECT('dataName', '含税金额', 'dataType', '小数', 'isRequired', FALSE, 'computed', TRUE),
  JSON_OBJECT('dataName', '费用承担部门', 'dataType', '参照', 'isRequired', FALSE, 'defaultValue', '', 'refPanel', 'DEPT', 'refField', '部门名称', 'displayField', '部门名称'),
  JSON_OBJECT('dataName', '备注', 'dataType', '文本', 'isRequired', FALSE, 'defaultValue', '')
);

SET @expense_groups = JSON_ARRAY_INSERT(
  JSON_EXTRACT(@approval_groups, '$'), '$[4]',
  JSON_OBJECT('name', '生单', 'actions', JSON_ARRAY('生成销售费用分摊单', '生成采购费用分摊单'))
);

SET @expense_config = JSON_OBJECT(
  'metadata', JSON_OBJECT(
    'panelCode', 'EXPENSE', 'panelName', '费用单', 'panelCategory', '单据',
    'autoCodeField', '单据编号', 'panelState', JSON_EXTRACT(@panel_state, '$'),
    'panelPageDto', JSON_OBJECT(
      'tablePages', JSON_ARRAY(JSON_OBJECT(
        'tableName', '费用单列表',
        'queryFields', JSON_ARRAY(JSON_OBJECT('dataName', '单据日期', 'dataType', '日期'), JSON_OBJECT('dataName', '单据编号', 'dataType', '文本'), JSON_OBJECT('dataName', '费用类型', 'dataType', '下拉框')),
        'gridTabs', JSON_ARRAY(JSON_OBJECT('label', '费用明细', 'rowSource', 'detail', 'columns', JSON_ARRAY('费用项目', '金额', '税率%', '税额', '含税金额', '费用承担部门', '备注'))),
        'topBarBtn', JSON_ARRAY(), 'rowOperationBarBtn', JSON_ARRAY(), 'events', JSON_ARRAY()
      )),
      'formPages', JSON_ARRAY(JSON_OBJECT('formName', '费用单', 'fieldNames', '单据日期,单据编号,业务类型,费用类型,往来单位,部门,经手人,项目,结算方式,费用合计,备注', 'bottomOperationBarBtn', JSON_ARRAY(), 'events', JSON_ARRAY()))
    ),
    'buttonGroups', JSON_EXTRACT(@expense_groups, '$'), 'panelButtons', JSON_ARRAY(), 'version', 'sales-finance-2.0'
  ),
  'dataSchema', JSON_OBJECT('type', 'object', 'fields', JSON_EXTRACT(@expense_head, '$')),
  'detail', JSON_OBJECT('tabs', JSON_ARRAY(JSON_OBJECT(
    'key', 'items', 'label', '费用明细', 'isRequired', TRUE,
    'fields', JSON_EXTRACT(@expense_items, '$'),
    'summaryItems', JSON_ARRAY(JSON_OBJECT('label', '金额合计', 'field', '金额'), JSON_OBJECT('label', '税额合计', 'field', '税额'), JSON_OBJECT('label', '含税金额合计', 'field', '含税金额')),
    'calc', JSON_ARRAY(JSON_OBJECT('target', '税额', 'formula', '金额 * 税率% / 100', 'round', 2), JSON_OBJECT('target', '含税金额', 'formula', '金额 * (1 + 税率% / 100)', 'round', 2))
  )))
);

SET @sale_alloc_head = JSON_ARRAY(
  JSON_OBJECT('dataName', '单据日期', 'dataType', '日期', 'isRequired', TRUE, 'defaultValue', ''),
  JSON_OBJECT('dataName', '单据编号', 'dataType', '文本', 'isRequired', TRUE, 'defaultValue', '', 'autoCode', TRUE),
  JSON_OBJECT('dataName', '业务类型', 'dataType', '下拉框', 'isRequired', TRUE, 'defaultValue', '销售费用分摊', 'options', JSON_ARRAY('销售费用分摊')),
  JSON_OBJECT('dataName', '费用类型', 'dataType', '下拉框', 'isRequired', TRUE, 'defaultValue', '销售费用', 'options', JSON_ARRAY('销售费用', '其他费用')),
  JSON_OBJECT('dataName', '部门', 'dataType', '参照', 'isRequired', FALSE, 'defaultValue', '', 'refPanel', 'DEPT', 'refField', '部门名称', 'displayField', '部门名称'),
  JSON_OBJECT('dataName', '经手人', 'dataType', '参照', 'isRequired', FALSE, 'defaultValue', '', 'refPanel', 'EMP', 'refField', '员工名称', 'displayField', '员工名称'),
  JSON_OBJECT('dataName', '项目', 'dataType', '参照', 'isRequired', FALSE, 'defaultValue', '', 'refPanel', 'PROJ', 'refField', '项目名称', 'displayField', '项目名称'),
  JSON_OBJECT('dataName', '来源单据', 'dataType', '文本', 'isRequired', FALSE, 'defaultValue', ''),
  JSON_OBJECT('dataName', '来源单号', 'dataType', '文本', 'isRequired', FALSE, 'defaultValue', ''),
  JSON_OBJECT('dataName', '分摊合计', 'dataType', '小数', 'isRequired', FALSE, 'defaultValue', 0),
  JSON_OBJECT('dataName', '备注', 'dataType', '文本', 'isRequired', FALSE, 'defaultValue', '')
);

SET @sale_alloc_items = JSON_ARRAY(
  JSON_OBJECT('dataName', '费用单号', 'dataType', '文本', 'isRequired', FALSE, 'defaultValue', ''),
  JSON_OBJECT('dataName', '费用项目', 'dataType', '下拉框', 'isRequired', TRUE, 'defaultValue', '运费', 'options', JSON_ARRAY('运费', '装卸费', '包装费', '广告费', '差旅费', '其他')),
  JSON_OBJECT('dataName', '分摊对象', 'dataType', '下拉框', 'isRequired', TRUE, 'defaultValue', '客户', 'options', JSON_ARRAY('客户', '销售发票')),
  JSON_OBJECT('dataName', '客户', 'dataType', '参照', 'isRequired', FALSE, 'defaultValue', '', 'refPanel', 'PARTNER', 'refField', '往来单位名称', 'displayField', '往来单位名称'),
  JSON_OBJECT('dataName', '销售发票号', 'dataType', '参照', 'isRequired', FALSE, 'defaultValue', '', 'refPanel', 'SALE_INVOICE', 'refField', '单据编号', 'displayField', '单据编号'),
  JSON_OBJECT('dataName', '分摊金额', 'dataType', '小数', 'isRequired', TRUE, 'defaultValue', 0),
  JSON_OBJECT('dataName', '备注', 'dataType', '文本', 'isRequired', FALSE, 'defaultValue', '')
);

SET @sale_alloc_select = JSON_OBJECT(
  'source', 'EXPENSE', 'title', '选费用单', 'detailKey', 'items',
  'sourceQuantityField', '金额', 'targetQuantityField', '分摊金额',
  'condition', JSON_OBJECT('费用类型', '销售费用'),
  'columns', JSON_ARRAY('单据编号', '单据日期', '费用类型', '往来单位', '费用项目', '金额'),
  'headerMap', JSON_ARRAY(
    JSON_OBJECT('from', '单据编号', 'to', '来源单号'),
    JSON_OBJECT('to', '来源单据', 'fixed', 'EXPENSE'),
    JSON_OBJECT('from', '费用类型', 'to', '费用类型'),
    JSON_OBJECT('from', '部门', 'to', '部门'),
    JSON_OBJECT('from', '经手人', 'to', '经手人'),
    JSON_OBJECT('from', '项目', 'to', '项目')
  ),
  'detailMap', JSON_ARRAY(
    JSON_OBJECT('from', '单据编号', 'to', '费用单号'),
    JSON_OBJECT('from', '费用项目', 'to', '费用项目'),
    JSON_OBJECT('from', '往来单位', 'to', '客户'),
    JSON_OBJECT('from', '金额', 'to', '分摊金额'),
    JSON_OBJECT('from', '备注', 'to', '备注')
  )
);

SET @sale_alloc_groups = JSON_ARRAY_INSERT(
  JSON_EXTRACT(@approval_groups, '$'), '$[1]',
  JSON_OBJECT('name', '选单', 'actions', JSON_ARRAY('选费用单'))
);

SET @sale_alloc_config = JSON_OBJECT(
  'metadata', JSON_OBJECT(
    'panelCode', 'SALE_COST_ALLOC', 'panelName', '销售费用分摊单', 'panelCategory', '单据',
    'autoCodeField', '单据编号', 'panelState', JSON_EXTRACT(@panel_state, '$'),
    'panelPageDto', JSON_OBJECT(
      'tablePages', JSON_ARRAY(JSON_OBJECT(
        'tableName', '销售费用分摊单列表',
        'queryFields', JSON_ARRAY(JSON_OBJECT('dataName', '单据日期', 'dataType', '日期'), JSON_OBJECT('dataName', '单据编号', 'dataType', '文本'), JSON_OBJECT('dataName', '费用类型', 'dataType', '下拉框')),
        'gridTabs', JSON_ARRAY(JSON_OBJECT('label', '分摊明细', 'rowSource', 'detail', 'columns', JSON_ARRAY('费用单号', '费用项目', '分摊对象', '客户', '销售发票号', '分摊金额', '备注'))),
        'topBarBtn', JSON_ARRAY(), 'rowOperationBarBtn', JSON_ARRAY(), 'events', JSON_ARRAY()
      )),
      'formPages', JSON_ARRAY(JSON_OBJECT('formName', '销售费用分摊单', 'fieldNames', '单据日期,单据编号,业务类型,费用类型,部门,经手人,项目,来源单据,来源单号,分摊合计,备注', 'bottomOperationBarBtn', JSON_ARRAY(), 'events', JSON_ARRAY()))
    ),
    'buttonGroups', JSON_EXTRACT(@sale_alloc_groups, '$'), 'panelButtons', JSON_ARRAY(), 'version', 'sales-finance-2.0'
  ),
  'dataSchema', JSON_OBJECT('type', 'object', 'fields', JSON_EXTRACT(@sale_alloc_head, '$')),
  'detail', JSON_OBJECT('tabs', JSON_ARRAY(JSON_OBJECT(
    'key', 'items', 'label', '分摊明细', 'isRequired', TRUE,
    'fields', JSON_EXTRACT(@sale_alloc_items, '$'),
    'summaryItems', JSON_ARRAY(JSON_OBJECT('label', '分摊金额合计', 'field', '分摊金额'))
  ))),
  'selectConfig', JSON_EXTRACT(@sale_alloc_select, '$'),
  'selectConfigs', JSON_OBJECT('选费用单', JSON_EXTRACT(@sale_alloc_select, '$'))
);

INSERT INTO panel_config (panel_code, panel_name, category, config, version, create_time, update_time)
VALUES
  ('SALE_INVOICE', '销售发票', '单据', @sale_invoice_config, 'sales-finance-2.0', NOW(), NOW()),
  ('EXPENSE', '费用单', '单据', @expense_config, 'sales-finance-2.0', NOW(), NOW()),
  ('SALE_COST_ALLOC', '销售费用分摊单', '单据', @sale_alloc_config, 'sales-finance-2.0', NOW(), NOW())
ON DUPLICATE KEY UPDATE
  panel_name = VALUES(panel_name),
  category = VALUES(category),
  config = VALUES(config),
  version = VALUES(version),
  update_time = VALUES(update_time);
