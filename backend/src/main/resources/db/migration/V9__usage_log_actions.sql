-- 使用权限查看：sys_usage_log 补齐「面板业务动作」留痕所需字段。
-- 背景：这张表原本只记登录（LOGIN/LOGIN_FAIL/OPEN_AI），
-- 要回答「谁在哪张单上做了什么」还缺 面板/动作/单据号。
-- 与 YINJIA 的 yj_usage_log 口径对齐（event_type 区分 login/action）。

ALTER TABLE sys_usage_log ADD COLUMN IF NOT EXISTS panel_name  varchar(100);
ALTER TABLE sys_usage_log ADD COLUMN IF NOT EXISTS panel_code  varchar(50);
ALTER TABLE sys_usage_log ADD COLUMN IF NOT EXISTS action_name varchar(100);
ALTER TABLE sys_usage_log ADD COLUMN IF NOT EXISTS doc_no      varchar(50);
ALTER TABLE sys_usage_log ADD COLUMN IF NOT EXISTS real_name   varchar(50);

COMMENT ON COLUMN sys_usage_log.panel_name  IS '面板名称（业务动作留痕）';
COMMENT ON COLUMN sys_usage_log.panel_code  IS '面板编码';
COMMENT ON COLUMN sys_usage_log.action_name IS '动作/按钮名称';
COMMENT ON COLUMN sys_usage_log.doc_no      IS '涉及单据号';
COMMENT ON COLUMN sys_usage_log.real_name   IS '操作人姓名（登录时冗余存，避免每次查用户表）';

-- 按面板/动作过滤是页面主要查询路径，补组合索引
CREATE INDEX IF NOT EXISTS idx_sys_usage_log_panel ON sys_usage_log (panel_code, create_time);
CREATE INDEX IF NOT EXISTS idx_sys_usage_log_action ON sys_usage_log (action_name);