package com.mes.service;

import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 委外追踪路线：销售订单 → 委外加工单 → 发料 → 委外检验 → 委外入库 → 委外加工费用。
 *
 * <p>2026-11-13：回厂检验从「首件报检单」改为「委外检验单」（独立成单，合格才能入库）；
 * 首件报检单只作历史单据的分支保留，不再作为路线环节。
 * 同一销售订单下的自制线（排产/加工单/报工/产成品入库）不属于委外路线，不能混进来。
 */
class ProcessTraceOutsourceRouteTest {

    private final ProcessTraceService service = new ProcessTraceService();

    private ProcessTraceService.Doc doc(String panel, String no, String status, String... links) {
        return new ProcessTraceService.Doc(panel, no, status, List.of(links));
    }

    private List<ProcessTraceService.Doc> chain(boolean withSelfMade) {
        List<ProcessTraceService.Doc> docs = new java.util.ArrayList<>(List.of(
                doc("SO_ORDER", "SO-001", "已审核"),
                doc("OUTSOURCE_ORDER", "WW-001", "已审核", "SO-001"),
                doc("OUTSOURCE_ISSUE", "WF-001", "已审核", "WW-001"),
                doc("OUTSOURCE_INSPECTION", "WJ-001", "已审核", "WW-001"),
                doc("FIRST_INSPECT", "SJ-001", "已审核", "WW-001"),
                doc("OUTSOURCE_IN", "WR-001", "已审核", "WW-001"),
                doc("OUTSOURCE_FEE", "WY-001", "已审核", "WW-001")));
        if (withSelfMade) {
            docs.add(doc("MANU_ORDER", "SO-001-1", "已完工", "SO-001"));
            docs.add(doc("FINISH_IN", "FI-001", "已审核", "SO-001-1"));
        }
        return docs;
    }

    @Test
    void outsourceSeedUsesOutsourceRoute() {
        ProcessTraceService.TraceResult r = service.trace("WW-001", chain(true));

        assertEquals(List.of("SO_ORDER", "OUTSOURCE_ORDER", "OUTSOURCE_ISSUE", "OUTSOURCE_INSPECTION",
                        "OUTSOURCE_IN", "OUTSOURCE_FEE"),
                r.route().stream().map(ProcessTraceService.RouteNode::panelCode).toList(),
                "委外路线六步：回厂检验是独立环节，首件报检只作历史分支");
        assertTrue(r.route().stream().allMatch(ProcessTraceService.RouteNode::present), "六步都有单据");
    }

    @Test
    void outsourceInspectionIsItsOwnRouteNode() {
        ProcessTraceService.TraceResult r = service.trace("WW-001", chain(false));

        ProcessTraceService.RouteNode inspect = r.route().stream()
                .filter(n -> "OUTSOURCE_INSPECTION".equals(n.panelCode())).findFirst()
                .orElseThrow(() -> new AssertionError("委外路线必须有委外检验单环节"));
        assertEquals("WJ-001", inspect.docNo());
        int inspectAt = r.route().indexOf(inspect);
        int inboundAt = -1;
        for (int i = 0; i < r.route().size(); i++) {
            if ("OUTSOURCE_IN".equals(r.route().get(i).panelCode())) inboundAt = i;
        }
        assertTrue(inboundAt >= 0 && inspectAt < inboundAt, "检验必须排在入库之前");
    }

