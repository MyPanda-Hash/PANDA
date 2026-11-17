-- V15：让「派工 → 报工」能落到具体的人
--
-- 起因（2026-11-17 实测）：一线看板算不出「我的产量」，因为
--   ① EMP（员工档案）面板只有「备注」一个字段，是个空壳，库里只有一行空记录，
--      于是派工/报工的「工人」参照（refPanel=EMP）永远选不到人；
--   ② DISPATCH 表头没有班组/工人，派活只能逐行填，实际没人填；
--   ③ 报工下推其实已经会带 班组/工人（PxService 里 pr.getOrDefault("班组","")），
--      只是上游一直是空的，带出来也空。
--
-- 本脚本补前两件（第三件在 Java 侧补「表头 → 明细」回填）：
--   ① 补全 EMP 面板定义；
--   ② 把现有 sys_user 账号初始化为员工档案；
--   ③ DISPATCH 表头加「班组 / 工人」，一次派整单。
--
-- 注意：JSON 用单引号字符串包（JSON 内无单引号），不用美元引用 ——
-- Flyway 会把美元引用紧邻左花括号处当成占位符而启动失败。

-- ① EMP 面板补全（结构对齐能正常工作的 TEAM 面板）
UPDATE panel_config
   SET config = '{"metadata":{"panelCode":"EMP","panelName":"员工","panelCategory":"基础档案","autoCodeField":"员工编码","panelState":{"dataName":"状态","dataType":"STRING","defaultOptions":["启用","停用"]},"panelPageDto":{"tablePages":[{"tableName":"员工列表","queryFields":[{"dataName":"员工编码","dataType":"文本"},{"dataName":"员工名称","dataType":"文本"},{"dataName":"所属部门","dataType":"文本"}],"gridTabs":[{"label":"列表","rowSource":"rows","columns":["员工编码","员工名称","所属部门","班组","岗位","在职状态","手机"]}],"topBarBtn":[{"buttonName":"新增流程"},{"buttonName":"删除"},{"buttonName":"刷新"}],"rowOperationBarBtn":[],"events":[]}],"formPages":[{"formName":"员工","fieldNames":"员工编码,员工名称,所属部门,班组,岗位,手机,在职状态,备注","bottomOperationBarBtn":[{"buttonName":"保存"},{"buttonName":"删除"},{"buttonName":"放弃"}],"events":[]}]},"panelButtons":[{"buttonName":"新增流程"},{"buttonName":"删除"},{"buttonName":"刷新"},{"buttonName":"保存"},{"buttonName":"放弃"}],"buttonGroups":[{"name":"新增","actions":["新增"]},{"name":"修改","actions":["修改"]},{"name":"删除","actions":["删除","删除单据"]},{"name":"查找","actions":["查找","刷新"]},{"name":"打印","actions":["打印","预览"]},{"name":"导入","actions":["下载模板","导入"]},{"name":"更多","actions":["复制","导出","退出"]}],"version":"1.0"},"dataSchema":{"type":"object","fields":[{"dataName":"员工编码","dataType":"文本","isRequired":true,"autoCode":true},{"dataName":"员工名称","dataType":"文本","isRequired":true},{"dataName":"所属部门","dataType":"下拉框","options":["熔铸车间","轧制车间","精整车间","测试车间"]},{"dataName":"班组","dataType":"参照","refPanel":"TEAM","refField":"班组名称","displayField":"班组名称","filter":{"是否停用":false}},{"dataName":"岗位","dataType":"文本"},{"dataName":"手机","dataType":"文本"},{"dataName":"在职状态","dataType":"下拉框","options":["在职","离职"],"defaultValue":"在职"},{"dataName":"备注","dataType":"文本"}]},"detail":{"tabs":[]}}',
       update_time = now()
 WHERE panel_code = 'EMP';

-- ② 员工档案初始化：把系统账号变成可选工人
INSERT INTO form_data (panel_code, form_no, data, detail_data, status, create_by, create_time, update_time, factory_code)
SELECT 'EMP',
       'EMP-' || u.user_name,
       jsonb_build_object(
         '编号', 'EMP-' || u.user_name,
         '员工编码', u.user_name,
         '员工名称', coalesce(nullif(u.real_name, ''), u.user_name),
         '所属部门', CASE r.role_code
                        WHEN 'KEEPER' THEN '熔铸车间'
                        WHEN 'PLANNER' THEN '轧制车间'
                        WHEN 'QC' THEN '测试车间'
                        WHEN 'SALES' THEN '精整车间'
                        ELSE '熔铸车间' END,
         '班组', '',
         '岗位', coalesce(r.role_name, ''),
         '手机', coalesce(u.phone, ''),
         '在职状态', CASE WHEN u.enabled = 1 THEN '在职' ELSE '离职' END,
         '备注', ''
       )::text,
       '{"tabs":[]}',
       '启用',
       'system',
       now(),
       now(),
       coalesce(u.factory_code, 'F01')
  FROM sys_user u
  LEFT JOIN sys_role r ON r.id = u.role_id
 WHERE NOT EXISTS (SELECT 1 FROM form_data f WHERE f.panel_code = 'EMP' AND f.form_no = 'EMP-' || u.user_name);

-- 清掉那行空的员工记录（无姓名无编码，占着列表第一行看不出问题）
DELETE FROM form_data
 WHERE panel_code = 'EMP'
   AND coalesce(data::jsonb ->> '员工名称', '') = ''
   AND coalesce(data::jsonb ->> '员工编码', '') = '';

-- ③ DISPATCH 表头加「班组 / 工人」
UPDATE panel_config
   SET config = jsonb_set(
         jsonb_set(
           config::jsonb,
           '{dataSchema,fields}',
           (config::jsonb -> 'dataSchema' -> 'fields') || jsonb_build_array(
             jsonb_build_object('dataName', '班组', 'dataType', '参照',
                                'isRequired', false, 'defaultValue', '',
                                'refPanel', 'TEAM', 'refField', '班组名称',
                                'displayField', '班组名称',
                                'filter', jsonb_build_object('是否停用', false)),
             jsonb_build_object('dataName', '工人', 'dataType', '参照',
                                'isRequired', false, 'defaultValue', '',
                                'refPanel', 'EMP', 'refField', '员工名称',
                                'displayField', '员工名称',
                                'filter', jsonb_build_object('在职状态', '在职'))
           )
         ),
         '{metadata,panelPageDto,formPages,0,fieldNames}',
         to_jsonb(((config::jsonb -> 'metadata' -> 'panelPageDto' -> 'formPages' -> 0 ->> 'fieldNames') || ',班组,工人'))
       )::text,
       update_time = now()
 WHERE panel_code = 'DISPATCH'
   AND (config::jsonb -> 'metadata' -> 'panelPageDto' -> 'formPages' -> 0 ->> 'fieldNames') NOT LIKE '%班组%';
