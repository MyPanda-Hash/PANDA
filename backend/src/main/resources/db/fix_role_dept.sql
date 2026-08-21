-- ============================================================
-- light-mes 数据库结构补丁（2026-08-20）
-- 背景：后端代码已引入「角色/部门」权限模型，但 init.sql 未同步，
--       导致登录报 Unknown column 'role_id' / 缺 sys_role 表。
-- 本文件包含：
--   1) sys_user 补 role_id / dept_id 列
--   2) 新建 sys_role / sys_role_panel / sys_dept 三张表
--   3) 写入 ADMIN 管理员角色种子并关联 admin 用户
--   4) 默认部门种子
-- 导入方式：宝塔 → little_mes 库 → 导入本文件（仅需执行一次）
-- ============================================================

-- 1. sys_user 补列（MySQL 8.0 不支持 ADD COLUMN IF NOT EXISTS，只执行一次）
ALTER TABLE sys_user
  ADD COLUMN role_id BIGINT COMMENT '角色ID' AFTER enabled,
  ADD COLUMN dept_id BIGINT COMMENT '部门ID' AFTER role_id;

-- 2. 角色表
CREATE TABLE IF NOT EXISTS sys_role (
  id          BIGINT PRIMARY KEY AUTO_INCREMENT,
  role_code   VARCHAR(50)  NOT NULL UNIQUE COMMENT '角色编码',
  role_name   VARCHAR(50)  NOT NULL COMMENT '角色名称',
  is_admin    TINYINT      DEFAULT 0 COMMENT '1=超级管理员(全部面板+全部审批)',
  remark      VARCHAR(200) COMMENT '备注',
  create_time DATETIME     DEFAULT CURRENT_TIMESTAMP
) COMMENT '角色表';

-- 3. 角色-面板权限表
CREATE TABLE IF NOT EXISTS sys_role_panel (
  id          BIGINT PRIMARY KEY AUTO_INCREMENT,
  role_id     BIGINT      NOT NULL COMMENT '角色ID',
  panel_code  VARCHAR(50) NOT NULL COMMENT '面板编码',
  can_approve TINYINT     DEFAULT 0 COMMENT '1=可审批通过/驳回',
  KEY idx_role_panel (role_id, panel_code)
) COMMENT '角色面板权限表';

-- 4. 部门表
CREATE TABLE IF NOT EXISTS sys_dept (
  id          BIGINT PRIMARY KEY AUTO_INCREMENT,
  parent_id   BIGINT      DEFAULT 0 COMMENT '父部门ID(0为根)',
  dept_name   VARCHAR(50) NOT NULL COMMENT '部门名称',
  sort        INT         DEFAULT 0 COMMENT '排序',
  create_time DATETIME    DEFAULT CURRENT_TIMESTAMP
) COMMENT '部门表';

-- 5. 管理员角色种子（幂等，可重复执行）
INSERT INTO sys_role (role_code, role_name, is_admin, remark) VALUES
('ADMIN', '系统管理员', 1, '内置超级管理员，拥有全部面板与审批权限')
ON DUPLICATE KEY UPDATE role_name = VALUES(role_name), is_admin = VALUES(is_admin);

-- 6. admin 用户关联管理员角色（否则登录后无任何面板权限）
UPDATE sys_user SET role_id = (SELECT id FROM sys_role WHERE role_code = 'ADMIN')
WHERE user_name = 'admin' AND (role_id IS NULL OR role_id = 0);

-- 7. 默认部门种子（可选）
INSERT INTO sys_dept (parent_id, dept_name, sort) VALUES
(0, '公司总部', 1),
(0, '生产部', 2),
(0, '销售部', 3),
(0, '采购部', 4);
