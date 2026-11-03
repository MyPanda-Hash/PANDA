package com.mes.service;

import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.ArrayDeque;
import java.util.Comparator;
import java.util.Deque;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 业务流程追踪：给定任意单号，沿生单关系正反向找全相关单据，判定每一步的状态与卡点。
 *
 * 关联关系来自实际数据里的字段（不是设计推演）：
 * - 表头「来源单号」：ARRIVAL_IN / INSPECTION / SALE_INV / OUTSOURCE_ORDER / TRANSFER
 * - 表头「销售订单号」：MANU_ORDER / SALE_OUT / MATERIAL_OUT 按订单号串联
 * - 表头「匹配来源单号」：采购入库到采购订单
 * - 明细「加工单号」「来源面板+来源单号」：材料出库到加工单
 *
 * 纯计算实现（不查库），便于单测；数据库读取在 ProcessTraceQueryService。
 */
@Service
public class ProcessTraceService {

    /**
     * 一张单据的追踪摘要。
     *
     * @param panelCode 面板编码
     * @param docNo     单号
     * @param status    单据状态（草稿/审批中/已审核/已完工等）
     * @param links     该单据引用的上游单号（来源单号/销售订单号/加工单号等）
     */
    public record Doc(String panelCode, String docNo, String status, List<String> links) {}

    /**
     * 追踪结果的一步。
     *
     * @param state 完成 / 进行中 / 待开始
     */
    public record Step(String panelCode, String panelName, String docNo, String status,
                       String state, int seq) {}

    /** 追踪结果。 */
    public record TraceResult(List<Step> steps, String stalledDocNo, String stalledReason,
                              String currentDocNo, String message) {}

    /** 业务流程顺序权重：数字小的在上游。用于把追踪结果按业务先后排序。 */
    private static final Map<String, Integer> PANEL_ORDER = buildPanelOrder();

    private static Map<String, Integer> buildPanelOrder() {
        Map<String, Integer> m = new LinkedHashMap<>();
        int seq = 0;
        // 销售链
        for (String code : List.of("QUOTE_ORDER", "SO_ORDER", "OUTSOURCE_ORDER")) {
            m.put(code, seq++);
        }
        // 采购链
        for (String code : List.of("PU_REQ", "PU_ORDER", "ARRIVAL_IN", "INSPECTION",
                "PURCHASE_IN", "PURCHASE_RETURN")) {
            m.put(code, seq++);
        }
        // 生产链
        for (String code : List.of("MANU_ORDER", "FIRST_INSPECT", "PROCESS_REPORT",
                "PROCESS_INSPECT_APPLY", "PROCESS_INSPECTION",
                "MATERIAL_REQ", "TRANSFER", "MATERIAL_OUT", "FINISH_INSPECT", "FINISH_IN")) {
            m.put(code, seq++);
        }
        // 出库与开票
        for (String code : List.of("SALE_OUT", "SALE_INV", "SALE_INVOICE", "SALE_RETURN",
                "RECEIPT", "RECEIPT_PAYABLE")) {
            m.put(code, seq++);
        }
        return Map.copyOf(m);
    }

    /** 面板编码 → 中文名，追踪步骤直接展示。 */
    private static final Map<String, String> PANEL_NAMES = Map.ofEntries(
            Map.entry("QUOTE_ORDER", "报价单"),
            Map.entry("SO_ORDER", "销售订单"),
            Map.entry("OUTSOURCE_ORDER", "委外加工单"),
            Map.entry("PU_REQ", "请购单"),
            Map.entry("PU_ORDER", "采购订单"),
            Map.entry("ARRIVAL_IN", "到货单"),
            Map.entry("INSPECTION", "检验单"),
            Map.entry("PURCHASE_IN", "采购入库单"),
            Map.entry("PURCHASE_RETURN", "采购退货单"),
            Map.entry("MANU_ORDER", "生产加工单"),
            Map.entry("PROCESS_REPORT", "工序汇报单"),
            Map.entry("PROCESS_INSPECT_APPLY", "工序报检单"),
            Map.entry("PROCESS_INSPECTION", "生产过程检验单"),
            Map.entry("FIRST_INSPECT", "首件报检单"),
            Map.entry("MATERIAL_REQ", "领料申请单"),
            Map.entry("TRANSFER", "调拨单"),
            Map.entry("MATERIAL_OUT", "材料出库单"),
            Map.entry("FINISH_INSPECT", "成品报检单"),
            Map.entry("FINISH_IN", "产成品入库单"),
            Map.entry("SALE_OUT", "销售出库单"),
            Map.entry("SALE_INV", "销货单"),
            Map.entry("SALE_INVOICE", "销售发票"),
            Map.entry("SALE_RETURN", "销售退货单"),
            Map.entry("RECEIPT", "收款单"),
            Map.entry("RECEIPT_PAYABLE", "付款单"));

