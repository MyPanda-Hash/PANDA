USE light_mes;

-- 质量管理菜单按 T+ 机械行业 NQM/QM/QT 目录增量迁移。
-- 固定 ID 便于重复执行；只删除本模块的旧简化节点，不影响业务数据和其他菜单。
UPDATE sys_menu
SET parent_id = 0, code = 'qc', title = '质量管理', path = NULL, icon = 'Aim', sort = 25
WHERE id = 216;
DELETE FROM sys_menu WHERE id IN (217, 218, 219, 220);
DELETE FROM sys_menu WHERE id BETWEEN 500 AND 542;

INSERT INTO sys_menu (id, parent_id, code, title, path, icon, sort) VALUES
(500, 216, 'qcManagement',         '质检管理',             NULL,                                       'DocumentChecked', 1),
(501, 500, 'inspectionApply',      '报检单',               NULL,                                       NULL,              1),
(502, 501, 'arrivalIn',            '到货单',               '/panelx/list/ARRIVAL_IN',                  'Van',             1),
(503, 502, 'arrivalStats',         '统计表',               '/panelx/list/ARRIVAL_IN_STATS',            'Histogram',       1),
(504, 502, 'arrivalDetail',        '明细表',               '/panelx/list/ARRIVAL_IN_DETAIL',           'List',            2),
(505, 501, 'finishInspect',        '成品报检单',           '/panelx/list/FINISH_INSPECT',              'Checked',         2),
(506, 505, 'finishInspectStats',   '统计表',               '/panelx/list/FINISH_INSPECT_STATS',        'Histogram',       1),
(507, 505, 'finishInspectDetail',  '明细表',               '/panelx/list/FINISH_INSPECT_DETAIL',       'List',            2),
(508, 501, 'firstInspect',         '首件报检单',           '/panelx/list/FIRST_INSPECT',               'Select',          3),
(509, 508, 'firstInspectStats',    '统计表',               '/panelx/list/FIRST_INSPECT_STATS',         'Histogram',       1),
(510, 508, 'firstInspectDetail',   '明细表',               '/panelx/list/FIRST_INSPECT_DETAIL',        'List',            2),
(511, 501, 'processInspectApply',  '工序报检单',           '/panelx/list/PROCESS_INSPECT_APPLY',       'Finished',        4),
(512, 511, 'processInspectStats',  '统计表',               '/panelx/list/PROCESS_INSPECT_APPLY_STATS', 'Histogram',       1),
(513, 511, 'processInspectDetail', '明细表',               '/panelx/list/PROCESS_INSPECT_APPLY_DETAIL','List',            2),
(514, 501, 'qualityAnalysisSystem','质量统计分析表-系统方案','/panelx/list/QUALITY_STATS_ANALYSIS',     'TrendCharts',      5),
(515, 500, 'inspectionDoc',        '检验单',               NULL,                                       NULL,              2),
(516, 515, 'inspection',           '来料/成品检验单',      '/panelx/list/INSPECTION',                  'View',             1),
(517, 515, 'processInspection',    '生产过程检验单',       '/panelx/list/PROCESS_INSPECTION',          'DataLine',         2),
(518, 500, 'qualityReport',        '报表',                 NULL,                                       NULL,              3),
(519, 518, 'arrivalExec',          '到货单执行表',         '/panelx/list/ARRIVAL_IN_EXEC',             'Operation',        1),
(520, 518, 'finishInspectExec',    '成品报检单执行表',     '/panelx/list/FINISH_INSPECT_EXEC',         'Operation',        2),
(521, 518, 'firstInspectExec',     '首件报检单执行表',     '/panelx/list/FIRST_INSPECT_EXEC',          'Operation',        3),
(522, 518, 'processInspectExec',   '工序报检单执行表',     '/panelx/list/PROCESS_INSPECT_APPLY_EXEC',  'Operation',        4),
(523, 518, 'qualityAnalysis',      '质量统计分析表',       '/panelx/list/QUALITY_STATS_ANALYSIS',      'TrendCharts',      5),
(524, 518, 'inspectionDetail',     '检验单综合明细表',     '/panelx/list/INSPECTION_DETAIL',           'List',             6),
(525, 518, 'inspectionStats',      '检验单综合统计表',     '/panelx/list/INSPECTION_STATS',            'Histogram',        7),
(526, 518, 'qcItemList',           '检验项目列表',         '/panelx/list/QC_ITEM_LIST',                'List',             8),
(527, 518, 'qcItemStats',          '检验项目综合统计表',   '/panelx/list/QC_ITEM_STATS',               'Histogram',        9),
(528, 216, 'qualityTrace',         '质量追溯',             NULL,                                       'Connection',       2),
(529, 528, 'traceSettings',        '基础设置',             NULL,                                       NULL,              1),
(530, 529, 'companyTraceSettings', '企业移动追溯设置',     '/panelx/list/COMPANY_TRACE_SETTINGS',      'Setting',          1),
(531, 529, 'customerTraceSettings','客户移动追溯设置',     '/panelx/list/CUSTOMER_TRACE_SETTINGS',     'Setting',          2),
(532, 529, 'tracePrintTemplate',   '追溯打印模板设置',     '/panelx/list/TRACE_PRINT_TEMPLATE',        'Printer',          3),
(533, 528, 'traceReport',          '报表',                 NULL,                                       NULL,              2),
(534, 533, 'forwardTrace',         '产品正向追溯报表',     '/panelx/list/PRODUCT_FORWARD_TRACE',       'Right',            1),
(535, 533, 'reverseTrace',         '材料反向追溯报表',     '/panelx/list/MATERIAL_REVERSE_TRACE',      'Back',             2),
(540, 302, 'reject',               '不合格原因',           '/panelx/list/REJECT',                      'CircleClose',      7),
(541, 302, 'qcItem',               '检验项目',             '/panelx/list/QC_ITEM',                     'List',             8),
(542, 302, 'qcPlan',               '检验方案',             '/panelx/list/QC_PLAN',                     'DocumentChecked',  9);
