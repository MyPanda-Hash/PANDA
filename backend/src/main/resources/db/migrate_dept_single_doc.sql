-- 将部门档案从“一部门一单据”迁移为与员工相同的“单档案 + 明细行”模式。
-- 可重复执行：如果已存在 depts 明细，只会保留该单档案并刷新面板配置。
-- 注意：当前表使用 MyISAM，不支持事务回滚，因此通过表锁和先更新后删除降低中断风险。

SET NAMES utf8mb4;
SET SESSION group_concat_max_len = 16 * 1024 * 1024;

CREATE TEMPORARY TABLE IF NOT EXISTS migration_dept_assert (
  ok TINYINT NOT NULL
);
TRUNCATE TABLE migration_dept_assert;

LOCK TABLES panel_config WRITE, form_data WRITE;

SET @dept_row_count := (
  SELECT COUNT(*)
  FROM form_data
  WHERE panel_code = 'DEPT'
);

SET @dept_single_id := (
  SELECT MIN(id)
  FROM form_data
  WHERE panel_code = 'DEPT'
    AND JSON_VALID(detail_data)
    AND JSON_TYPE(JSON_EXTRACT(detail_data, '$.depts')) = 'ARRAY'
);

SET @dept_invalid_count := (
  SELECT COUNT(*)
  FROM form_data
  WHERE panel_code = 'DEPT'
    AND @dept_single_id IS NULL
    AND (
      NOT JSON_VALID(data)
      OR JSON_UNQUOTE(JSON_EXTRACT(data, '$."部门编码"')) IS NULL
      OR JSON_UNQUOTE(JSON_EXTRACT(data, '$."部门名称"')) IS NULL
    )
);

-- 如旧部门数据不是有效 JSON 或缺少关键字段，立即停止，避免静默丢失数据。
INSERT INTO migration_dept_assert(ok)
VALUES (IF(@dept_invalid_count = 0, 1, NULL));

SET @dept_keeper_id := COALESCE(
  @dept_single_id,
  (SELECT MIN(id) FROM form_data WHERE panel_code = 'DEPT')
);

SET @dept_rows_json := (
  SELECT COALESCE(
    CONCAT('[', GROUP_CONCAT(data ORDER BY id SEPARATOR ','), ']'),
    '[]'
  )
  FROM form_data
  WHERE panel_code = 'DEPT'
    AND @dept_single_id IS NULL
);

SET @dept_detail_json := JSON_OBJECT(
  'depts',
  CAST(@dept_rows_json AS JSON)
);

-- 没有部门数据时创建空的部门档案。
INSERT INTO form_data (
  panel_code, form_no, data, detail_data, status,
  create_by, create_time, update_by, update_time
)
SELECT
  'DEPT', '部门档案', JSON_OBJECT('备注', '部门档案'), JSON_OBJECT('depts', JSON_ARRAY()), '启用',
  'admin', NOW(), 'admin', NOW()
WHERE @dept_row_count = 0;

SET @dept_keeper_id := COALESCE(
  @dept_keeper_id,
  (SELECT MIN(id) FROM form_data WHERE panel_code = 'DEPT')
);

-- 首次迁移时，把原有独立档案按 id 顺序合并到 depts 明细中。
UPDATE form_data
SET form_no = '部门档案',
    data = JSON_OBJECT('备注', '部门档案'),
    detail_data = IF(@dept_single_id IS NULL, @dept_detail_json, detail_data),
    status = '启用',
    update_by = COALESCE(update_by, 'admin'),
    update_time = NOW()
WHERE id = @dept_keeper_id;

-- 更新完成后再删除其余旧档案；脚本中断后重新执行也不会重复合并。
DELETE FROM form_data
WHERE panel_code = 'DEPT'
  AND id <> @dept_keeper_id;

INSERT INTO panel_config (panel_code, panel_name, category, config)
VALUES ('DEPT', '部门', '基础档案', '{"metadata":{"panelCode":"DEPT","panelName":"部门","panelCategory":"基础档案","singleDoc":true,"panelState":{"dataName":"状态","dataType":"STRING","defaultOptions":["启用","停用"]},"buttonGroups":[{"name":"新增","actions":["新增"]},{"name":"修改","actions":["修改"]},{"name":"保存","actions":["保存","保存新增"]},{"name":"删除","actions":["删除","删除单据"]},{"name":"查找","actions":["查找","刷新"]},{"name":"打印","actions":["打印","预览"]},{"name":"导入","actions":["下载模板","导入"]},{"name":"更多","actions":["复制","导出","退出"]}],"panelButtons":[{"buttonName":"新增流程"},{"buttonName":"删除"},{"buttonName":"刷新"},{"buttonName":"保存"},{"buttonName":"放弃"}],"panelPageDto":{"formPages":[{"events":[],"formName":"部门","fieldNames":"备注","bottomOperationBarBtn":[{"buttonName":"保存"},{"buttonName":"删除"},{"buttonName":"放弃"}]}],"tablePages":[{"events":[],"gridTabs":[{"label":"部门明细","columns":["部门编码","部门名称","负责人","停用"],"rowSource":"rows"}],"tableName":"部门列表","topBarBtn":[{"buttonName":"新增流程"},{"buttonName":"删除"},{"buttonName":"刷新"}],"queryFields":[],"rowOperationBarBtn":[]}]},"version":"1.0"},"dataSchema":{"type":"object","fields":[{"dataName":"备注","dataType":"文本"}]},"detail":{"tabs":[{"key":"depts","label":"部门明细","fields":[{"dataName":"部门编码","dataType":"文本","isRequired":true},{"dataName":"部门名称","dataType":"文本","isRequired":true},{"dataName":"负责人","dataType":"文本"},{"dataName":"停用","dataType":"是否","defaultValue":false}],"isRequired":false}]}}')
ON DUPLICATE KEY UPDATE
  panel_name = VALUES(panel_name),
  category = VALUES(category),
  config = VALUES(config),
  version = '1.0',
  update_time = NOW();

UNLOCK TABLES;
DROP TEMPORARY TABLE IF EXISTS migration_dept_assert;

SELECT
  pc.panel_code,
  JSON_UNQUOTE(JSON_EXTRACT(pc.config, '$.metadata.singleDoc')) AS single_doc,
  JSON_UNQUOTE(JSON_EXTRACT(pc.config, '$.detail.tabs[0].key')) AS detail_key,
  COUNT(fd.id) AS form_count,
  COALESCE(MAX(JSON_LENGTH(JSON_EXTRACT(fd.detail_data, '$.depts'))), 0) AS dept_count
FROM panel_config pc
LEFT JOIN form_data fd ON fd.panel_code = pc.panel_code
WHERE pc.panel_code = 'DEPT'
GROUP BY pc.panel_code, pc.config;