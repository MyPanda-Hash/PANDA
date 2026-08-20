-- ============================================================
-- light-mes 部署修补 SQL：init.sql 与 jar 代码结构不同步
-- 1) sys_user 缺 role_id / dept_id 列
-- 2) sys_role / sys_role_panel / sys_dept 三张表未建
-- 用法: mysql -uroot -proot light_mes < light-mes-patch.sql
-- ============================================================
USE light_mes;

-- 1. sys_user 补列（幂等：列已存在则跳过）
SET @has_role := (SELECT COUNT(*) FROM information_schema.COLUMNS
                  WHERE TABLE_SCHEMA='light_mes' AND TABLE_NAME='sys_user' AND COLUMN_NAME='role_id');
SET @sql := IF(@has_role=0, 'ALTER TABLE sys_user ADD COLUMN role_id BIGINT COMMENT ''角色ID''', 'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

SET @has_dept := (SELECT COUNT(*) FROM information_schema.COLUMNS
                  WHERE TABLE_SCHEMA='light_mes' AND TABLE_NAME='sys_user' AND COLUMN_NAME='dept_id');
SET @sql := IF(@has_dept=0, 'ALTER TABLE sys_user ADD COLUMN dept_id BIGINT COMMENT ''部门ID''', 'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- 2. 建缺失的三张表（幂等）
CREATE TABLE IF NOT EXISTS sys_role (
  id          BIGINT PRIMARY KEY AUTO_INCREMENT,
  role_code   VARCHAR(50)  NOT NULL COMMENT '角色编码',
  role_name   VARCHAR(50)  NOT NULL COMMENT '角色名称',
  is_admin    TINYINT      DEFAULT 0 COMMENT '1超级管理员 0普通',
  remark      VARCHAR(200) COMMENT '备注',
  create_time DATETIME     DEFAULT CURRENT_TIMESTAMP
) COMMENT '角色表';

CREATE TABLE IF NOT EXISTS sys_role_panel (
  id          BIGINT PRIMARY KEY AUTO_INCREMENT,
  role_id     BIGINT       NOT NULL COMMENT '角色ID',
  panel_code  VARCHAR(50)  NOT NULL COMMENT '面板编码',
  can_approve TINYINT      DEFAULT 0 COMMENT '1可审批 0否',
  KEY idx_role_panel (role_id, panel_code)
) COMMENT '角色-面板授权表';

CREATE TABLE IF NOT EXISTS sys_dept (
  id          BIGINT PRIMARY KEY AUTO_INCREMENT,
  parent_id   BIGINT       DEFAULT 0 COMMENT '父部门ID，0为根',
  dept_name   VARCHAR(50)  NOT NULL COMMENT '部门名称',
  sort        INT          DEFAULT 0 COMMENT '排序',
  create_time DATETIME     DEFAULT CURRENT_TIMESTAMP
) COMMENT '部门表';

-- 3. 种子数据：管理员角色 + admin 用户绑定
INSERT INTO sys_role (id, role_code, role_name, is_admin, remark) VALUES
(1, 'ADMIN', '系统管理员', 1, '内置超级管理员角色（全面板+全审批）')
ON DUPLICATE KEY UPDATE is_admin = 1;

UPDATE sys_user SET role_id = 1 WHERE user_name = 'admin' AND (role_id IS NULL OR role_id = 0);

-- 4. 部门种子（与前端组织管理页面一致）
INSERT INTO sys_dept (id, parent_id, dept_name, sort) VALUES
(1, 0, '公司', 1),
(2, 1, '总经办', 1),
(3, 1, '销售部', 2),
(4, 1, '采购部', 3),
(5, 1, '生产部', 4),
(6, 5, '熔铸车间', 1),
(7, 5, '轧制车间', 2),
(8, 5, '精整车间', 3),
(9, 5, '测试车间', 4),
(10, 1, '质检部', 5),
(11, 1, '仓储部', 6)
ON DUPLICATE KEY UPDATE dept_name = dept_name;

-- 5. 验收查询
SELECT 'sys_user 列' AS chk, GROUP_CONCAT(COLUMN_NAME ORDER BY ORDINAL_POSITION) AS cols
  FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='light_mes' AND TABLE_NAME='sys_user';
SELECT id, role_code, role_name, is_admin FROM sys_role;
SELECT user_name, role_id FROM sys_user WHERE user_name='admin';
SELECT COUNT(*) AS dept_count FROM sys_dept;
