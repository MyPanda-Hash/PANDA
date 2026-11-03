package com.mes.controller;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.mes.dto.ApiResult;
import com.mes.entity.SysUsageLog;
import com.mes.mapper.SysUsageLogMapper;
import com.mes.service.RoleService;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.util.List;

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
