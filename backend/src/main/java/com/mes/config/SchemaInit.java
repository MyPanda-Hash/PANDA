package com.mes.config;

import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

/** 启动时幂等建表：备份留痕 + 使用日志 */
@Component
public class SchemaInit implements ApplicationRunner {

    private final JdbcTemplate jdbc;

    public SchemaInit(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    public void run(ApplicationArguments args) {
        jdbc.execute("CREATE TABLE IF NOT EXISTS sys_backup_log ("
                + "id BIGINT PRIMARY KEY AUTO_INCREMENT,"
                + "file_name VARCHAR(200) NOT NULL,"
                + "file_size_kb BIGINT NULL,"
                + "status VARCHAR(20) NULL,"
                + "message VARCHAR(500) NULL,"
                + "elapsed_ms BIGINT NULL,"
                + "operator VARCHAR(50) NULL,"
                + "create_time DATETIME DEFAULT CURRENT_TIMESTAMP,"
                + "KEY idx_backup_time (create_time)"
                + ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4");
        jdbc.execute("CREATE TABLE IF NOT EXISTS sys_usage_log ("
                + "id BIGINT PRIMARY KEY AUTO_INCREMENT,"
                + "user_name VARCHAR(50) NULL,"
                + "event_type VARCHAR(20) NULL,"
                + "detail VARCHAR(200) NULL,"
                + "ip VARCHAR(50) NULL,"
                + "create_time DATETIME DEFAULT CURRENT_TIMESTAMP,"
                + "KEY idx_usage_time (create_time),"
                + "KEY idx_usage_user (user_name)"
                + ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4");
    }
}
