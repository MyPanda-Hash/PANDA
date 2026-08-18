package com.mes.controller;

import com.mes.dto.ApiResult;
import com.mes.entity.SysRole;
import com.mes.service.RoleService;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

/** 组织架构-角色管理（仅管理员可访问） */
@RestController
@RequestMapping("/api/sys/role")
public class SysRoleController {

    private final RoleService roleService;

    public SysRoleController(RoleService roleService) {
        this.roleService = roleService;
    }

    private void guard(Authentication auth) {
        if (auth == null || !roleService.isAdminUser(auth.getName())) {
            throw new IllegalStateException("仅管理员可访问组织架构");
        }
    }

    @GetMapping("/list")
    public ApiResult<List<SysRole>> list(Authentication auth) {
        guard(auth);
        return ApiResult.ok(roleService.listRoles());
    }

    @PostMapping("/save")
    public ApiResult<SysRole> save(@RequestBody SysRole role, Authentication auth) {
        guard(auth);
        return ApiResult.ok(roleService.saveRole(role));
    }

    @DeleteMapping("/{id}")
    public ApiResult<Void> delete(@PathVariable Long id, Authentication auth) {
        guard(auth);
        roleService.deleteRole(id);
        return ApiResult.ok(null);
    }

    @GetMapping("/{id}/panels")
    public ApiResult<Map<String, Object>> panels(@PathVariable Long id, Authentication auth) {
        guard(auth);
        return ApiResult.ok(roleService.getRolePanels(id));
    }

    @PostMapping("/{id}/panels")
    public ApiResult<Void> savePanels(@PathVariable Long id,
                                      @RequestBody Map<String, Object> body, Authentication auth) {
        guard(auth);
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> panels = (List<Map<String, Object>>) body.getOrDefault("panels", List.of());
        roleService.saveRolePanels(id, panels);
        return ApiResult.ok(null);
    }
}