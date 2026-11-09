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

    /**
     * 采购发票行级选单：未开票的来源入库单/退货单明细行。
     * mode=purchase（采购入库单）| return（采购退货单）；supplier/keyword 为包含过滤。
     */
    @GetMapping("/invoiceSourceLines")
    public ApiResult<Map<String, Object>> invoiceSourceLines(Authentication auth,
                                                             @RequestParam(defaultValue = "purchase") String mode,
                                                             @RequestParam(required = false) String supplier,
                                                             @RequestParam(required = false) String keyword) {
        requireVisible(auth, "PU_INVOICE");
        return ApiResult.ok(service.invoiceSourceLines(mode, supplier, keyword));
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

    /**
     * 生单预览：销售订单 → 生产加工单。
     * 逐行返回 需求数量 / 现存量 / 已下单生产数量 / 建议生产数量 / 是否不足，
     * 供前端弹窗勾选要投产的行与数量，避免有货的也一起投产。
     */
    @PostMapping("/previewProduce")
    public ApiResult<List<Map<String, Object>>> previewProduce(@RequestBody Map<String, Object> body) {
        String docNo = String.valueOf(body.getOrDefault("docNo", ""));
        return ApiResult.ok(service.producePreview(docNo));
    }

    /** 按排产批次取报工记录。 */
    @GetMapping("/batchReports")
    public ApiResult<List<Map<String, Object>>> batchReports(@RequestParam("dispatchNo") String dispatchNo) {
        return ApiResult.ok(service.batchReports(dispatchNo));
    }

    /** 核销弹窗：该收款/付款单可核销的往来单与未结金额。 */
    @GetMapping("/openWriteOffLines")
    public ApiResult<List<Map<String, Object>>> openWriteOffLines(
            @RequestParam("panelCode") String panelCode, @RequestParam("formNo") String formNo) {
        return ApiResult.ok(service.openWriteOffLines(panelCode, formNo));
    }

    /**
     * 出库预览：销售订单 → 销售出库单。
     * 逐行返回库存与剩余待出数量，供前端弹窗确认出库数量。
     */
    /** 完工汇总：各工序累计报工与完成状态。 */
    @GetMapping("/completionSummary")
    public ApiResult<Map<String, Object>> completionSummary(@RequestParam("manuOrderNo") String manuOrderNo) {
        return ApiResult.ok(service.completionSummary(manuOrderNo));
    }

    /** 按排产批次领料：生成材料出库单（草稿）。 */
    @PostMapping("/issueByBatch")
    public ApiResult<Map<String, Object>> issueByBatch(@RequestBody Map<String, Object> body) {
        String dispatchNo = String.valueOf(body.getOrDefault("排产单号", ""));
        String materialCode = String.valueOf(body.getOrDefault("材料编码", ""));
        double qty = parseDouble(body.get("数量"), "领料数量");
        String operator = String.valueOf(body.getOrDefault("领料人", ""));
        String warehouse = String.valueOf(body.getOrDefault("仓库", ""));
        return ApiResult.ok(service.issueByBatch(dispatchNo, materialCode, qty, operator, warehouse));
    }

    /** 按批次整批领料：一个批次的多种材料生成一张材料出库单（多行）。 */
    @PostMapping("/issueBatch")
    public ApiResult<Map<String, Object>> issueBatch(@RequestBody Map<String, Object> body) {
        String dispatchNo = String.valueOf(body.getOrDefault("排产单号", ""));
        String operator = String.valueOf(body.getOrDefault("领料人", ""));
        String warehouse = String.valueOf(body.getOrDefault("仓库", ""));
        List<Map<String, Object>> lines = new java.util.ArrayList<>();
        if (body.get("items") instanceof List<?> items) {
            for (Object o : items) {
                if (!(o instanceof Map<?, ?> raw)) continue;
                Map<String, Object> typed = new java.util.LinkedHashMap<>();
                raw.forEach((k, v) -> typed.put(String.valueOf(k), v));
                Map<String, Object> line = new java.util.LinkedHashMap<>();
                line.put("材料编码", String.valueOf(typed.getOrDefault("材料编码", "")));
                line.put("数量", parseDouble(typed.get("数量"), "领料数量"));
                lines.add(line);
            }
        }
        return ApiResult.ok(service.issueBatch(dispatchNo, lines, operator, warehouse));
    }

    /** 按排产批次的领料需求：批次需用量 / 已领 / 剩余可领。 */
    @GetMapping("/materialIssueBoard")
    public ApiResult<List<Map<String, Object>>> materialIssueBoard(@RequestParam("manuOrderNo") String manuOrderNo) {
        return ApiResult.ok(service.materialIssueBoard(manuOrderNo));
    }

    /** 按排产批次报工看板。 */
    @GetMapping("/reportBatchBoard")
    public ApiResult<List<Map<String, Object>>> reportBatchBoard() {
        return ApiResult.ok(service.reportBatchBoard());
    }

    /** 按排产批次报工：合格 + 不合格 = 报工数量。 */
    @PostMapping("/reportByBatch")
    public ApiResult<Map<String, Object>> reportByBatch(@RequestBody Map<String, Object> body) {
        String dispatchNo = String.valueOf(body.getOrDefault("排产单号", ""));
        double qualified = parseDouble(body.get("合格数量"), "合格数量");
        double rejected = parseDouble(body.get("不合格数量"), "不合格数量");
        return ApiResult.ok(service.reportByBatch(dispatchNo, qualified, rejected));
    }

    private double parseDouble(Object raw, String label) {
        if (raw == null) return 0;
        try {
            return Double.parseDouble(String.valueOf(raw).trim());
        } catch (NumberFormatException e) {
            throw new IllegalArgumentException(label + "必须是数字");
        }
    }

    /**
     * 建排产批次：一个工序分一批，可跨班组/设备。
     */
    @PostMapping("/planBatch")
    public ApiResult<Map<String, Object>> planBatch(@RequestBody Map<String, Object> body) {
        String moNo = String.valueOf(body.getOrDefault("manuOrderNo", ""));
        String procCode = String.valueOf(body.getOrDefault("工序编码", ""));
        double qty;
        try {
            qty = Double.parseDouble(String.valueOf(body.getOrDefault("数量", "0")));
        } catch (NumberFormatException e) {
            throw new IllegalArgumentException("排产数量必须是数字");
        }
        return ApiResult.ok(service.planBatch(moNo, procCode, qty,
                String.valueOf(body.getOrDefault("班组", "")),
                String.valueOf(body.getOrDefault("设备", "")),
                String.valueOf(body.getOrDefault("预开工日", "")),
                String.valueOf(body.getOrDefault("预完工日", ""))));
    }

    /**
     * 工单排产：某张加工单每道工序的可排产量与已有批次。
     */
    @GetMapping("/planBoard")
    public ApiResult<List<Map<String, Object>>> planBoard(@RequestParam("manuOrderNo") String manuOrderNo) {
        return ApiResult.ok(service.planBoard(manuOrderNo));
    }

    /**
     * 工序报工工作台：一行一道工序，带计划/已报/剩余与可否报工。
     * manuOrderNo 可选，不传返回全部已审核加工单。
     */
    @GetMapping("/processReportBoard")
    public ApiResult<List<Map<String, Object>>> processReportBoard(
            @RequestParam(value = "manuOrderNo", required = false) String manuOrderNo) {
        return ApiResult.ok(service.processReportBoard(manuOrderNo));
    }

    /**
     * 按工序报工：针对某加工单的某道工序生成一张工序汇报单（草稿）。
     */
    @PostMapping("/reportProcess")
    public ApiResult<Map<String, Object>> reportProcess(@RequestBody Map<String, Object> body) {
        String moNo = body == null ? null : String.valueOf(body.getOrDefault("加工单号", ""));
        String procCode = body == null ? null : String.valueOf(body.getOrDefault("工序编码", ""));
        double qty;
        try {
            qty = Double.parseDouble(String.valueOf(body == null ? "0" : body.getOrDefault("报工数量", "0")));
        } catch (NumberFormatException e) {
            throw new IllegalArgumentException("报工数量必须是数字");
        }
        return ApiResult.ok(service.reportProcess(moNo, procCode, qty));
    }

    /**
     * 开票工作台：以销货单为行，带出发票号与发票状态，
     * 供「列出销货单 → 选择开票 → 审批 → 打印」使用。
     */
    @GetMapping("/invoiceWorkbench")
    public ApiResult<List<Map<String, Object>>> invoiceWorkbench() {
        return ApiResult.ok(service.invoiceWorkbench());
    }

    /**
     * 开票方（销售方）信息：销售发票打印页使用。
     * 取自 application.yml 的 mes.company，可用环境变量覆盖。
     */
    @GetMapping("/companyInfo")
    public ApiResult<Map<String, Object>> companyInfo() {
        return ApiResult.ok(service.companyInfo());
    }

    /**
     * 入库预览：生产加工单 → 产成品入库单。
     * 逐行返回计划/已入库/剩余入库数量，供前端弹窗确认入库仓库与数量。
     */
    @PostMapping("/previewFinishIn")
    public ApiResult<List<Map<String, Object>>> previewFinishIn(@RequestBody Map<String, Object> body) {
        String docNo = String.valueOf(body.getOrDefault("docNo", ""));
        return ApiResult.ok(service.finishInPreview(docNo));
    }

    @PostMapping("/previewSaleOut")
    public ApiResult<List<Map<String, Object>>> previewSaleOut(@RequestBody Map<String, Object> body) {
        String docNo = String.valueOf(body.getOrDefault("docNo", ""));
        Object warehouse = body.get("warehouse");
        return ApiResult.ok(service.saleOutPreview(docNo, warehouse == null ? null : String.valueOf(warehouse)));
    }

    /** 面板自助加字段：登记一条元数据，读取出口合并进面板配置（服务端校验字段名/类型/重名 + 「修改」权限） */
    @PostMapping("/addCustomField")
    public ApiResult<Map<String, Object>> addCustomField(@RequestBody Map<String, Object> body) {
        String panelCode = String.valueOf(body.getOrDefault("panelCode", ""));
        return ApiResult.ok(service.addCustomField(panelCode, body));
    }

    /** 停用自定义字段：只删元数据，已录入数据保留（重新加同名字段即恢复显示） */
    @PostMapping("/retireCustomField")
    public ApiResult<Void> retireCustomField(@RequestBody Map<String, Object> body) {
        String panelCode = String.valueOf(body.getOrDefault("panelCode", ""));
        service.retireCustomField(panelCode, body);
        return ApiResult.ok(null);
    }
}
