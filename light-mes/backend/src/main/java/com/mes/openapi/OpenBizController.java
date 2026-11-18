package com.mes.openapi;

import java.util.LinkedHashMap;
import java.util.Map;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import com.mes.dto.ApiResult;
import com.mes.service.UsageLogService;

import jakarta.servlet.http.HttpServletRequest;

/**
 * 开放业务接口（只读）：单据追踪 / 单据详情 / 库存结存。
 *
 * <p>安全边界与 {@link OpenApprovalController} 一致：只有 GET、令牌走
 * {@code X-Open-Api-Token}（{@link OpenApiGuard} 做 IP 白名单与限流）、字段按面板配置白名单。
 *
 * <p>每次调用写一条 {@code sys_usage_log}（事件类型 {@code OPEN_AI}），
 * 端到端可追「哪个账号 / 哪张单 / 什么时候被问过」。
 */
@RestController
@RequestMapping("/api/open/biz")
public class OpenBizController {

    private static final Logger log = LoggerFactory.getLogger(OpenBizController.class);
    private static final String EVENT = "OPEN_AI";

    private final OpenApiGuard guard;
    private final OpenBizService service;
    private final UsageLogService usageLog;

    public OpenBizController(OpenApiGuard guard, OpenBizService service, UsageLogService usageLog) {
        this.guard = guard;
        this.service = service;
        this.usageLog = usageLog;
    }

    @GetMapping("/trace")
    public ApiResult<Map<String, Object>> trace(
            @RequestHeader(name = OpenApiGuard.HEADER, required = false) String token,
            @RequestHeader(name = "X-Open-Api-User", required = false) String headerUser,
            @RequestParam(required = false) String by,
            @RequestParam String docNo,
            HttpServletRequest request) {
        String ip = request.getRemoteAddr();
        guard.check(token, ip);
        Map<String, Object> body = service.trace(docNo);
        audit(by, headerUser, "trace " + docNo, ip);
        log.info("open-biz trace {} by {} from {}", docNo, user(by, headerUser), ip);
        return ApiResult.ok(body);
    }

    @GetMapping("/form")
    public ApiResult<Map<String, Object>> form(
            @RequestHeader(name = OpenApiGuard.HEADER, required = false) String token,
            @RequestHeader(name = "X-Open-Api-User", required = false) String headerUser,
            @RequestParam(required = false) String by,
            @RequestParam String panelCode,
            @RequestParam String formNo,
            HttpServletRequest request) {
        String ip = request.getRemoteAddr();
        guard.check(token, ip);
        Map<String, Object> body = service.form(panelCode, formNo);
        audit(by, headerUser, "form " + panelCode + " " + formNo, ip);
        log.info("open-biz form {} {} by {} from {}", panelCode, formNo, user(by, headerUser), ip);
        return ApiResult.ok(body);
    }

    @GetMapping("/stock")
    public ApiResult<Map<String, Object>> stock(
            @RequestHeader(name = OpenApiGuard.HEADER, required = false) String token,
            @RequestHeader(name = "X-Open-Api-User", required = false) String headerUser,
            @RequestParam(required = false) String by,
            @RequestParam(required = false) String itemCode,
            @RequestParam(required = false) String warehouse,
            @RequestParam(required = false) Integer limit,
            HttpServletRequest request) {
        String ip = request.getRemoteAddr();
        guard.check(token, ip);
        Map<String, Object> body = service.stock(itemCode, warehouse, limit);
        audit(by, headerUser, "stock " + (itemCode == null ? "" : itemCode), ip);
        log.info("open-biz stock item={} wh={} by {} from {}", itemCode, warehouse, user(by, headerUser), ip);
        return ApiResult.ok(body);
    }

    private void audit(String by, String headerUser, String detail, String ip) {
        usageLog.record(user(by, headerUser), EVENT, detail, ip);
    }

    private static String user(String by, String headerUser) {
        String value = by != null && !by.isBlank() ? by : headerUser;
        return value == null || value.isBlank() ? "ai" : value.trim();
    }

    @ExceptionHandler(OpenApiException.class)
    public ResponseEntity<Map<String, Object>> onGuardFailure(OpenApiException error) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("code", error.getStatus());
        body.put("message", error.getMessage());
        body.put("data", null);
        return ResponseEntity.status(HttpStatus.valueOf(error.getStatus())).body(body);
    }
}
