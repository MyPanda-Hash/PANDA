package com.mes.dc;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * 数采流水定时清理。
 *
 * <p>电子秤连续发送时按 1 秒一条落 {@code dc_sample}，一天 8.6 万行；数据监控页只用得到
 * 「最近 10 条」，所以每 5 分钟把每个采集点只保留最新 N 条，其余删掉。
 *
 * <p>口径与安全：只删 {@code dc_sample}，不动报警、在线日志、采集点与设备台账；按 point_id
 * 分组保留，避免多采集点互相挤掉；保留条数配 0 或负数即关闭清理。
 */
@Component
public class DcSampleCleanup {

    private static final Logger log = LoggerFactory.getLogger(DcSampleCleanup.class);

    private final JdbcTemplate jdbc;

    /** 每个采集点保留的流水条数（默认 10，与监控页展示条数对齐；<=0 关闭） */
    @Value("${mes.dc.sample.retain:10}")
    private int retain;

    public DcSampleCleanup(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Scheduled(cron = "${mes.dc.sample.cleanup-cron:0 */5 * * * *}")
    public void cleanup() {
        cleanupNow();
    }

    /** 抽成可重入方法，便于测试与手工触发。 */
    public int cleanupNow() {
        if (retain <= 0) return 0;
        try {
            int deleted = jdbc.update("""
                    delete from dc_sample s
                     where exists (
                           select 1 from (
                                 select id, row_number() over (partition by point_id order by id desc) as rn
                                   from dc_sample
                           ) t where t.id = s.id and t.rn > ?
                     )
                    """, retain);
            if (deleted > 0) log.info("[数采流水清理] 每点保留 {} 条，本次删除 {} 行", retain, deleted);
            return deleted;
        } catch (Exception e) {
            log.warn("[数采流水清理] 失败：{}", e.getMessage());
            return -1;
        }
    }
}
