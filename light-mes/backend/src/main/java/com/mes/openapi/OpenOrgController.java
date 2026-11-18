package com.mes.openapi;

import java.util.Map;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import com.mes.dto.ApiResult;

import jakarta.servlet.http.HttpServletRequest;

/**
 * 组织架构开放接口（只读）：部门树 + 每个人的部门归属与岗位。
 *
 * <p>给 AI 助理的「人力地图」用。安全边界与 {@link OpenApprovalController}、
 * {@link OpenBizController} 完全一致：只有 GET、令牌走 {@code X-Open-Api-Token}、
 * 由 {@link OpenApiGuard} 做 IP 白名单与限流；没配令牌即整体关闭（失败即拒绝）。
 *
 * <p>只暴露组织关系（部门、岗位、启用状态），不返回手机号、口令、角色权限明细等
 * 与「人和部门在哪」无关的字段。
 */
@RestController
@RequestMapping("/api/open/org")
public class OpenOrgController {

    private static final Logger log = LoggerFactory.getLogger(OpenOrgController.class);

    private final OpenApiGuard guard;
    private final OpenOrgService service;

    public OpenOrgController(OpenApiGuard guard, OpenOrgService service) {
        this.guard = guard;
        this.service = service;
    }

    @GetMapping("/map")
    public ApiResult<Map<String, Object>> map(
            @RequestHeader(name = OpenApiGuard.HEADER, required = false) String token,
            HttpServletRequest request) {
        String ip = request.getRemoteAddr();
        guard.check(token, ip);
        Map<String, Object> body = service.orgMap();
        log.info("open-org map from {}", ip);
        return ApiResult.ok(body);
    }
}
