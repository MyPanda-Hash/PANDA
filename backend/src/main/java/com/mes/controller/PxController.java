package com.mes.controller;

import com.mes.dto.ApiResult;
import com.mes.panel.PanelRuntimeService;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/px")
public class PxController {

    private final PanelRuntimeService service;

    public PxController(PanelRuntimeService service) {
        this.service = service;
    }

    @GetMapping("/getPanelConfig")
    public ApiResult<Map<String, Object>> getPanelConfig(@RequestParam String panelCode,
                                                         Authentication authentication) {
        // 叠加登录用户的表格列定制（px_column_pref：顺序/显隐/别名；未认证不叠加）
        String userName = authentication != null ? authentication.getName() : null;
        return ApiResult.ok(service.getPanelConfig(panelCode, userName));
    }

    @GetMapping("/getPermMatrix")
    public ApiResult<Map<String, Object>> getPermMatrix(@RequestParam String panelCode,
                                                        Authentication authentication) {
        // 按登录用户权限过滤按钮（admin/未认证不过滤，保持全真）
        String userName = authentication != null ? authentication.getName() : null;
        return ApiResult.ok(service.getPermMatrix(panelCode, userName));
    }

    @GetMapping("/getNewFormPermMatrix")
    public ApiResult<Map<String, Object>> getNewFormPermMatrix(@RequestParam String panelCode,
                                                               @RequestParam(required = false) String operationName) {
        return ApiResult.ok(service.getNewFormPermMatrix(panelCode, operationName));
    }

    @GetMapping("/getFormDescriptor")
    public ApiResult<Map<String, Object>> getFormDescriptor(@RequestParam String panelCode,
                                                            @RequestParam String code) {
        return ApiResult.ok(service.getFormDescriptor(panelCode, code));
    }

    @PostMapping("/queryFormDataList")
    public ApiResult<Map<String, Object>> queryFormDataList(@RequestBody Map<String, Object> body) {
        String panelCode = String.valueOf(body.getOrDefault("panelCode", ""));
        String keyword = body.get("keyword") == null ? null : String.valueOf(body.get("keyword"));
        int pageNo = body.get("pageNo") == null ? 1 : Integer.parseInt(String.valueOf(body.get("pageNo")));
        int pageSize = body.get("pageSize") == null ? 20 : Integer.parseInt(String.valueOf(body.get("pageSize")));
        @SuppressWarnings("unchecked")
        Map<String, Object> condition = (Map<String, Object>) body.getOrDefault("condition", Map.of());
        return ApiResult.ok(service.queryFormDataList(panelCode, keyword, condition, pageNo, pageSize));
    }

    @GetMapping("/getApprovalHistory")
    public ApiResult<List<Map<String, Object>>> getApprovalHistory(@RequestParam String panelCode,
                                                                   @RequestParam String code) {
        return ApiResult.ok(service.getApprovalHistory(panelCode, code));
    }

    @PostMapping("/callButton")
    public ApiResult<Map<String, Object>> callButton(@RequestBody Map<String, Object> body) {
        String panelCode = String.valueOf(body.getOrDefault("panelCode", ""));
        String buttonName = String.valueOf(body.getOrDefault("buttonName", ""));
        @SuppressWarnings("unchecked")
        Map<String, Object> formData = (Map<String, Object>) body.getOrDefault("formData", Map.of());
        @SuppressWarnings("unchecked")
        Map<String, Object> buttonParam = (Map<String, Object>) body.getOrDefault("buttonParam", Map.of());
        return ApiResult.ok(service.callButton(panelCode, buttonName, formData, buttonParam));
    }

    @PostMapping("/deleteForms")
    public ApiResult<Void> deleteForms(@RequestBody Map<String, Object> body) {
        String panelCode = String.valueOf(body.getOrDefault("panelCode", ""));
        @SuppressWarnings("unchecked")
        List<String> rowCodes = (List<String>) body.getOrDefault("rowCodes", List.of());
        service.deleteForms(panelCode, rowCodes);
        return ApiResult.ok(null);
    }

    /** 保存登录用户的表格列定制（columns 空数组=恢复默认） */
    @PostMapping("/saveColumnPrefs")
    public ApiResult<Void> saveColumnPrefs(@RequestBody Map<String, Object> body,
                                           Authentication authentication) {
        String panelCode = String.valueOf(body.getOrDefault("panelCode", ""));
        String userName = authentication != null ? authentication.getName() : null;
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> columns = (List<Map<String, Object>>) body.getOrDefault("columns", List.of());
        service.saveColumnPrefs(panelCode, userName, columns);
        return ApiResult.ok(null);
    }
}
