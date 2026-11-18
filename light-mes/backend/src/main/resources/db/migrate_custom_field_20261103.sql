-- 轻 MES：面板自定义字段（用户自助加字段）2026-11-03
--
-- 背景：面板字段一直是 panel_config.config 里的 JSON，加字段要改库/改配置的人来干。
--       借鉴 YINJIA-MES 的「字段管理（动态字段 + 备用列池）」把这件事交给有「修改」权限的用户。
--       本项目面板数据本来就存在 form_data.data / detail_data 的 JSON 里（不是物理列），
--       所以不需要备用列池，也不需要 DDL：新增字段 = 登记一条元数据，读取出口并进配置即可。
--
-- 为什么不写进 panel_config.config：4 个 Registry 启动时会整体覆盖该 JSON
--   （SalesFinancePanelRegistry/ReportPanelRegistry/... 的 upsert），用户配置会被冲掉。
--   与 px_column_pref（按用户的表格调整）同一套做法：单独建表，读取出口合并。
--
-- 幂等：IF NOT EXISTS；可重复执行。
-- 用法：mysql -uroot -p --default-character-set=utf8mb4 --execute="source backend/src/main/resources/db/migrate_custom_field_20261103.sql"

USE light_mes;

CREATE TABLE IF NOT EXISTS px_custom_field (
  id          BIGINT       PRIMARY KEY AUTO_INCREMENT,
  panel_code  VARCHAR(50)  NOT NULL COMMENT '面板编码',
  scope       VARCHAR(10)  NOT NULL DEFAULT 'detail' COMMENT '位置：header=表头 / detail=明细',
  tab_key     VARCHAR(50)  NOT NULL DEFAULT '' COMMENT '明细页签 key（scope=detail 时必填）',
  data_name   VARCHAR(60)  NOT NULL COMMENT '字段名（中文数据键）',
  data_type   VARCHAR(20)  NOT NULL DEFAULT '文本' COMMENT '类型：文本/下拉框/日期/是否/小数/整数',
  options     VARCHAR(500) NULL COMMENT '下拉词表（逗号分隔，仅下拉框）',
  field_width INT          NULL COMMENT '列宽（px，空=按类型推算）',
  is_required TINYINT      NOT NULL DEFAULT 0 COMMENT '是否必填',
  in_grid     TINYINT      NOT NULL DEFAULT 1 COMMENT '是否进列表列（明细页签）',
  seq         INT          NOT NULL DEFAULT 0 COMMENT '排序（与内置字段一致：10/20/30…）',
  created_by  VARCHAR(50)  NULL COMMENT '创建人',
  create_time DATETIME     DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  UNIQUE KEY uk_panel_field (panel_code, scope, tab_key, data_name),
  KEY idx_panel (panel_code)
) COMMENT '面板自定义字段：读取出口合并进面板配置，停用即删行，业务数据保留';

CREATE TABLE IF NOT EXISTS px_field_log (
  id         BIGINT       PRIMARY KEY AUTO_INCREMENT,
  panel_code VARCHAR(50)  NOT NULL COMMENT '面板编码',
  scope      VARCHAR(10)  NOT NULL DEFAULT '' COMMENT '位置',
  tab_key    VARCHAR(50)  NOT NULL DEFAULT '' COMMENT '页签 key',
  data_name  VARCHAR(60)  NOT NULL COMMENT '字段名',
  data_type  VARCHAR(20)  NOT NULL DEFAULT '' COMMENT '类型',
  action     VARCHAR(10)  NOT NULL COMMENT '动作：add / retire',
  op_by      VARCHAR(50)  NOT NULL COMMENT '操作人',
  op_time    DATETIME     DEFAULT CURRENT_TIMESTAMP COMMENT '操作时间',
  detail     VARCHAR(500) NULL COMMENT '备注',
  KEY idx_panel (panel_code)
) COMMENT '自定义字段操作审计（加字段/停用字段可回溯）';
