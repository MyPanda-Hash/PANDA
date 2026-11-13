-- 顶栏消息中心已读游标（2026-11-13）
-- 消息只保留「数字员工的群聊消息」一类，并且只显示未读：清空未读后列表为空。
-- 游标按账号存一个时间点，之后的消息才算未读；不存消息副本，AI 侧才是权威数据源。

create table if not exists portal_notice_read (
  user_name       varchar(50)  not null,
  message_read_at timestamp(0),
  update_time     timestamp(0) default now(),
  constraint pk_portal_notice_read primary key (user_name)
);

comment on table portal_notice_read is '顶栏消息已读游标：message_read_at 之前的群聊消息不再计入未读';
comment on column portal_notice_read.message_read_at is '上次清空未读的时间；为空表示从未读过（全部未读）';
