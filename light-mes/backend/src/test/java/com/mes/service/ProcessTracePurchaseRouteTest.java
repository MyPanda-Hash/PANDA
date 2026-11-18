package com.mes.service;

import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 采购追踪路线：采购订单 → 到货单（分批到货收成一个节点 + 分叉）→ 检验单 → 采购入库单。
 * 采购单不该出现销售出库/销货/发票这种下游销售环节。
 */
class ProcessTracePurchaseRouteTest {

    private final ProcessTraceService service = new ProcessTraceService();

    private ProcessTraceService.Doc doc(String panel, String no, String status, String... links) {
        return new ProcessTraceService.Doc(panel, no, status, List.of(links));
    }

    private List<ProcessTraceService.Doc> chain() {
        return List.of(
                doc("PU_ORDER", "PO-001", "已审核"),
                doc("ARRIVAL_IN", "DH-001", "已审核", "PO-001"),
                doc("ARRIVAL_IN", "DH-002", "已审核", "PO-001"),
                doc("INSPECTION", "JY-001", "已审核", "DH-001"),
                doc("INSPECTION", "JY-002", "已审核", "DH-002"),
                doc("PURCHASE_IN", "RK-001", "已审核", "DH-001"));
    }

    @Test
    void purchaseSeedUsesPurchaseRouteWithoutSalesSteps() {
        ProcessTraceService.TraceResult r = service.trace("PO-001", chain());

        assertEquals(List.of("PU_ORDER", "ARRIVAL_IN", "INSPECTION", "PURCHASE_IN"),
                r.route().stream().map(ProcessTraceService.RouteNode::panelCode).toList());
        assertFalse(r.route().stream().anyMatch(n -> "SALE_OUT".equals(n.panelCode())
                || "SALE_INV".equals(n.panelCode()) || "SALE_INVOICE".equals(n.panelCode())),
                "采购单追踪不该带销售出库/销货/发票");
    }

    @Test
    void splitArrivalsCollapseIntoOneNodeWithBranches() {
        ProcessTraceService.TraceResult r = service.trace("PO-001", chain());

        ProcessTraceService.RouteNode arrival = r.route().stream()
                .filter(n -> "ARRIVAL_IN".equals(n.panelCode())).findFirst().orElseThrow();
        assertEquals("DH-001", arrival.docNo());
        assertEquals(1, arrival.branches().size(), "第二批到货挂成分叉，不再占一格");
        assertEquals("DH-002", arrival.branches().get(0).docNo());
    }

    @Test
    void inspectionsAreBetweenArrivalAndReceipt() {
        List<String> codes = service.trace("PO-001", chain()).route().stream()
                .map(ProcessTraceService.RouteNode::panelCode).toList();
        assertTrue(codes.indexOf("ARRIVAL_IN") < codes.indexOf("INSPECTION"));
        assertTrue(codes.indexOf("INSPECTION") < codes.indexOf("PURCHASE_IN"));
    }

    @Test
    void receiptWithoutInspectionShowsWaivedInspection() {
        // 到货行免检直入：没有检验单，但入库单已经存在，这一环写「免检直入」而不是「待生成」
        List<ProcessTraceService.Doc> docs = List.of(
                doc("PU_ORDER", "PO-002", "已审核"),
                doc("ARRIVAL_IN", "DH-003", "已审核", "PO-002"),
                doc("PURCHASE_IN", "RK-002", "已审核", "DH-003"));

        ProcessTraceService.RouteNode inspection = service.trace("PO-002", docs).route().stream()
                .filter(n -> "INSPECTION".equals(n.panelCode())).findFirst().orElseThrow();

        assertFalse(inspection.present(), "确实没有检验单");
        assertEquals("免检直入", inspection.status());
    }

    @Test
    void arrivalSeedAlsoUsesPurchaseRoute() {
        List<String> codes = service.trace("DH-001", chain()).route().stream()
                .map(ProcessTraceService.RouteNode::panelCode).toList();
        assertEquals(List.of("PU_ORDER", "ARRIVAL_IN", "INSPECTION", "PURCHASE_IN"), codes);
    }
}