    @Test
    void legacyChainWithoutInspectionShowsHistoricalDirectInbound() {
        // 2026-11-13 之前生成的委外单没有委外检验单，但已经入库了：
        // 这一环不能显示「待生成」（会让人以为还得补检验），要标成历史直入
        List<ProcessTraceService.Doc> docs = List.of(
                doc("SO_ORDER", "SO-001", "已审核"),
                doc("OUTSOURCE_ORDER", "WW-001", "已审核", "SO-001"),
                doc("OUTSOURCE_ISSUE", "WF-001", "已审核", "WW-001"),
                doc("FIRST_INSPECT", "SJ-001", "已审核", "WW-001"),
                doc("OUTSOURCE_IN", "WR-001", "已审核", "WW-001"),
                doc("OUTSOURCE_FEE", "WY-001", "已审核", "WW-001"));

        ProcessTraceService.TraceResult r = service.trace("WW-001", docs);

        ProcessTraceService.RouteNode node = r.route().stream()
                .filter(n -> "OUTSOURCE_INSPECTION".equals(n.panelCode())).findFirst().orElseThrow();
        assertFalse(node.present(), "历史单没有检验单，不算已完成");
        assertEquals("历史直入", node.status(), "不能显示待生成，要说明是历史单据直接入库");
    }

    @Test
    void legacyFirstInspectStaysAsBranchOfReceiptNode() {
        ProcessTraceService.TraceResult r = service.trace("WW-001", chain(false));

        ProcessTraceService.RouteNode receipt = r.route().stream()
                .filter(n -> "OUTSOURCE_IN".equals(n.panelCode())).findFirst().orElseThrow();
        assertEquals(1, receipt.branches().size(), "一张回厂首检单挂在入库节点下");
        assertEquals("FIRST_INSPECT", receipt.branches().get(0).panelCode());
        assertEquals("SJ-001", receipt.branches().get(0).docNo());
        assertFalse(r.route().stream().anyMatch(n -> "FIRST_INSPECT".equals(n.panelCode())),
                "首检不再作为路线上的独立环节");
    }

    @Test
    void selfMadeBranchIsNotPartOfOutsourceRoute() {
        ProcessTraceService.TraceResult r = service.trace("WW-001", chain(true));

        assertFalse(r.steps().stream().anyMatch(s -> "MANU_ORDER".equals(s.panelCode())),
                "同一销售订单的自制线不属于委外链路");
        assertFalse(r.steps().stream().anyMatch(s -> "FINISH_IN".equals(s.panelCode())));
    }

    @Test
    void siblingOutsourceOrdersAreNotPulledIn() {
        // 同一销售订单下还有第二张委外加工单：追踪第一张时不该把兄弟单及其下游带进来
        List<ProcessTraceService.Doc> docs = new java.util.ArrayList<>(chain(false));
        docs.add(doc("OUTSOURCE_ORDER", "WW-002", "已审核", "SO-001"));
        docs.add(doc("OUTSOURCE_ISSUE", "WF-002", "已审核", "WW-002"));
        docs.add(doc("OUTSOURCE_IN", "WR-002", "已审核", "WW-002"));

        ProcessTraceService.TraceResult r = service.trace("WW-001", docs);

        List<String> nos = r.steps().stream().map(ProcessTraceService.Step::docNo).toList();
        assertTrue(nos.contains("WW-001"));
        assertFalse(nos.contains("WW-002"), "兄弟委外单不该出现在这条追踪里");
        assertFalse(nos.contains("WF-002"));
        assertFalse(nos.contains("WR-002"));
        assertEquals(7, nos.size(), "只剩销售订单 + 本单六张（含委外检验单）");
    }

    @Test
    void upstreamPanelsSeedFromIssueOrFeeAlsoUseOutsourceRoute() {
        ProcessTraceService.TraceResult fromIssue = service.trace("WF-001", chain(false));
        assertEquals("OUTSOURCE_ISSUE",
                fromIssue.route().stream().filter(ProcessTraceService.RouteNode::present)
                        .filter(n -> "OUTSOURCE_ISSUE".equals(n.panelCode()))
                        .findFirst().orElseThrow().panelCode());

        ProcessTraceService.TraceResult fromFee = service.trace("WY-001", chain(false));
        assertEquals(List.of("SO_ORDER", "OUTSOURCE_ORDER", "OUTSOURCE_ISSUE", "OUTSOURCE_INSPECTION",
                        "OUTSOURCE_IN", "OUTSOURCE_FEE"),
                fromFee.route().stream().map(ProcessTraceService.RouteNode::panelCode).toList());
    }
}
