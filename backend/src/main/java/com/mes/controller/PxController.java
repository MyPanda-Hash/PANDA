package com.mes.controller;

import com.mes.dto.ApiResult;
import com.mes.panel.PanelRuntimeService;
import com.mes.service.RoleService;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;
import java.util.Set;

@RestController
@RequestMapping("/api/px")
public class PxController {

    /** 审批决定类按钮：审批流面板需要 can_approve 权限（非审批流面板无审批环节，按可见权限即可） */
    private static final Set<String> APPROVAL_DECISION_BUTTONS =
            Set.of("审批通过", "审批驳回", "驳回审批", "弃审", "反审批", "取消审批");

    private final PanelRuntimeService service;
    private final RoleService roleService;

    public PxController(PanelRuntimeService service, RoleService roleService) {
        this.service = service;
        this.roleService = roleService;
    }

    // ---------- 服务端权限校验（此前仅靠前端过滤，任何登录用户可越权读写任意面板） ----------

    private Map<String, Object> perms(Authentication auth) {
        if (auth == null || auth.getName() == null || auth.getName().isBlank()) {
            throw new IllegalStateException("未登录或会话已失效");
        }
        return roleService.getPerms(auth.getName());
    }

    private void requireVisible(Authentication auth, String panelCode) {
        Map<String, Object> p = perms(auth);
        if (Boolean.TRUE.equals(p.get("isAdmin"))) return;
        @SuppressWarnings("unchecked")
        List<String> visible = (List<String>) p.getOrDefault("visiblePanels", List.of());
        if (!visible.contains(panelCode)) throw new IllegalStateException("无权访问面板：" + panelCode);
    }

    private void requireApproveIfDecision(Authentication auth, String panelCode, String buttonName) {
        if (buttonName == null || !APPROVAL_DECISION_BUTTONS.contains(buttonName)) return;
        // approvePanels 只包含有审批流的面板；非审批流面板的「弃审/取消审批」等属于普通状态操作，
        // 若按 approvePanels 拦截将导致只有 admin 能操作，故此处先判断面板是否走审批流
        boolean workflowPanel = roleService.listPanels().stream().anyMatch(p ->
                panelCode.equals(String.valueOf(p.get("panelCode"))) && Boolean.TRUE.equals(p.get("hasApproval")));
        if (!workflowPanel) return;
        Map<String, Object> p = perms(auth);
        if (Boolean.TRUE.equals(p.get("isAdmin"))) return;
        @SuppressWarnings("unchecked")
        List<String> approve = (List<String>) p.getOrDefault("approvePanels", List.of());
        if (!approve.contains(panelCode)) throw new IllegalStateException("无权在面板执行审批操作：" + panelCode);
    }

    // ---------- 面板接口 ----------

    @GetMapping("/getPanelConfig")
    public ApiResult<Map<String, Object>> getPanelConfig(@RequestParam String panelCode,
                                                         Authentication authentication) {
        // 服务端权限兜底：不可见面板直接拒绝，避免仅靠前端过滤
        requireVisible(authentication, panelCode);
        // 叠加登录用户的表格列定制（px_column_pref：顺序/显隐/别名；未认证不叠加）
        String userName = authentication != null ? authentication.getName() : null;
        return ApiResult.ok(service.getPanelConfig(panelCode, userName));
    }

    @GetMapping("/getPermMatrix")
    public ApiResult<Map<String, Object>> getPermMatrix(@RequestParam String panelCode,
                                                        Authentication authentication) {
        requireVisible(authentication, panelCode);
        // 按登录用户权限过滤按钮（admin/未认证不过滤，保持全真）
        String userName = authentication != null ? authentication.getName() : null;
        return ApiResult.ok(service.getPermMatrix(panelCode, userName));
    }

    @GetMapping("/getNewFormPermMatrix")
    public ApiResult<Map<String, Object>> getNewFormPermMatrix(Authentication auth, @RequestParam String panelCode,
                                                               @RequestParam(required = false) String operationName) {
        requireVisible(auth, panelCode);
        return ApiResult.ok(service.getNewFormPermMatrix(panelCode, operationName));
    }

    @GetMapping("/getFormDescriptor")
    public ApiResult<Map<String, Object>> getFormDescriptor(Authentication auth, @RequestParam String panelCode,
                                                            @RequestParam String code) {
        requireVisible(auth, panelCode);
        return ApiResult.ok(service.getFormDescriptor(panelCode, code));
    }

    @PostMapping("/queryFormDataList")
    public ApiResult<Map<String, Object>> queryFormDataList(Authentication auth, @RequestBody Map<String, Object> body) {
        String panelCode = String.valueOf(body.getOrDefault("panelCode", ""));
        requireVisible(auth, panelCode);
        String keyword = body.get("keyword") == null ? null : String.valueOf(body.get("keyword"));
        int pageNo = body.get("pageNo") == null ? 1 : Integer.parseInt(String.valueOf(body.get("pageNo")));
        int pageSize = body.get("pageSize") == null ? 20 : Integer.parseInt(String.valueOf(body.get("pageSize")));
        @SuppressWarnings("unchecked")
        Map<String, Object> condition = (Map<String, Object>) body.getOrDefault("condition", Map.of());
        return ApiResult.ok(service.queryFormDataList(panelCode, keyword, condition, pageNo, pageSize));
    }

    @GetMapping("/getApprovalHistory")
    public ApiResult<List<Map<String, Object>>> getApprovalHistory(Authentication auth, @RequestParam String panelCode,
                                                                   @RequestParam String code) {
        requireVisible(auth, panelCode);
        return ApiResult.ok(service.getApprovalHistory(panelCode, code));
    }

    @PostMapping("/callButton")
    public ApiResult<Map<String, Object>> callButton(Authentication auth, @RequestBody Map<String, Object> body) {
        String panelCode = String.valueOf(body.getOrDefault("panelCode", ""));
        String buttonName = String.valueOf(body.getOrDefault("buttonName", ""));
        requireVisible(auth, panelCode);
        requireApproveIfDecision(auth, panelCode, buttonName);
        @SuppressWarnings("unchecked")
        Map<String, Object> formData = (Map<String, Object>) body.getOrDefault("formData", Map.of());
        @SuppressWarnings("unchecked")
        Map<String, Object> buttonParam = (Map<String, Object>) body.getOrDefault("buttonParam", Map.of());
        return ApiResult.ok(service.callButton(panelCode, buttonName, formData, buttonParam));
    }

    @PostMapping("/deleteForms")
    public ApiResult<Void> deleteForms(Authentication auth, @RequestBody Map<String, Object> body) {
        String panelCode = String.valueOf(body.getOrDefault("panelCode", ""));
        requireVisible(auth, panelCode);
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

    /**
     * 业务流程追踪：给定任意单号（销售订单号/加工单号/入库单号等），
     * 正反向找全相关单据并判定每一步状态与卡点。
     */
    @GetMapping("/trace")
    public ApiResult<Map<String, Object>> trace(@RequestParam String docNo) {
        return ApiResult.ok(service.traceProcess(docNo));
    }
}
