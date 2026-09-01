package com.mes.controller;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.mes.config.JwtUtil;
import com.mes.dto.ApiResult;
import com.mes.dto.LoginRequest;
import com.mes.dto.LoginResponse;
import com.mes.entity.SysUser;
import com.mes.mapper.SysUserMapper;
import com.mes.service.RoleService;
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

    public AuthController(SysUserMapper userMapper, PasswordEncoder passwordEncoder, JwtUtil jwtUtil, RoleService roleService) {
        this.userMapper = userMapper;
        this.passwordEncoder = passwordEncoder;
        this.jwtUtil = jwtUtil;
        this.roleService = roleService;
    }

    @PostMapping("/login")
    public ApiResult<LoginResponse> login(@Valid @RequestBody LoginRequest req) {
        SysUser user = userMapper.selectOne(
                new LambdaQueryWrapper<SysUser>().eq(SysUser::getUserName, req.getUserName()));
        if (user == null || !passwordEncoder.matches(req.getPassword(), user.getPassword())) {
            return ApiResult.fail(400, "用户名或密码错误");
        }
        if (user.getEnabled() != null && user.getEnabled() == 0) {
            return ApiResult.fail(403, "账号已被禁用");
        }
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

    /** 当前登录用户的权限：isAdmin / visiblePanels（可见面板）/ approvePanels（可审批面板） */
    @GetMapping("/perms")
    public ApiResult<Map<String, Object>> perms(Authentication auth) {
        String name = auth != null ? auth.getName() : "";
        return ApiResult.ok(roleService.getPerms(name));
    }
}
