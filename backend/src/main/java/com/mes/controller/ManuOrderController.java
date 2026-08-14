package com.mes.controller;

import com.mes.dto.ApiResult;
import com.mes.entity.ManuOrder;
import com.mes.service.ManuOrderService;
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

    public ManuOrderController(ManuOrderService service) {
        this.service = service;
    }

    @GetMapping("/page")
    public ApiResult<Map<String, Object>> page(
            @RequestParam(defaultValue = "1") int pageNo,
            @RequestParam(defaultValue = "20") int pageSize,
            @RequestParam(required = false) String orderNo,
            @RequestParam(required = false) String ingotNo,
            @RequestParam(required = false) Integer status,
            @RequestParam(required = false) String factoryCode) {
        return ApiResult.ok(service.pageList(pageNo, pageSize, orderNo, ingotNo, status, factoryCode));
    }

    @GetMapping("/{id}")
    public ApiResult<ManuOrder> detail(@PathVariable Long id) {
        ManuOrder order = service.getDetail(id);
        if (order == null) throw new IllegalArgumentException("单据不存在");
        return ApiResult.ok(order);
    }

    @PostMapping
    public ApiResult<ManuOrder> create(@RequestBody ManuOrder order) {
        return ApiResult.ok(service.create(order, currentUser()));
    }

    @PutMapping("/{id}")
    public ApiResult<ManuOrder> update(@PathVariable Long id, @RequestBody ManuOrder order) {
        return ApiResult.ok(service.update(id, order, currentUser()));
    }

    @DeleteMapping("/{id}")
    public ApiResult<Void> delete(@PathVariable Long id) {
        service.delete(id);
        return ApiResult.ok(null);
    }

    @PostMapping("/{id}/audit")
    public ApiResult<ManuOrder> audit(@PathVariable Long id) {
        return ApiResult.ok(service.audit(id, currentUser()));
    }

    @PostMapping("/{id}/unaudit")
    public ApiResult<ManuOrder> unaudit(@PathVariable Long id) {
        return ApiResult.ok(service.unaudit(id));
    }

    @PostMapping("/{id}/close")
    public ApiResult<ManuOrder> close(@PathVariable Long id) {
        return ApiResult.ok(service.close(id));
    }

    private String currentUser() {
        var auth = SecurityContextHolder.getContext().getAuthentication();
        return auth != null ? auth.getName() : "system";
    }
}
