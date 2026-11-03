-- ============================================================
-- 2026-09-01 阶段 C：「表格调整」列定制（顺序/显隐/别名）按用户持久化
--  新表 px_column_pref：panel_code + owner（用户名，'' 预留全局默认）维度保存
--  每列一行：seq 顺序（步长 10）/ alias 别名 / visible 显隐
--  无行 = 默认列序（读取出口叠加，绝不写回 panel_config.config）
-- 幂等：CREATE TABLE IF NOT EXISTS，可重复执行
-- ============================================================
-- 适用库：light_mes（MySQL 8.x）

CREATE TABLE IF NOT EXISTS px_column_pref (
  id          BIGINT PRIMARY KEY AUTO_INCREMENT,
  panel_code  VARCHAR(50)  NOT NULL COMMENT '面板编码',
  owner       VARCHAR(50)  NOT NULL DEFAULT '' COMMENT '用户名（'' 预留全局默认行）',
  col_name    VARCHAR(100) NOT NULL COMMENT '列名（中文列名即数据键）',
  seq         INT          DEFAULT 100 COMMENT '顺序（步长 10）',
  alias       VARCHAR(100) NULL COMMENT '栏名别名（空=默认列名）',
  visible     TINYINT(1)   DEFAULT 1 COMMENT '1显示 0隐藏',
  update_by   VARCHAR(50)  NULL,
  update_time DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_col_pref (panel_code, owner, col_name),
  KEY idx_col_pref (panel_code, owner)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT '表格列定制表（按用户）';

-- 校验：无种子（无行=默认列序）
SELECT COUNT(*) AS pref_rows FROM px_column_pref;
