package com.mes.controller;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.mes.config.JwtUtil;
import com.mes.dto.ApiResult;
import com.mes.dto.LoginRequest;
import com.mes.dto.LoginResponse;
import com.mes.entity.SysUser;
import com.mes.mapper.SysUserMapper;
import com.mes.service.RoleService;
import com.mes.service.UsageLogService;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.springframework.security.core.Authentication;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.web.bind.annotation.*;

import java.util.HashMap;
import java.util.Map;

@RestController
@RequestMapping("/api/auth")
public class AuthController {

    private final SysUserMapper userMapper;
    private final PasswordEncoder passwordEncoder;
    private final JwtUtil jwtUtil;
    private final RoleService roleService;
    private final UsageLogService usageLog;

    public AuthController(SysUserMapper userMapper, PasswordEncoder passwordEncoder, JwtUtil jwtUtil,
                          RoleService roleService, UsageLogService usageLog) {
        this.userMapper = userMapper;
        this.passwordEncoder = passwordEncoder;
        this.jwtUtil = jwtUtil;
        this.roleService = roleService;
        this.usageLog = usageLog;
    }

    @PostMapping("/login")
    public ApiResult<LoginResponse> login(@Valid @RequestBody LoginRequest req, HttpServletRequest http) {
        SysUser user = userMapper.selectOne(
                new LambdaQueryWrapper<SysUser>().eq(SysUser::getUserName, req.getUserName()));
        if (user == null || !passwordEncoder.matches(req.getPassword(), user.getPassword())) {
            usageLog.record(req.getUserName(), "LOGIN_FAIL", "密码错误或用户不存在", clientIp(http));
            return ApiResult.fail(400, "用户名或密码错误");
        }
        if (user.getEnabled() != null && user.getEnabled() == 0) {
            usageLog.record(req.getUserName(), "LOGIN_FAIL", "账号已禁用", clientIp(http));
            return ApiResult.fail(403, "账号已被禁用");
        }
        usageLog.record(user.getUserName(), "LOGIN", "登录成功", clientIp(http));
        Map<String, Object> info = new HashMap<>();
        info.put("userName", user.getUserName());
        info.put("realName", user.getRealName());
        info.put("factoryCode", user.getFactoryCode());
        Map<String, Object> perms = roleService.getPerms(user.getUserName());
        info.put("isAdmin", perms.get("isAdmin"));
        info.put("roleCode", perms.get("roleCode"));
        info.put("visiblePanels", perms.get("visiblePanels"));
        info.put("approvePanels", perms.get("approvePanels"));
        info.put("panelPerms", perms.get("panelPerms"));
        return ApiResult.ok(new LoginResponse(jwtUtil.generateToken(user.getUserName()), info));
    }

    @GetMapping("/userinfo")
    public ApiResult<Object> userInfo(Authentication auth) {
        SysUser user = userMapper.selectOne(
                new LambdaQueryWrapper<SysUser>().eq(SysUser::getUserName, auth.getName()));
        Map<String, Object> info = new HashMap<>();
        info.put("userName", user.getUserName());
        info.put("realName", user.getRealName());
        info.put("factoryCode", user.getFactoryCode());
        Map<String, Object> perms = roleService.getPerms(user.getUserName());
        info.put("isAdmin", perms.get("isAdmin"));
        info.put("roleCode", perms.get("roleCode"));
        info.put("visiblePanels", perms.get("visiblePanels"));
        info.put("approvePanels", perms.get("approvePanels"));
        info.put("panelPerms", perms.get("panelPerms"));
        return ApiResult.ok(info);
    }

    /** 取真实客户端 IP：优先 X-Forwarded-For（nginx 反代） */
    static String clientIp(HttpServletRequest req) {
        if (req == null) return null;
        String xff = req.getHeader("X-Forwarded-For");
        if (xff != null && !xff.isBlank()) {
            int comma = xff.indexOf(',');
            return (comma > 0 ? xff.substring(0, comma) : xff).trim();
        }
        return req.getRemoteAddr();
    }

    /** 当前登录用户的权限：isAdmin / visiblePanels（可见面板）/ approvePanels（可审批面板） */
    @GetMapping("/perms")
    public ApiResult<Map<String, Object>> perms(Authentication auth) {
        String name = auth != null ? auth.getName() : "";
        return ApiResult.ok(roleService.getPerms(name));
    }
}
