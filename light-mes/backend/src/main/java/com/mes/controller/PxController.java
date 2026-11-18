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

import java.util.ArrayList;
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
    private final com.mes.service.UsageLogService usageLog;
    private final com.mes.service.PurchaseReqAnalysisService purchaseReqAnalysisService;

    public PxController(PanelRuntimeService service, RoleService roleService,
                        com.mes.service.UsageLogService usageLog,
                        com.mes.service.PurchaseReqAnalysisService purchaseReqAnalysisService) {
        this.service = service;
        this.roleService = roleService;
        this.usageLog = usageLog;
        this.purchaseReqAnalysisService = purchaseReqAnalysisService;
    }

    /**
     * 取真实客户端 IP：直接 getRemoteAddr 在反代后面永远是 127.0.0.1，
     * 日志里就分不清是谁在哪儿操作。按 X-Forwarded-For 第一个地址兜底。
     */
    private static String clientIp(jakarta.servlet.http.HttpServletRequest req) {
        if (req == null) return null;
        String xff = req.getHeader("X-Forwarded-For");
        if (xff != null && !xff.isBlank()) {
            String first = xff.split(",")[0].trim();
            if (!first.isEmpty()) return first;
        }
        return req.getRemoteAddr();
    }

    /** 面板编码 → 展示名；从面板配置里取，查不到就退回编码本身，不让日志出现空白。 */
    @SuppressWarnings("unchecked")
    private String panelNameOf(String panelCode) {
        try {
            Map<String, Object> config = service.getPanelConfig(panelCode);
            if (config != null) {
                // 面板名在 metadata.panelName 里（顶层只有 metadata/dataSchema/detail 等块）
                Object meta = config.get("metadata");
                if (meta instanceof Map<?, ?> m) {
                    Object name = m.get("panelName");
                    if (name != null && !String.valueOf(name).isBlank()) return String.valueOf(name);
                }
                Object direct = config.get("panelName");
                if (direct != null && !String.valueOf(direct).isBlank()) return String.valueOf(direct);
            }
        } catch (Exception ignored) {
            // 取不到就用编码，日志不该因为查名字而失败
        }
        return panelCode;
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
    public ApiResult<Map<String, Object>> callButton(Authentication auth, @RequestBody Map<String, Object> body,
                                                     jakarta.servlet.http.HttpServletRequest request) {
        String panelCode = String.valueOf(body.getOrDefault("panelCode", ""));
        String buttonName = String.valueOf(body.getOrDefault("buttonName", ""));
        requireVisible(auth, panelCode);
        requireApproveIfDecision(auth, panelCode, buttonName);
        @SuppressWarnings("unchecked")
        Map<String, Object> formData = (Map<String, Object>) body.getOrDefault("formData", Map.of());
        @SuppressWarnings("unchecked")
        Map<String, Object> buttonParam = (Map<String, Object>) body.getOrDefault("buttonParam", Map.of());
        // 业务动作留痕要在 try/finally 里做：失败的提交同样要留痕。
        // 「谁试图提交但被状态校验挡回去」本身就是审计要点，
        // 只在成功路径埋点会丢掉这一类记录（实测踩到：提交非草稿单返回 409，日志里什么都没有）。
        boolean audit = isBusinessAction(buttonName);
        try {
            Map<String, Object> result = service.callButton(panelCode, buttonName, formData, buttonParam);
            if (audit) {
                usageLog.recordAction(auth.getName(), null, panelCode, panelNameOf(panelCode),
                        buttonName, docNoOf(formData), clientIp(request));
            }
            return ApiResult.ok(result);
        } catch (RuntimeException ex) {
            if (audit) {
                // 失败也留痕：动作名后面标记失败原因，页面上一眼能分出「做了」和「试了没成」
                usageLog.recordAction(auth.getName(), null, panelCode, panelNameOf(panelCode),
                        buttonName + "（失败）", docNoOf(formData), clientIp(request));
            }
            throw ex;
        }
    }

    /** 纯浏览类动作不记日志；其余（提交/审批/生单/弃审/删除…）都记。 */
    private static final java.util.Set<String> READONLY_ACTIONS = java.util.Set.of(
            "查询", "刷新", "查找", "重置", "导出", "打印", "预览", "翻页", "全部", "高级查询");

    private static boolean isBusinessAction(String buttonName) {
        if (buttonName == null || buttonName.isBlank()) return false;
        for (String readonly : READONLY_ACTIONS) {
            if (buttonName.contains(readonly)) return false;
        }
        return true;
    }

    /** 从表单数据里取单据号：不同面板的键名不统一，按常见顺序兜底。 */
    private static String docNoOf(Map<String, Object> formData) {
        if (formData == null) return null;
        for (String key : new String[]{"编号", "单据编号", "记录编号", "面板单号", "单号"}) {
            Object v = formData.get(key);
            if (v != null && !String.valueOf(v).isBlank()) return String.valueOf(v);
        }
        return null;
    }

    @PostMapping("/deleteForms")
    public ApiResult<Void> deleteForms(Authentication auth, @RequestBody Map<String, Object> body,
                                       jakarta.servlet.http.HttpServletRequest request) {
        String panelCode = String.valueOf(body.getOrDefault("panelCode", ""));
        requireVisible(auth, panelCode);
        @SuppressWarnings("unchecked")
        List<String> rowCodes = (List<String>) body.getOrDefault("rowCodes", List.of());
        service.deleteForms(panelCode, rowCodes);
        // 删除是高风险动作，必须留痕
        usageLog.recordAction(auth.getName(), null, panelCode, panelNameOf(panelCode),
                "删除单据", String.join(",", rowCodes), clientIp(request));
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

    /** 排产批次看板的「未开工」列：已下发加工单里还没排产的工序。 */
    @GetMapping("/pendingPlanBoard")
    public ApiResult<List<Map<String, Object>>> pendingPlanBoard() {
        return ApiResult.ok(service.pendingPlanBoard());
    }

    /** 按排产批次报工：合格 + 不合格 = 报工数量。 */
    @PostMapping("/reportByBatch")
    public ApiResult<Map<String, Object>> reportByBatch(@RequestBody Map<String, Object> body) {
        String dispatchNo = String.valueOf(body.getOrDefault("排产单号", ""));
        double qualified = parseDouble(body.get("合格数量"), "合格数量");
        double rejected = parseDouble(body.get("不合格数量"), "不合格数量");
        return ApiResult.ok(service.reportByBatch(dispatchNo, qualified, rejected));
    }

    /** 首件在前、报工在后：给某个排产批次所属工序补建首件报检单（一工序一单，已存在则返回原单）。 */
    @PostMapping("/firstInspectByBatch")
    public ApiResult<Map<String, Object>> firstInspectByBatch(@RequestBody Map<String, Object> body) {
        return ApiResult.ok(service.createFirstInspectByBatch(
                String.valueOf(body.getOrDefault("排产单号", ""))));
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
     * 一键排产：把当前可排产量 > 0 的工序全部排成批次并审批通过。
     * 可传 productCode 只排某一件产品。
     */
    @PostMapping("/planAll")
    public ApiResult<Map<String, Object>> planAll(@RequestBody Map<String, Object> body) {
        String moNo = String.valueOf(body.getOrDefault("manuOrderNo", ""));
        String productCode = String.valueOf(body.getOrDefault("产品编码", ""));
        return ApiResult.ok(service.planAll(moNo, productCode));
    }

    /**
     * 建排产批次：一个工序分一批，可跨班组/设备。
     */
    @PostMapping("/planBatch")
    public ApiResult<Map<String, Object>> planBatch(@RequestBody Map<String, Object> body) {
        String moNo = String.valueOf(body.getOrDefault("manuOrderNo", ""));
        String prodCode = String.valueOf(body.getOrDefault("产品编码", ""));
        String procCode = String.valueOf(body.getOrDefault("工序编码", ""));
        double qty;
        try {
            qty = Double.parseDouble(String.valueOf(body.getOrDefault("数量", "0")));
        } catch (NumberFormatException e) {
            throw new IllegalArgumentException("排产数量必须是数字");
        }
        return ApiResult.ok(service.planBatch(moNo, prodCode, procCode, qty,
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
     * 重算加工单完工进度（报工已存在但累计没推进时的维护入口）。
     */
    @PostMapping("/syncCompletion")
    public ApiResult<Map<String, Object>> syncCompletion(@RequestBody Map<String, Object> body) {
        return ApiResult.ok(service.syncCompletion(String.valueOf(body.getOrDefault("加工单号", ""))));
    }

    /**
     * 成品报检候选：已报完全部工序、且还没生成过成品报检单的加工单。
     */
    @GetMapping("/finishInspectCandidates")
    public ApiResult<List<Map<String, Object>>> finishInspectCandidates() {
        return ApiResult.ok(service.finishInspectCandidates());
    }

    /**
     * 成品报检单生成：按加工单生成（单号 = 加工单号-JY），生成即草稿。
     */
    @PostMapping("/finishInspectFromMo")
    public ApiResult<Map<String, Object>> finishInspectFromMo(@RequestBody Map<String, Object> body) {
        String moNo = String.valueOf(body.getOrDefault("加工单号", ""));
        return ApiResult.ok(service.createFinishInspectFromMo(moNo));
    }

    /**
     * 工序汇报选单看板：加工单 + 每道工序的计划/已报/未报与可否报工。
     */
    @GetMapping("/processReportPickBoard")
    public ApiResult<List<Map<String, Object>>> processReportPickBoard() {
        return ApiResult.ok(service.processReportPickBoard());
    }

    /**
     * 销售订单排产看板：一行一个订单行，含订单数量、已排、可排与预计加工单号。
     */
    /** 加工单「选销售订单」候选：已审核销售订单 + 排产进度（未排产 / 部分排产 / 已排产），可按日期与客户筛。 */
    @GetMapping("/saleOrderPlanCandidates")
    public ApiResult<List<Map<String, Object>>> saleOrderPlanCandidates(
            @RequestParam(value = "dateFrom", required = false) String dateFrom,
            @RequestParam(value = "dateTo", required = false) String dateTo,
            @RequestParam(value = "customer", required = false) String customer) {
        return ApiResult.ok(service.saleOrderPlanCandidates(dateFrom, dateTo, customer));
    }

    @GetMapping("/saleOrderPlanBoard")
    public ApiResult<List<Map<String, Object>>> saleOrderPlanBoard(@RequestParam("saleOrderNo") String saleOrderNo) {
        return ApiResult.ok(service.saleOrderPlanBoard(saleOrderNo));
    }

    /**
     * 销售订单排产：按订单行生成生产加工单（单号 = 销售订单号-行号），生成即已审核。
     * body.lines = [{行号, 数量}]，省略行表示整单每行全排。
     */
    @SuppressWarnings("unchecked")
    @PostMapping("/planFromSalesOrder")
    public ApiResult<Map<String, Object>> planFromSalesOrder(@RequestBody Map<String, Object> body) {
        String soNo = String.valueOf(body.getOrDefault("销售订单号", ""));
        List<Map<String, Object>> lines = body.get("lines") instanceof List<?> list
                ? (List<Map<String, Object>>) list : new ArrayList<>();
        return ApiResult.ok(service.planFromSalesOrder(soNo, lines));
    }

    /**
     * 计划分析可选的销售订单：已审核且还有未排产数量。
     * 计划分析面板的选单下拉数据源。
     */
    @GetMapping("/planAnalyzeCandidates")
    public ApiResult<List<Map<String, Object>>> planAnalyzeCandidates(Authentication auth) {
        requireVisible(auth, "SO_ORDER");
        return ApiResult.ok(service.planAnalyzeCandidates());
    }

    /**
     * 计划分析：一张销售订单的成品 / 物料缺口 / 工艺路线 / 风险 / 结论。
     *
     * <p>body: { salesOrderNo }。替代主生产计划、物料需求运算、齐套分析、
     * 采购需求分析四个横向汇总面板的单据级分析入口。
     */
    @PostMapping("/planAnalyze")
    public ApiResult<Map<String, Object>> planAnalyze(Authentication auth,
                                                      @RequestBody Map<String, Object> body) {
        requireVisible(auth, "SO_ORDER");
        String salesOrderNo = String.valueOf(body.getOrDefault("salesOrderNo", "")).trim();
        return ApiResult.ok(service.planAnalyze(salesOrderNo, true));
    }

    /**
     * 计划分析 → 生成请购单（草稿）：外购件缺料行落成 PU_REQ。
     *
     * <p>body: { salesOrderNo, codes?: string[] }。codes 为空表示全部可请购行。
     */
    @PostMapping("/planAnalyzePurchaseReq")
    public ApiResult<Map<String, Object>> planAnalyzePurchaseReq(Authentication auth,
                                                                 @RequestBody Map<String, Object> body) {
        requireVisible(auth, "SO_ORDER");
        String salesOrderNo = String.valueOf(body.getOrDefault("salesOrderNo", "")).trim();
        List<String> codes = new java.util.ArrayList<>();
        Object raw = body.get("codes");
        if (raw instanceof List<?> list) {
            for (Object item : list) {
                if (item == null) continue;
                String code = String.valueOf(item).trim();
                if (!code.isEmpty()) codes.add(code);
            }
        }
        return ApiResult.ok(service.planAnalyzePurchaseReq(salesOrderNo, codes));
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

    /** 采购开票工作台：以采购入库单为行，带出发票号与状态。 */
    @GetMapping("/purchaseInvoiceWorkbench")
    public ApiResult<List<Map<String, Object>>> purchaseInvoiceWorkbench() {
        return ApiResult.ok(service.purchaseInvoiceWorkbench());
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
    /**
     * 字段布局：表头 + 明细字段的顺序 / 列宽 / 显隐（字段管理弹窗初始化）。
     */
    @GetMapping("/fieldLayout")
    public ApiResult<List<Map<String, Object>>> fieldLayout(@RequestParam("panelCode") String panelCode) {
        return ApiResult.ok(service.fieldLayout(panelCode));
    }

    /**
     * 保存字段布局：全公司共用（owner=''），需要该面板的「调整」权限。
     */
    @PostMapping("/saveFieldLayout")
    public ApiResult<Void> saveFieldLayout(@RequestBody Map<String, Object> body, Authentication auth) {
        String panelCode = String.valueOf(body.getOrDefault("panelCode", ""));
        String userName = auth == null ? "" : auth.getName();
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> rows = body.get("rows") instanceof List<?> list
                ? (List<Map<String, Object>>) list : new ArrayList<>();
        service.saveFieldLayout(panelCode, userName, rows);
        return ApiResult.ok(null);
    }

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

    // ---------- 采购需求分析（轻量工作台） ----------

    /** 缺口汇总（分页）+ 概览。 */
    @PostMapping("/purchaseReqAnalysis/query")
    public ApiResult<Map<String, Object>> purchaseReqAnalysisQuery(Authentication auth,
            @RequestBody(required = false) Map<String, Object> body) {
        requireVisible(auth, "PU_REQ_ANALYSIS");
        Map<String, Object> filters = new java.util.LinkedHashMap<>();
        if (body != null && body.get("filters") instanceof Map<?, ?> raw) {
            for (Map.Entry<?, ?> entry : raw.entrySet()) {
                filters.put(String.valueOf(entry.getKey()), entry.getValue());
            }
        }
        int pageNo = body != null && body.get("pageNo") instanceof Number n ? n.intValue() : 1;
        int pageSize = body != null && body.get("pageSize") instanceof Number n ? n.intValue() : 50;
        return ApiResult.ok(purchaseReqAnalysisService.query(filters, pageNo, pageSize));
    }

    /** 某物料的来源明细（计划分析/手工）。 */
    @GetMapping("/purchaseReqAnalysis/sources")
    public ApiResult<List<Map<String, Object>>> purchaseReqAnalysisSources(Authentication auth,
            @RequestParam("materialCode") String materialCode,
            @RequestParam(value = "spec", required = false) String spec,
            @RequestParam(value = "unit", required = false) String unit) {
        requireVisible(auth, "PU_REQ_ANALYSIS");
        return ApiResult.ok(purchaseReqAnalysisService.sources(materialCode, spec, unit));
    }

    /** 某物料的单据链路：请购 → 采购订单 → 采购入库。 */
    @GetMapping("/purchaseReqAnalysis/chain")
    public ApiResult<List<Map<String, Object>>> purchaseReqAnalysisChain(Authentication auth,
            @RequestParam("materialCode") String materialCode) {
        requireVisible(auth, "PU_REQ_ANALYSIS");
        return ApiResult.ok(purchaseReqAnalysisService.chain(materialCode));
    }

    /** 生成请购单（单行/批量）。 */
    @PostMapping("/purchaseReqAnalysis/generate")
    public ApiResult<Map<String, Object>> purchaseReqAnalysisGenerate(Authentication auth,
            @RequestBody Map<String, Object> body) {
        requireVisible(auth, "PU_REQ_ANALYSIS");
        List<Map<String, Object>> items = new ArrayList<>();
        if (body != null && body.get("items") instanceof List<?> list) {
            for (Object one : list) {
                if (one instanceof Map<?, ?> raw) {
                    Map<String, Object> item = new java.util.LinkedHashMap<>();
                    for (Map.Entry<?, ?> e : raw.entrySet()) item.put(String.valueOf(e.getKey()), e.getValue());
                    items.add(item);
                }
            }
        }
        return ApiResult.ok(purchaseReqAnalysisService.generate(items));
    }

    /** 手工补录需求。 */
    @PostMapping("/purchaseReqAnalysis/manual")
    public ApiResult<Map<String, Object>> purchaseReqAnalysisManual(Authentication auth,
            @RequestBody Map<String, Object> body) {
        requireVisible(auth, "PU_REQ_ANALYSIS");
        Map<String, Object> data = new java.util.LinkedHashMap<>();
        if (body != null) {
            for (Map.Entry<String, Object> e : body.entrySet()) data.put(e.getKey(), e.getValue());
        }
        data.put("operator", auth == null ? "" : auth.getName());
        return ApiResult.ok(purchaseReqAnalysisService.manual(data));
    }

    /** 撤销手工需求。 */
    @PostMapping("/purchaseReqAnalysis/manual/revoke")
    public ApiResult<Map<String, Object>> purchaseReqAnalysisManualRevoke(Authentication auth,
            @RequestBody Map<String, Object> body) {
        requireVisible(auth, "PU_REQ_ANALYSIS");
        Object id = body == null ? null : body.get("id");
        if (!(id instanceof Number n)) throw new IllegalArgumentException("缺少手工需求 id");
        return ApiResult.ok(purchaseReqAnalysisService.revokeManual(n.longValue()));
    }

    /** 手工需求列表。 */
    @GetMapping("/purchaseReqAnalysis/manual")
    public ApiResult<List<Map<String, Object>>> purchaseReqAnalysisManualList(Authentication auth) {
        requireVisible(auth, "PU_REQ_ANALYSIS");
        return ApiResult.ok(purchaseReqAnalysisService.manualList());
    }

    /** 导出缺口汇总（xlsx）。 */
    @PostMapping("/purchaseReqAnalysis/export")
    public org.springframework.http.ResponseEntity<byte[]> purchaseReqAnalysisExport(Authentication auth,
            @RequestBody(required = false) Map<String, Object> body) throws java.io.IOException {
        requireVisible(auth, "PU_REQ_ANALYSIS");
        Map<String, Object> filters = new java.util.LinkedHashMap<>();
        if (body != null && body.get("filters") instanceof Map<?, ?> raw) {
            for (Map.Entry<?, ?> entry : raw.entrySet()) {
                filters.put(String.valueOf(entry.getKey()), entry.getValue());
            }
        }
        byte[] bytes = purchaseReqAnalysisService.export(filters);
        String name = "采购需求分析-" + java.time.LocalDate.now() + ".xlsx";
        return org.springframework.http.ResponseEntity.ok()
                .header(org.springframework.http.HttpHeaders.CONTENT_DISPOSITION,
                        org.springframework.http.ContentDisposition.attachment()
                                .filename(name, java.nio.charset.StandardCharsets.UTF_8).build().toString())
                .contentType(org.springframework.http.MediaType.parseMediaType(
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
                .body(bytes);
    }
}
