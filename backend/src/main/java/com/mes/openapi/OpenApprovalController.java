package com.mes.openapi;

import java.util.LinkedHashMap;
import java.util.List;
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

import jakarta.servlet.http.HttpServletRequest;

/**
 * 开放审批接口（只读）。
 *
 * <p>安全边界：
 * <ul>
 *   <li>只有 GET；本控制器不注册任何写方法，天然无法篡改数据。</li>
 *   <li>令牌走 {@code X-Open-Api-Token} 头，由 {@link OpenApiGuard} 校验（含 IP 白名单与限流）。</li>
 *   <li>返回字段由 {@link OpenApprovalService#PENDING_FIELDS} 白名单固定，不含单据正文。</li>
 * </ul>
 */
@RestController
@RequestMapping("/api/open/approvals")
public class OpenApprovalController {

    private static final Logger log = LoggerFactory.getLogger(OpenApprovalController.class);

    private final OpenApiGuard guard;
    private final OpenApprovalService service;

    public OpenApprovalController(OpenApiGuard guard, OpenApprovalService service) {
        this.guard = guard;
        this.service = service;
    }

    @GetMapping("/approvers")
    public ApiResult<List<Map<String, Object>>> approvers(
            @RequestHeader(name = OpenApiGuard.HEADER, required = false) String token,
            HttpServletRequest request) {
        guard.check(token, request.getRemoteAddr());
        List<Map<String, Object>> rows = service.listApprovers();
        log.info("open-api approvers from {} -> {}", request.getRemoteAddr(), rows.size());
        return ApiResult.ok(rows);
    }

    @GetMapping("/pending")
    public ApiResult<Map<String, Object>> pending(
            @RequestHeader(name = OpenApiGuard.HEADER, required = false) String token,
            @RequestParam String approver,
            @RequestParam(required = false) Integer limit,
            HttpServletRequest request) {
        guard.check(token, request.getRemoteAddr());
        List<Map<String, Object>> rows = service.pendingFor(approver, limit);
        log.info("open-api pending approver={} from {} -> {}", approver, request.getRemoteAddr(), rows.size());
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("approver", approver);
        body.put("fields", OpenApprovalService.PENDING_FIELDS);
        body.put("items", rows);
        return ApiResult.ok(body);
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
