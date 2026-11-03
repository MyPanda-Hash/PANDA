-- ============================================================
-- 2026-09-01 阶段 A：阿里云机器翻译 i18n 栈
--  sys_locale      界面语言注册表（zh-CN 恒启用，其余按需启用）
--  sys_translation 词条词典（scope='biz'，机翻成功落库，UNIQUE(scope,ref_key,locale)）
-- 幂等：CREATE TABLE IF NOT EXISTS + INSERT ... ON DUPLICATE KEY / NOT EXISTS，可重复执行
-- ============================================================
-- 适用库：light_mes（MySQL 8.x）

CREATE TABLE IF NOT EXISTS sys_locale (
  locale      VARCHAR(10)  NOT NULL COMMENT '语言码（zh-CN/en/ja…，见后端 localeKey 归一）',
  name_zh     VARCHAR(50)  NOT NULL COMMENT '中文名',
  name_native VARCHAR(50)  NULL COMMENT '本地语名',
  enabled     TINYINT(1)   DEFAULT 1 COMMENT '1 启用 0 停用',
  sort        INT          DEFAULT 0,
  PRIMARY KEY (locale)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT '界面语言注册表';

CREATE TABLE IF NOT EXISTS sys_translation (
  id          BIGINT PRIMARY KEY AUTO_INCREMENT,
  scope       VARCHAR(20)  DEFAULT 'biz' COMMENT '词条域（biz=界面词条）',
  ref_key     VARCHAR(200) NOT NULL COMMENT '源词条（中文原文即键）',
  locale      VARCHAR(10)  NOT NULL COMMENT '目标语言键（en/ja/zh-TW…）',
  text        VARCHAR(500) NULL COMMENT '译文（空=未翻译）',
  source      VARCHAR(10)  DEFAULT 'mt' COMMENT '来源（mt=机翻/manual=人工）',
  create_time DATETIME     DEFAULT CURRENT_TIMESTAMP,
  update_time DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_trans (scope, ref_key, locale),
  KEY idx_trans (locale)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT '界面词条词典';

-- 语言种子（10 种；zh-CN 恒启用恒首位由代码保证；zh-TW 默认停用，繁转可用时启用）
-- enabled 不覆盖：保留管理员手动启停状态
INSERT INTO sys_locale (locale, name_zh, name_native, enabled, sort) VALUES
  ('zh-CN', '简体中文', '简体中文', 1, 0),
  ('en',    '英语',    'English',  1, 10),
  ('ja',    '日语',    '日本語',    1, 20),
  ('ko',    '韩语',    '한국어',    1, 30),
  ('de',    '德语',    'Deutsch',  1, 40),
  ('fr',    '法语',    'Français', 1, 50),
  ('es',    '西班牙语', 'Español',  1, 60),
  ('ru',    '俄语',    'Русский',  1, 70),
  ('th',    '泰语',    'ไทย',      1, 80),
  ('zh-TW', '繁体中文', '繁體中文',  0, 90)
ON DUPLICATE KEY UPDATE
  name_zh = VALUES(name_zh), name_native = VALUES(name_native), sort = VALUES(sort);

-- 校验
SELECT locale, name_zh, enabled, sort FROM sys_locale ORDER BY sort;
SELECT COUNT(*) AS translation_rows FROM sys_translation;
