package com.mes.controller;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.mes.dto.ApiResult;
import com.mes.entity.SysUser;
import com.mes.mapper.SysUserMapper;
import com.mes.service.AiBriefService;
import com.mes.service.AiSsoService;
import com.mes.service.RoleService;
import org.springframework.http.HttpStatus;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * AI 助理入口：给当前登录账号签一次票据，前端拿 url 新标签跳转即免密进场。
 *
 * <p>未配置 MES_AI_SSO_SECRET 时返回 503，前端回落到「只打开对话页」。
 */
@RestController
@RequestMapping("/api/ai")
public class AiSsoController {

    private final AiSsoService aiSsoService;
    private final AiBriefService aiBriefService;
    private final SysUserMapper userMapper;
    private final RoleService roleService;

    public AiSsoController(AiSsoService aiSsoService, AiBriefService aiBriefService,
                           SysUserMapper userMapper, RoleService roleService) {
        this.aiSsoService = aiSsoService;
        this.aiBriefService = aiBriefService;
        this.userMapper = userMapper;
        this.roleService = roleService;
    }

    /**
     * 我的桌面「AI 助理」卡片的数据：今日简报摘要（结论 + 四个数字 + 待拍板）。
     *
     * <p>AI 侧不可用时返回 available=false，桌面据此显示降级文案，不报错。
     */
    @GetMapping("/brief")
    public ApiResult<Map<String, Object>> brief(Authentication auth) {
        String userName = auth == null ? null : auth.getName();
        if (userName == null || userName.isBlank()) {
            throw new AccessDeniedException("未登录，无法读取 AI 助理简报");
        }
        SysUser user = userMapper.selectOne(
                new LambdaQueryWrapper<SysUser>().eq(SysUser::getUserName, userName));
        String realName = user == null || user.getRealName() == null || user.getRealName().isBlank()
                ? userName
                : user.getRealName();
        boolean admin = roleService.isAdminUser(userName);
        return ApiResult.ok(aiBriefService.brief(userName, realName, admin));
    }

    /**
     * 数据看板的「AI 数据分析」：前端把看板指标传上来，后端转给 AI 助理分析。
     *
     * <p>为什么指标由前端传：分析对象必须是用户屏幕上那一份数，
     * 后端重算会得到另一套口径，AI 的结论与界面就对不上了。
     */
    @PostMapping("/analysis")
    public ApiResult<Map<String, Object>> analysis(@RequestBody(required = false) Map<String, Object> body,
                                                   Authentication auth) {
        String userName = auth == null ? null : auth.getName();
        if (userName == null || userName.isBlank()) {
            throw new AccessDeniedException("未登录，无法使用 AI 数据分析");
        }
        SysUser user = userMapper.selectOne(
                new LambdaQueryWrapper<SysUser>().eq(SysUser::getUserName, userName));
        String realName = user == null || user.getRealName() == null || user.getRealName().isBlank()
                ? userName
                : user.getRealName();
        boolean admin = roleService.isAdminUser(userName);
        Map<String, Object> metrics = body == null ? Map.of() : body;
        @SuppressWarnings("unchecked")
        Map<String, Object> onlyMetrics = metrics.get("metrics") instanceof Map<?, ?> m
                ? (Map<String, Object>) m
                : metrics;
        return ApiResult.ok(aiBriefService.analysis(userName, realName, admin, onlyMetrics));
    }

    @GetMapping("/sso-ticket")
    public ApiResult<Map<String, Object>> ssoTicket(Authentication auth) {
        String userName = auth == null ? null : auth.getName();
        if (userName == null || userName.isBlank()) {
            throw new AccessDeniedException("未登录，无法进入 AI 助理");
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
