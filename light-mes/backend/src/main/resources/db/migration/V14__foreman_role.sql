-- V14：新增「车间主任」角色，把生产单据的审批从管理员下放到中层
--
-- 现实问题（2026-11-17 实测）：
--   sys_role_panel 里**没有任何角色**拥有 MANU_ORDER / DISPATCH / PROCESS_REPORT 的 audit 权限，
--   于是生产加工单只能由 admin（老板）审。老板成了唯一审批人，中层没事可做，
--   审批也失去了「谁主管谁负责」的意义。
--
-- 本脚本做三件事：
--   ① 建 FOREMAN（车间主任）角色；
--   ② 把生产执行类面板的审批权交给它（看/录/改/审），老板仍是超级管理员可代审；
--   ③ 给 WORKER（车间调度/一线）补执行权限（看/录/改，不含审批），否则一线进系统是空屏。

INSERT INTO sys_role (role_code, role_name, is_admin, remark)
VALUES ('FOREMAN', '车间主任', 0, '中层：负责生产加工单、派工、工序汇报的审批')
    ON CONFLICT DO NOTHING;

-- ② 车间主任：生产链路上的审批权
INSERT INTO sys_role_panel (role_id, panel_code, perms, can_approve)
SELECT r.id, p.panel_code, p.perms, p.can_approve
FROM sys_role r
CROSS JOIN (VALUES
    ('MANU_ORDER',     'view,query,add,edit,print,audit', 1),
    ('DISPATCH',       'view,query,add,edit,print,audit', 1),
    ('PROCESS_REPORT', 'view,query,add,edit,print,audit', 1),
    ('MATERIAL_REQ',   'view,query,add,edit,print,audit', 1),
    ('MATERIAL_OUT',   'view,query,add,edit,print,audit', 1),
    ('FINISH_IN',      'view,query,add,edit,print,audit', 1),
    ('FIRST_INSPECT',  'view,query,add,edit,print,audit', 1),
    ('BOM',            'view,query',                      0),
    ('ROUTE',          'view,query',                      0),
    ('STOCK_STATUS',   'view,query',                      0)
) AS p(panel_code, perms, can_approve)
WHERE r.role_code = 'FOREMAN'
  AND NOT EXISTS (SELECT 1 FROM sys_role_panel x WHERE x.role_id = r.id AND x.panel_code = p.panel_code);

-- ③ 车间调度 / 一线：能看能录，不能审
INSERT INTO sys_role_panel (role_id, panel_code, perms, can_approve)
SELECT r.id, p.panel_code, p.perms, 0
FROM sys_role r
CROSS JOIN (VALUES
    ('MANU_ORDER',     'view,query'),
    ('DISPATCH',       'view,query,add,edit,print'),
    ('PROCESS_REPORT', 'view,query,add,edit,print'),
    ('MATERIAL_REQ',   'view,query,add'),
    ('MATERIAL_OUT',   'view,query'),
    ('FINISH_IN',      'view,query'),
    ('STOCK_STATUS',   'view,query')
) AS p(panel_code, perms)
WHERE r.role_code = 'WORKER'
  AND NOT EXISTS (SELECT 1 FROM sys_role_panel x WHERE x.role_id = r.id AND x.panel_code = p.panel_code);

-- ④ 把现有的「赵车间」从车间调度改为车间主任：
--    当前库里没有任何车间主任账号，先让这个人承接审批，避免新角色建了没人用。
UPDATE sys_user
   SET role_id = (SELECT id FROM sys_role WHERE role_code = 'FOREMAN')
 WHERE user_name = 'worker'
   AND role_id = (SELECT id FROM sys_role WHERE role_code = 'WORKER');
