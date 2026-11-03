package com.mes.service;

import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 业务流程追踪：给定一个单号（销售订单号/加工单号/入库单号等），
 * 沿生单关系正反向找全相关单据，判定每一步的完成状态与卡点。
 *
 * 关联字段来自实际数据（不是设计推演）：
 * - 表头「来源单号」：ARRIVAL_IN / INSPECTION / SALE_INV / OUTSOURCE_ORDER / TRANSFER 等
 * - 表头「销售订单号」：MANU_ORDER / SALE_OUT / MATERIAL_OUT 等按订单号串联
 * - 表头「匹配来源单号」：采购入库到采购订单
 * - 明细「加工单号」「来源面板+来源单号」：材料出库到加工单
 */
class ProcessTraceServiceTest {

    private final ProcessTraceService service = new ProcessTraceService();

    /** 构造一条追踪输入用的单据摘要。 */
    private ProcessTraceService.Doc doc(String panel, String no, String status, String... links) {
        return new ProcessTraceService.Doc(panel, no, status, List.of(links));
    }

    @Test
    void singleDocumentTracesToItself() {
        List<ProcessTraceService.Doc> docs = List.of(doc("SO_ORDER", "SO-001", "已审核"));

        ProcessTraceService.TraceResult result = service.trace("SO-001", docs);

        assertEquals(1, result.steps().size());
        assertEquals("SO-001", result.steps().get(0).docNo());
    }

    @Test
    void forwardTraceFollowsSourceLink() {
        // SO → MO（MO 的销售订单号 = SO 的单号）
        List<ProcessTraceService.Doc> docs = List.of(
                doc("SO_ORDER", "SO-001", "已审核"),
                doc("MANU_ORDER", "MO-001", "已审核", "SO-001"),
                doc("FINISH_IN", "FI-001", "已审核", "MO-001"));

        ProcessTraceService.TraceResult result = service.trace("SO-001", docs);

        List<String> nos = result.steps().stream().map(ProcessTraceService.Step::docNo).toList();
        assertTrue(nos.contains("MO-001"), "应顺着销售订单号找到加工单");
        assertTrue(nos.contains("FI-001"), "应继续找到产成品入库单");
        assertEquals(3, result.steps().size());
    }

    @Test
    void backwardTraceFindsOriginatingSalesOrder() {
        List<ProcessTraceService.Doc> docs = List.of(
                doc("SO_ORDER", "SO-001", "已审核"),
                doc("MANU_ORDER", "MO-001", "已审核", "SO-001"),
                doc("FINISH_IN", "FI-001", "已审核", "MO-001"));

        ProcessTraceService.TraceResult result = service.trace("FI-001", docs);

        List<String> nos = result.steps().stream().map(ProcessTraceService.Step::docNo).toList();
        assertTrue(nos.contains("SO-001"), "反向应追到销售订单");
        assertTrue(nos.contains("MO-001"));
    }

    @Test
    void unknownDocumentReturnsEmptyTraceNotException() {
        ProcessTraceService.TraceResult result =
                service.trace("NOT-EXISTS", List.of(doc("SO_ORDER", "SO-001", "已审核")));

        assertTrue(result.steps().isEmpty());
        assertNotNull(result.message());
    }

    @Test
    void blankInputReturnsEmptyTrace() {
        assertTrue(service.trace("", List.of()).steps().isEmpty());
        assertTrue(service.trace(null, List.of()).steps().isEmpty());
    }

    @Test
    void cycleDoesNotLoopForever() {
        // A 引用 B，B 又引用 A：必须去重终止
        List<ProcessTraceService.Doc> docs = List.of(
                doc("SO_ORDER", "A", "已审核", "B"),
                doc("MANU_ORDER", "B", "已审核", "A"));

        ProcessTraceService.TraceResult result = service.trace("A", docs);

        assertEquals(2, result.steps().size());
    }

    @Test
    void stepCarriesStatusAndProgress() {
        List<ProcessTraceService.Doc> docs = List.of(
                doc("SO_ORDER", "SO-001", "已审核"),
                doc("MANU_ORDER", "MO-001", "草稿", "SO-001"));

        ProcessTraceService.TraceResult result = service.trace("SO-001", docs);

        ProcessTraceService.Step so = result.steps().stream()
                .filter(s -> s.docNo().equals("SO-001")).findFirst().orElseThrow();
        ProcessTraceService.Step mo = result.steps().stream()
                .filter(s -> s.docNo().equals("MO-001")).findFirst().orElseThrow();

        assertEquals("已审核", so.status());
        assertEquals("完成", so.state());
        assertEquals("草稿", mo.status());
        assertEquals("进行中", mo.state(), "草稿是进行中，不是卡住");
    }

    @Test
    void stalledDocumentIsReportedWithReason() {
        // 加工单停在草稿且下游还没有任何单据 → 卡点
        List<ProcessTraceService.Doc> docs = List.of(
                doc("SO_ORDER", "SO-001", "已审核"),
                doc("MANU_ORDER", "MO-001", "草稿", "SO-001"));

        ProcessTraceService.TraceResult result = service.trace("SO-001", docs);

        assertEquals("MO-001", result.stalledDocNo());
        assertFalse(result.stalledReason().isBlank());
    }

    @Test
    void fullyCompletedChainIsNotStalled() {
        List<ProcessTraceService.Doc> docs = List.of(
                doc("SO_ORDER", "SO-001", "已审核"),
                doc("MANU_ORDER", "MO-001", "已审核", "SO-001"),
                doc("FINISH_IN", "FI-001", "已审核", "MO-001"),
                doc("SALE_INV", "XS-001", "已审核", "FI-001"));

        ProcessTraceService.TraceResult result = service.trace("SO-001", docs);

        assertTrue(result.stalledDocNo().isEmpty());
    }

    @Test
    void orderedByBusinessSequenceNotDiscoveryOrder() {
        // 传入乱序，输出应按 销售→生产→入库→开票 排列
        List<ProcessTraceService.Doc> docs = List.of(
                doc("SALE_INV", "XS-001", "已审核", "FI-001"),
                doc("FINISH_IN", "FI-001", "已审核", "MO-001"),
                doc("MANU_ORDER", "MO-001", "已审核", "SO-001"),
                doc("SO_ORDER", "SO-001", "已审核"));

        ProcessTraceService.TraceResult result = service.trace("SO-001", docs);

        List<String> nos = result.steps().stream().map(ProcessTraceService.Step::docNo).toList();
        assertEquals(List.of("SO-001", "MO-001", "FI-001", "XS-001"), nos);
    }
}