    /**
     * 追踪入口：从 seedNo 出发，正反向找全相关单据并判定状态。
     * 找不到任何单据时返回空 steps + 提示，不抛异常。
     */
    public TraceResult trace(String seedNo, List<Doc> docs) {
        String seed = seedNo == null ? "" : seedNo.trim();
        if (seed.isEmpty() || docs == null || docs.isEmpty()) {
            return new TraceResult(List.of(), "", "", "", "请输入单号");
        }

        Map<String, Doc> byNo = new LinkedHashMap<>();
        for (Doc d : docs) {
            if (d.docNo() != null && !d.docNo().isBlank()) byNo.putIfAbsent(d.docNo(), d);
        }
        if (!byNo.containsKey(seed)) {
            return new TraceResult(List.of(), "", "", "", "未找到单号 " + seed + "，请确认是否为本系统单据");
        }

        // 反向索引：被谁引用。用于从下游单号向上找源单。
        Map<String, Set<String>> referrers = new LinkedHashMap<>();
        for (Doc d : docs) {
            for (String link : d.links()) {
                if (link == null || link.isBlank()) continue;
                referrers.computeIfAbsent(link, k -> new LinkedHashSet<>()).add(d.docNo());
            }
        }

        // BFS 双向扩散，visited 去重保证环不导致死循环
        Set<String> visited = new LinkedHashSet<>();
        Deque<String> queue = new ArrayDeque<>();
        visited.add(seed);
        queue.add(seed);
        while (!queue.isEmpty()) {
            String no = queue.poll();
            Doc d = byNo.get(no);
            if (d == null) continue;
            for (String link : d.links()) {
                if (link == null || link.isBlank()) continue;
                if (visited.add(link)) queue.add(link);
            }
            for (String child : referrers.getOrDefault(no, Set.of())) {
                if (visited.add(child)) queue.add(child);
            }
        }

        List<Step> steps = new ArrayList<>();
        for (String no : visited) {
            Doc d = byNo.get(no);
            if (d == null) continue;
            steps.add(new Step(d.panelCode(), panelName(d.panelCode()), no, d.status(),
                    stateOf(d.status()), 0));
        }
        // 按业务顺序排，同顺序按单号稳定排序
        steps.sort(Comparator
                .comparingInt((Step s) -> PANEL_ORDER.getOrDefault(s.panelCode(), 999))
                .thenComparing(Step::docNo));
        List<Step> numbered = new ArrayList<>();
        for (int i = 0; i < steps.size(); i++) {
            Step s = steps.get(i);
            numbered.add(new Step(s.panelCode(), s.panelName(), s.docNo(), s.status(), s.state(), i + 1));
        }

        String current = numbered.isEmpty() ? "" : numbered.get(numbered.size() - 1).docNo();
        Stalled stalled = detectStalled(numbered);
        return new TraceResult(numbered, stalled.docNo(), stalled.reason(), current,
                numbered.size() + " 个环节");
    }

    /** 状态 → 阶段：已审核类算完成，审批中/草稿/生产中算进行中。 */
    private static String stateOf(String status) {
        String s = status == null ? "" : status.trim();
        if (s.isEmpty()) return "待开始";
        return switch (s) {
            case "已审核", "已完工", "已关闭", "已核销" -> "完成";
            case "审批中", "草稿", "生产中", "部分核销" -> "进行中";
            case "已中止", "已驳回", "作废" -> "已中止";
            default -> "进行中";
        };
    }

    /** 卡点：最靠下游的「进行中」单据；全部完成则无卡点。 */
    private record Stalled(String docNo, String reason) {}

    private static Stalled detectStalled(List<Step> steps) {
        for (int i = steps.size() - 1; i >= 0; i--) {
            Step s = steps.get(i);
            if ("已中止".equals(s.state())) {
                return new Stalled(s.docNo(), s.panelName() + " " + s.docNo() + " 已中止，流程中断");
            }
            if ("进行中".equals(s.state())) {
                String reason = statusReason(s);
                return new Stalled(s.docNo(), s.panelName() + " " + s.docNo() + " 停在「"
                        + s.status() + "」，" + reason);
            }
        }
        return new Stalled("", "");
    }

    /** 把状态翻译成业务上该做什么，直接给操作员看。 */
    private static String statusReason(Step step) {
        String status = step.status();
        if ("草稿".equals(status)) return "需要提交审核";
        if ("审批中".equals(status)) return "等待审批通过";
        if ("生产中".equals(status)) return "车间尚未完工";
        if ("部分核销".equals(status)) return "尚有未核销金额";
        return "需要推进到已审核";
    }

    private static String panelName(String panelCode) {
        if (panelCode == null) return "";
        return PANEL_NAMES.getOrDefault(panelCode, panelCode);
    }
}
