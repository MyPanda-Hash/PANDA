package com.mes.controller;

import com.mes.dto.ApiResult;
import com.mes.entity.ManuOrder;
import com.mes.service.ManuOrderService;
import com.mes.service.RoleService;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

@RestController
@RequestMapping("/api/manu/order")
public class ManuOrderController {

    private final ManuOrderService service;
    private final RoleService roleService;

    public ManuOrderController(ManuOrderService service, RoleService roleService) {
        this.service = service;
        this.roleService = roleService;
    }

    @GetMapping("/page")
    public ApiResult<Map<String, Object>> page(
            @RequestParam(defaultValue = "1") int pageNo,
            @RequestParam(defaultValue = "20") int pageSize,
            @RequestParam(required = false) String orderNo,
            @RequestParam(required = false) String ingotNo,
            @RequestParam(required = false) Integer status,
            @RequestParam(required = false) String factoryCode,
            Authentication auth) {
        requirePermission(auth, "view");
        return ApiResult.ok(service.pageList(pageNo, pageSize, orderNo, ingotNo, status, factoryCode));
    }

    @GetMapping("/{id}")
    public ApiResult<ManuOrder> detail(@PathVariable Long id, Authentication auth) {
        requirePermission(auth, "view");
        ManuOrder order = service.getDetail(id);
        if (order == null) throw new IllegalArgumentException("单据不存在");
        return ApiResult.ok(order);
    }

    @PostMapping
    public ApiResult<ManuOrder> create(@RequestBody ManuOrder order, Authentication auth) {
        requirePermission(auth, "add");
        return ApiResult.ok(service.create(order, currentUser()));
    }

    @PutMapping("/{id}")
    public ApiResult<ManuOrder> update(@PathVariable Long id, @RequestBody ManuOrder order, Authentication auth) {
        requirePermission(auth, "edit");
        return ApiResult.ok(service.update(id, order, currentUser()));
    }

    @DeleteMapping("/{id}")
    public ApiResult<Void> delete(@PathVariable Long id, Authentication auth) {
        requirePermission(auth, "delete");
        service.delete(id);
        return ApiResult.ok(null);
    }

    @PostMapping("/{id}/audit")
    public ApiResult<ManuOrder> audit(@PathVariable Long id, Authentication auth) {
        requirePermission(auth, "audit");
        return ApiResult.ok(service.audit(id, currentUser()));
    }

    @PostMapping("/{id}/unaudit")
    public ApiResult<ManuOrder> unaudit(@PathVariable Long id, Authentication auth) {
        requirePermission(auth, "audit");
        return ApiResult.ok(service.unaudit(id));
    }

    @PostMapping("/{id}/close")
    public ApiResult<ManuOrder> close(@PathVariable Long id, Authentication auth) {
        requirePermission(auth, "edit");
        return ApiResult.ok(service.close(id));
    }

    private void requirePermission(Authentication auth, String permission) {
        if (auth == null || auth.getName() == null || auth.getName().isBlank()) {
            throw new AccessDeniedException("请先登录");
        }
        Map<String, Object> permissions = roleService.getPerms(auth.getName());
        if (Boolean.TRUE.equals(permissions.get("isAdmin"))) return;
        Object panelPermsObject = permissions.get("panelPerms");
        if (panelPermsObject instanceof Map<?, ?> panelPerms
                && panelPerms.get("MANU_ORDER") instanceof Iterable<?> granted) {
            for (Object code : granted) {
                if (permission.equals(String.valueOf(code))) return;
            }
        }
        throw new AccessDeniedException("当前用户没有生产加工单的 " + permission + " 权限");
    }

    private String currentUser() {
        var auth = SecurityContextHolder.getContext().getAuthentication();
        return auth != null ? auth.getName() : "system";
    }
}
