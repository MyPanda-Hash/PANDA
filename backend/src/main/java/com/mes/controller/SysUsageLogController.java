package com.mes.controller;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.mes.dto.ApiResult;
import com.mes.entity.SysUsageLog;
import com.mes.mapper.SysUsageLogMapper;
import com.mes.service.RoleService;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** 系统管理 · 使用日志：登录与关键操作留痕查询（仅管理员） */
@RestController
@RequestMapping("/api/sys/usage")
public class SysUsageLogController {

    private final SysUsageLogMapper usageMapper;
    private final RoleService roleService;

    public SysUsageLogController(SysUsageLogMapper usageMapper, RoleService roleService) {
        this.usageMapper = usageMapper;
        this.roleService = roleService;
    }

    /**
     * 按账号分组查询（「使用权限查看」页面数据源）。
     *
     * 为什么要分组：日志天然是「一个人一串记录」，平铺成一张大表没人看得完；
     * 按账号折叠后，先看谁活跃、再展开看他具体做了什么。
     * 组序按各账号最近活跃时间倒序，活跃的人排前面。
     */
    @GetMapping("/grouped")
    public ApiResult<Map<String, Object>> grouped(
            Authentication auth,
            @RequestParam(required = false) String userName,
            @RequestParam(required = false) String panelName,
            @RequestParam(required = false) String actionName,
            @RequestParam(required = false) String start,
            @RequestParam(required = false) String end,
            @RequestParam(defaultValue = "2000") int limit) {
        if (auth == null || !roleService.isAdminUser(auth.getName())) {
            return ApiResult.fail(403, "仅管理员可操作");
        }
        LambdaQueryWrapper<SysUsageLog> q = new LambdaQueryWrapper<>();
        if (userName != null && !userName.isBlank()) q.like(SysUsageLog::getUserName, userName.trim());
        if (panelName != null && !panelName.isBlank()) q.like(SysUsageLog::getPanelName, panelName.trim());
        if (actionName != null && !actionName.isBlank()) q.like(SysUsageLog::getActionName, actionName.trim());
        if (start != null && !start.isBlank()) q.ge(SysUsageLog::getCreateTime, start.trim() + " 00:00:00");
        if (end != null && !end.isBlank()) q.le(SysUsageLog::getCreateTime, end.trim() + " 23:59:59");
        int cap = Math.max(1, Math.min(limit, 5000));
        q.orderByDesc(SysUsageLog::getId).last("limit " + cap);

        List<SysUsageLog> rows = usageMapper.selectList(q);

        // 按账号聚合；用 LinkedHashMap 保持「首次出现顺序」，而查询已按时间倒序，
        // 所以第一个出现的账号就是最近最活跃的。
        Map<String, Map<String, Object>> byUser = new LinkedHashMap<>();
        for (SysUsageLog row : rows) {
            String key = row.getUserName() == null ? "(未知)" : row.getUserName();
            Map<String, Object> group = byUser.computeIfAbsent(key, k -> {
                Map<String, Object> g = new LinkedHashMap<>();
                g.put("userName", k);
                g.put("realName", row.getRealName() == null ? "" : row.getRealName());
                g.put("rows", new ArrayList<Map<String, Object>>());
                return g;
            });
            // 姓名取最近一条非空的（老数据没有 real_name 列值）
            if (row.getRealName() != null && !row.getRealName().isBlank()) {
                group.put("realName", row.getRealName());
            }
            @SuppressWarnings("unchecked")
            List<Map<String, Object>> list = (List<Map<String, Object>>) group.get("rows");
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("id", row.getId());
            item.put("eventType", row.getEventType());
            item.put("panelName", row.getPanelName());
            item.put("panelCode", row.getPanelCode());
            item.put("actionName", row.getActionName());
            item.put("docNo", row.getDocNo());
            item.put("detail", row.getDetail());
            item.put("ip", row.getIp());
            item.put("createdAt", row.getCreateTime() == null ? null : row.getCreateTime().toString());
            list.add(item);
        }
        List<Map<String, Object>> groups = new ArrayList<>(byUser.values());
        for (Map<String, Object> g : groups) {
            @SuppressWarnings("unchecked")
            List<Map<String, Object>> list = (List<Map<String, Object>>) g.get("rows");
            g.put("total", list.size());
        }

        Map<String, Object> out = new LinkedHashMap<>();
        out.put("total", rows.size());
        out.put("truncated", rows.size() >= cap);
        out.put("groups", groups);
        return ApiResult.ok(out);
    }

    @GetMapping("/logs")
    public ApiResult<List<SysUsageLog>> logs(Authentication auth,
                                             @RequestParam(defaultValue = "100") int limit,
                                             @RequestParam(required = false) String userName,
                                             @RequestParam(required = false) String eventType) {
        if (auth == null || !roleService.isAdminUser(auth.getName())) {
            return ApiResult.fail(403, "仅管理员可操作");
        }
        LambdaQueryWrapper<SysUsageLog> q = new LambdaQueryWrapper<>();
        if (userName != null && !userName.isBlank()) q.like(SysUsageLog::getUserName, userName.trim());
        if (eventType != null && !eventType.isBlank()) q.eq(SysUsageLog::getEventType, eventType.trim());
        q.orderByDesc(SysUsageLog::getId).last("limit " + Math.max(1, Math.min(limit, 500)));
        return ApiResult.ok(usageMapper.selectList(q));
    }
}
