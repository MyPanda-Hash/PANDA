-- QC menu seed v3
INSERT INTO sys_menu (id, parent_id, code, title, path, icon, sort) VALUES
('210', '200', 'smartShop', '智慧车间', NULL, 'MagicStick', '2'),
('211', '210', 'shopDoc', '单据', NULL, NULL, '1'),
('212', '211', 'procReport', '工序汇报单', '/prod/shop/procReport', 'EditPen', '1'),
('213', '211', 'reworkReport', '返修工序汇报单', '/prod/shop/reworkReport', 'EditPen', '2'),
('214', '211', 'reworkDesk', '返修工作台', '/prod/shop/reworkDesk', 'Tools', '3'),
('215', '210', 'shopReport', '报表', NULL, NULL, '2'),
('216', '0', 'qc', '质量管理', NULL, 'Aim', '25'),
('217', '216', 'qcDoc', '单据', NULL, 'Document', '1'),
('218', '217', 'arrivalIn', '到货单', '/qc/qcDoc/arrivalIn', 'Van', '1'),
('219', '217', 'finishInspect', '成品报检单', '/qc/qcDoc/finishInspect', 'Checked', '2'),
('220', '217', 'inspection', '来料/成品检验单', '/qc/qcDoc/inspection', 'View', '3'),
('221', '202', 'dispatch', '工序派工单', '/prod/manufacture/dispatch', 'AlarmClock', '3')
ON DUPLICATE KEY UPDATE parent_id = VALUES(parent_id), code = VALUES(code), title = VALUES(title), path = VALUES(path), icon = VALUES(icon), sort = VALUES(sort);
