-- 初始化向导需要「部门编码 → 部门」的稳定映射：员工模板按编码引用部门。
-- sys_dept 原本只有 dept_name，编码只存在于上传表格里，跨批次就断了，因此补一列。

alter table sys_dept add column if not exists dept_code varchar(50);

comment on column sys_dept.dept_code is '部门编码：初始化导入时按编码引用上级与员工归属（历史数据为空）';

create unique index if not exists uk_sys_dept_code on sys_dept (dept_code) where dept_code is not null;
