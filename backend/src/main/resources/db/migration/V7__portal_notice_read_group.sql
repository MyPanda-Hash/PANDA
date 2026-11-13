-- 消息按群聊分类（2026-11-13）：未读要按群记，点开某个群只清这个群的未读。
-- V6 的全局游标只能记「一次清空」，做不到「某个群读过、另一个群没读」，这里换成按群游标。

drop table if exists portal_notice_read;

create table if not exists portal_notice_read_group (
  user_name   varchar(50) not null,
  group_id    varchar(64) not null,
  read_at     timestamp(0),
  update_time timestamp(0) default now(),
  constraint pk_portal_notice_read_group primary key (user_name, group_id)
);

comment on table portal_notice_read_group is '按群聊的已读游标：read_at 之前的消息不再计入该群未读';
comment on column portal_notice_read_group.read_at is '该群上次清空未读的时间；为空表示这个群从未读过';
