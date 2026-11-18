package com.mes.service;

import com.mes.entity.SysUsageLog;
import com.mes.mapper.SysUsageLogMapper;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;

/**
 * 使用日志埋点：写失败不影响业务主流程。
 *
 * 记录口径（与 YINJIA 对齐）：
 * - 登录成功/失败记；
 * - 面板的「业务动作」记（提交/审批/生单/删除这类改变数据的操作）；
 * - 查询、刷新、翻页、列宽调整等纯浏览动作不记 —— 记了会把表撑满且没有审计价值。
 */
@Service
public class UsageLogService {

    private final SysUsageLogMapper mapper;

    public UsageLogService(SysUsageLogMapper mapper) {
        this.mapper = mapper;
    }

    public void record(String userName, String eventType, String detail, String ip) {
        insert(userName, null, eventType, detail, null, null, null, null, ip);
    }

    /** 登录时把姓名一起落库，页面按账号分组时不必再回查用户表。 */
    public void recordLogin(String userName, String realName, boolean success, String detail, String ip) {
        insert(userName, realName, success ? "LOGIN" : "LOGIN_FAIL", detail, null, null, null, null, ip);
    }

    /** 面板业务动作。docNo 取不到时传 null。 */
    public void recordAction(String userName, String realName, String panelCode, String panelName,
                             String actionName, String docNo, String ip) {
        insert(userName, realName, "ACTION", actionName, panelCode, panelName, actionName, docNo, ip);
    }

    private void insert(String userName, String realName, String eventType, String detail,
                        String panelCode, String panelName, String actionName, String docNo, String ip) {
        try {
            SysUsageLog log = new SysUsageLog();
            log.setUserName(cut(userName, 50));
            log.setRealName(cut(realName, 50));
            log.setEventType(cut(eventType, 20));
            log.setDetail(cut(detail, 200));
            log.setPanelCode(cut(panelCode, 50));
            log.setPanelName(cut(panelName, 100));
            log.setActionName(cut(actionName, 100));
            log.setDocNo(cut(docNo, 50));
            log.setIp(cut(ip, 50));
            log.setCreateTime(LocalDateTime.now());
            mapper.insert(log);
        } catch (Exception ignored) {
            // 审计失败不能影响业务主流程
        }
    }

    static String cut(String v, int max) {
        if (v == null) return null;
        return v.length() <= max ? v : v.substring(0, max);
    }
}