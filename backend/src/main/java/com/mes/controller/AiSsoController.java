package com.mes.controller;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.mes.dto.ApiResult;
import com.mes.entity.SysUser;
import com.mes.mapper.SysUserMapper;
import com.mes.service.AiSsoService;
import com.mes.service.RoleService;
import org.springframework.http.HttpStatus;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * AI 数字员工入口：给当前登录账号签一次票据，前端拿 url 新标签跳转即免密进场。
 *
 * <p>未配置 MES_AI_SSO_SECRET 时返回 503，前端回落到「只打开对话页」。
 */
@RestController
@RequestMapping("/api/ai")
public class AiSsoController {

    private final AiSsoService aiSsoService;
    private final SysUserMapper userMapper;
    private final RoleService roleService;

    public AiSsoController(AiSsoService aiSsoService, SysUserMapper userMapper, RoleService roleService) {
        this.aiSsoService = aiSsoService;
        this.userMapper = userMapper;
        this.roleService = roleService;
    }

    @GetMapping("/sso-ticket")
    public ApiResult<Map<String, Object>> ssoTicket(Authentication auth) {
        String userName = auth == null ? null : auth.getName();
        if (userName == null || userName.isBlank()) {
            throw new AccessDeniedException("未登录，无法进入 AI 数字员工");
        }
        if (!aiSsoService.enabled()) {
            throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "未配置 AI 单点登录密钥");
        }
        SysUser user = userMapper.selectOne(
                new LambdaQueryWrapper<SysUser>().eq(SysUser::getUserName, userName));
        boolean admin = roleService.isAdminUser(userName);
        String realName = user == null || user.getRealName() == null || user.getRealName().isBlank()
                ? userName
                : user.getRealName();

        String ticket = aiSsoService.issueTicket(userName, realName, admin, Instant.now());
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("ticket", ticket);
        data.put("url", aiSsoService.ssoUrl(ticket));
        return ApiResult.ok(data);
    }
}
