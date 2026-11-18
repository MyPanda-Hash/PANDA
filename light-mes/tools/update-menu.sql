-- 新面板菜单同步（2026-08-20）
INSERT INTO sys_menu (id, parent_id, code, title, path, icon, sort) VALUES (164, 102, 'saleInv', '销货单', '/scm/sales/saleInv', 'Tickets', 2) ON DUPLICATE KEY UPDATE title = VALUES(title), path = VALUES(path);
INSERT INTO sys_menu (id, parent_id, code, title, path, icon, sort) VALUES (165, 121, 'materialReq', '领料申请单', '/scm/inv/materialReq', 'Upload', 7) ON DUPLICATE KEY UPDATE title = VALUES(title), path = VALUES(path);
INSERT INTO sys_menu (id, parent_id, code, title, path, icon, sort) VALUES (160, 100, 'purchase', '采购管理', NULL, 'ShoppingCart', 3) ON DUPLICATE KEY UPDATE title = VALUES(title), path = VALUES(path);
INSERT INTO sys_menu (id, parent_id, code, title, path, icon, sort) VALUES (161, 160, 'purchaseDoc', '单据', NULL, NULL, 1) ON DUPLICATE KEY UPDATE title = VALUES(title), path = VALUES(path);
INSERT INTO sys_menu (id, parent_id, code, title, path, icon, sort) VALUES (162, 161, 'puOrder', '采购订单', '/scm/purchase/puOrder', 'Tickets', 1) ON DUPLICATE KEY UPDATE title = VALUES(title), path = VALUES(path);
INSERT INTO sys_menu (id, parent_id, code, title, path, icon, sort) VALUES (163, 161, 'puIn', '进货单', '/scm/purchase/puIn', 'Tickets', 2) ON DUPLICATE KEY UPDATE title = VALUES(title), path = VALUES(path);
INSERT INTO sys_menu (id, parent_id, code, title, path, icon, sort) VALUES (170, 100, 'distribution', '配货管理', NULL, 'Box', 4) ON DUPLICATE KEY UPDATE title = VALUES(title), path = VALUES(path);
INSERT INTO sys_menu (id, parent_id, code, title, path, icon, sort) VALUES (171, 170, 'distDoc', '单据', NULL, NULL, 1) ON DUPLICATE KEY UPDATE title = VALUES(title), path = VALUES(path);
INSERT INTO sys_menu (id, parent_id, code, title, path, icon, sort) VALUES (172, 171, 'pickOrder', '配货单', '/scm/dist/pickOrder', 'Tickets', 1) ON DUPLICATE KEY UPDATE title = VALUES(title), path = VALUES(path);
