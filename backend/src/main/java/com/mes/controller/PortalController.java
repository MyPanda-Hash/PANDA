package com.mes.controller;

import com.mes.dto.ApiResult;
import com.mes.service.PortalNotificationService;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/portal")
public class PortalController {

    private final PortalNotificationService notificationService;

    public PortalController(PortalNotificationService notificationService) {
        this.notificationService = notificationService;
    }

    @GetMapping("/badge")
    public ApiResult<Map<String, Integer>> badge(Authentication authentication) {
        return ApiResult.ok(notificationService.badge(authentication.getName()));
    }

    private static final Map<String, List<Map<String, Object>>> NOTICES = buildNotices();

    private static Map<String, List<Map<String, Object>>> buildNotices() {
        Map<String, List<Map<String, Object>>> m = new LinkedHashMap<>();
        m.put("notice", List.of(
            notice(1, "notice", "轻MES v0.2 更新公告", "2026-08-13 09:00", false, "本次更新：新增单据查询/新增单据快捷入口、界面设置与工作台设置、消息通知中心。"),
            notice(2, "notice", "生产加工单模块即将上线", "2026-08-12 15:30", true, "生产加工单（建表 → 后端 CRUD → 前端列表+表单页）将作为第一个真实业务模块开发。")
        ));
        return m;
    }

    private static Map<String, Object> notice(long id, String type, String title, String time, boolean read, String content) {
        Map<String, Object> n = new LinkedHashMap<>();
        n.put("id", id);
        n.put("type", type);
        n.put("title", title);
        n.put("time", time);
        n.put("read", read);
        n.put("content", content);
        return n;
    }

    @GetMapping("/notice/list")
    public ApiResult<List<Map<String, Object>>> noticeList(
            @RequestParam(defaultValue = "todo") String type, Authentication authentication) {
        if ("notice".equals(type)) return ApiResult.ok(NOTICES.getOrDefault(type, List.of()));
        return ApiResult.ok(notificationService.list(authentication.getName(), type));
    }
}
