package com.mes.service;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import jakarta.annotation.PostConstruct;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.Map;

/**
 * 数据库定时备份。
 *
 * <p>默认每周一 02:00 跑一次（{@code mes.backup.cron}，时区 {@code mes.backup.zone}）。
 * 备份走的是和数据安全页「立即备份」同一条链路，所以文件、日志、下载能力完全一致，
 * 只是操作人记成 {@code system}。
 *
 * <p>为什么不放到系统 crontab：备份要读数据库密码，密码只在应用进程的环境变量里；
 * 放外部脚本就得把密码再抄一份到别处。跟着应用跑最省事，也能复用权限与审计。
 *
 * <p>注意：应用没在跑的时候不会备份。本地这台机器上服务是手动启动的，
 * 想要「关机也备」得靠外部计划任务，那是另一件事。
 */
@Component
public class BackupScheduler {

    private static final Logger log = LoggerFactory.getLogger(BackupScheduler.class);

    private final DbBackupService backup;

    @Value("${mes.backup.auto:true}")
    private boolean auto;

    @Value("${mes.backup.keep:8}")
    private int keep;

    @Value("${mes.backup.cron:0 0 2 * * MON}")
    private String cron;

    @Value("${mes.backup.zone:Asia/Shanghai}")
    private String zone;

    public BackupScheduler(DbBackupService backup) {
        this.backup = backup;
    }

    /** 启动时把生效的计划打出来，省得事后猜「到底有没有开」。 */
    @PostConstruct
    public void announce() {
        if (!auto) {
            log.info("[backup] 定时备份未启用（mes.backup.auto=false），仍可手动「立即备份」");
            return;
        }
        log.info("[backup] 定时备份已启用 cron='{}' zone={} keep={}", cron, zone, keep);
    }

    @Scheduled(cron = "${mes.backup.cron:0 0 2 * * MON}", zone = "${mes.backup.zone:Asia/Shanghai}")
    public void weekly() {
        if (!auto) {
            log.info("[backup] 定时备份已关闭（mes.backup.auto=false）");
            return;
        }
        log.info("[backup] 定时备份开始");
        try {
            Map<String, Object> result = backup.runNow("system");
            log.info("[backup] 定时备份结束 status={} file={} sizeKb={} elapsedMs={}",
                    result.get("status"), result.get("fileName"),
                    result.get("fileSizeKb"), result.get("elapsedMs"));
            int removed = backup.prune(keep);
            if (removed > 0) {
                log.info("[backup] 清理旧备份 {} 份，保留最近 {} 份", removed, keep);
            }
        } catch (Exception e) {
            // runNow 内部已经把失败写进 sys_backup_log，这里只留一行日志
            log.error("[backup] 定时备份异常：{}", e.getMessage());
        }
    }
}
