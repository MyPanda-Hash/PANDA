-- 2026-08-31：为 form_data 与 sys_role_panel 补唯一约束（审查发现：单号/授权并发重复无兜底）
-- 执行：mysql -u<用户> -p light_mes < migrate_unique_keys_20260831.sql
-- 幂等：已存在同名索引时自动跳过，可重复执行。
-- 注意：执行前先跑下面两条体检 SQL，若有重复行必须先人工清理（保留最新 id 删除其余），否则 ADD UNIQUE 会失败：
--   SELECT panel_code, form_no, COUNT(*) c FROM form_data GROUP BY panel_code, form_no HAVING c > 1;
--   SELECT role_id, panel_code, COUNT(*) c FROM sys_role_panel GROUP BY role_id, panel_code HAVING c > 1;

USE light_mes;

-- form_data: (panel_code, form_no) → 唯一
SET @stmt := (
  SELECT IF(COUNT(*) = 0,
    'ALTER TABLE form_data ADD UNIQUE KEY uk_panel_form (panel_code, form_no)',
    'SELECT ''[skip] form_data.uk_panel_form 已存在'' AS msg')
  FROM information_schema.statistics
  WHERE table_schema = DATABASE() AND table_name = 'form_data' AND index_name = 'uk_panel_form'
);
PREPARE stmt FROM @stmt; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- sys_role_panel: (role_id, panel_code) → 唯一
SET @stmt := (
  SELECT IF(COUNT(*) = 0,
    'ALTER TABLE sys_role_panel ADD UNIQUE KEY uk_role_panel (role_id, panel_code)',
    'SELECT ''[skip] sys_role_panel.uk_role_panel 已存在'' AS msg')
  FROM information_schema.statistics
  WHERE table_schema = DATABASE() AND table_name = 'sys_role_panel' AND index_name = 'uk_role_panel'
);
PREPARE stmt FROM @stmt; EXECUTE stmt; DEALLOCATE PREPARE stmt;
