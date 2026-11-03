package com.mes.service;

import com.mes.entity.SysUsageLog;
import com.mes.mapper.SysUsageLogMapper;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;

/** 使用日志埋点：写失败不影响业务主流程 */
@Service
public class UsageLogService {

    private final SysUsageLogMapper mapper;

    public UsageLogService(SysUsageLogMapper mapper) {
        this.mapper = mapper;
    }

    public void record(String userName, String eventType, String detail, String ip) {
        try {
            SysUsageLog log = new SysUsageLog();
            log.setUserName(cut(userName, 50));
            log.setEventType(cut(eventType, 20));
            log.setDetail(cut(detail, 200));
            log.setIp(cut(ip, 50));
            log.setCreateTime(LocalDateTime.now());
            mapper.insert(log);
        } catch (Exception ignored) {
            // 审计失败不能影响登录/业务主流程
        }
    }

    static String cut(String v, int max) {
        if (v == null) return null;
        return v.length() <= max ? v : v.substring(0, max);
    }
}
