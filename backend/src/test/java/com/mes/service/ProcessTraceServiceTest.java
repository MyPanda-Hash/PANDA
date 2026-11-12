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

    // ---------- 标准路线：路线固定，缺的环节显示为「待生成」 ----------

    /**
     * 需要生产的订单：路线固定为
     * 销售订单 → 生产加工单 → 工序排产 → 生产领料单 → 材料出库单 →
     * 首件报检单 → 工序汇报单 → 产成品入库单 → 销售出库单 → 销货单 → 销售发票。
     */
    @Test
    void productionRouteIsFixedAndMarksPendingSteps() {
        List<ProcessTraceService.Doc> docs = List.of(
                doc("SO_ORDER", "SO-001", "已审核"),
                doc("MANU_ORDER", "MO-001", "已完工", "SO-001"));

        ProcessTraceService.TraceResult result = service.trace("SO-001", docs, true);

        List<String> codes = result.route().stream().map(ProcessTraceService.RouteNode::panelCode).toList();
        assertEquals(List.of("SO_ORDER", "DISPATCH", "MANU_ORDER", "MATERIAL_OUT",
                "PROCESS_REPORT", "FINISH_INSPECT", "FINISH_IN", "SALE_OUT", "SALE_INV", "SALE_INVOICE"), codes);
        assertTrue(result.route().get(0).present(), "销售订单已存在");
        assertTrue(result.route().get(2).present(), "生产加工单已存在");
        // 排产在加工单之前：这张单是直接生成的加工单，没有排产批次
        assertFalse(result.route().get(1).present(), "工单排产还没做");
        assertEquals("待生成", result.route().get(1).state());
        assertEquals("生产加工单", result.route().get(2).panelName());
    }

    /** 首件检验是工序汇报的分支，不占主干；成品报检在产成品入库之前。 */
    @Test
    void firstInspectIsBranchOfProcessReport() {
        ProcessTraceService.Doc mo = new ProcessTraceService.Doc("MANU_ORDER", "MO-001", "已审核",
                List.of("SO-001"), "", "",
                List.of(new ProcessTraceService.Process("PX001", "下料"),
                        new ProcessTraceService.Process("PX002", "车削")));
        ProcessTraceService.Doc report = new ProcessTraceService.Doc("PROCESS_REPORT", "GX-001", "已审核",
                List.of("MO-001"), "PX001", "下料", List.of());
        // 一工序一单：下料的首检单只挂下料，车削另有自己的一张
        ProcessTraceService.Doc inspect = new ProcessTraceService.Doc("FIRST_INSPECT", "SJ-001", "已审核",
                List.of("MO-001"), "PX001", "下料",
                List.of(new ProcessTraceService.Process("PX001", "下料")));
        ProcessTraceService.Doc inspect2 = new ProcessTraceService.Doc("FIRST_INSPECT", "SJ-002", "草稿",
                List.of("MO-001"), "PX002", "车削",
                List.of(new ProcessTraceService.Process("PX002", "车削")));
        List<ProcessTraceService.Doc> docs = List.of(
                doc("SO_ORDER", "SO-001", "已审核"), mo, report, inspect, inspect2);

        ProcessTraceService.TraceResult result = service.trace("SO-001", docs, true);

        List<String> codes = result.route().stream().map(ProcessTraceService.RouteNode::panelCode).toList();
        assertFalse(codes.contains("FIRST_INSPECT"), "首检不进主干");
        ProcessTraceService.RouteNode firstReport = result.route().stream()
                .filter(n -> "PROCESS_REPORT".equals(n.panelCode())).findFirst().orElseThrow();
        assertEquals(1, firstReport.branches().size());
        assertEquals("FIRST_INSPECT", firstReport.branches().get(0).panelCode());
        assertEquals("SJ-001", firstReport.branches().get(0).docNo());
        assertTrue(firstReport.branches().get(0).present());
        // 每道工序挂自己的首检单：车削的汇报节点挂 SJ-002，不是下料那张
        ProcessTraceService.RouteNode secondReport = result.route().stream()
                .filter(n -> "PROCESS_REPORT".equals(n.panelCode())).skip(1).findFirst().orElseThrow();
        assertEquals("SJ-002", secondReport.branches().get(0).docNo());
        assertEquals("草稿", secondReport.branches().get(0).status());
    }

    /** 加工单没建时，标准路线照样 10 个环节，全部待生成（领料不再必经：现场领料直接出库）。 */
    @Test
    void pendingProductionRouteHasTenSteps() {
        ProcessTraceService.TraceResult result =
                service.trace("SO-001", List.of(doc("SO_ORDER", "SO-001", "已审核")), true);

        assertEquals(10, result.route().size());
        assertEquals("DISPATCH", result.route().get(1).panelCode());
        assertEquals("MANU_ORDER", result.route().get(2).panelCode());
        assertEquals("MATERIAL_OUT", result.route().get(3).panelCode(), "领料申请单不再是必经环节");
        assertEquals("PROCESS_REPORT", result.route().get(4).panelCode());
        assertEquals("FINISH_INSPECT", result.route().get(5).panelCode());
        assertEquals("FINISH_IN", result.route().get(6).panelCode());
    }

    /** 工序派工单同样一工序一张，按工序展开并显示工序名。 */
    @Test
    void dispatchExpandsPerProcess() {
        ProcessTraceService.Doc mo = new ProcessTraceService.Doc("MANU_ORDER", "MO-001", "已完工",
                List.of("SO-001"), "", "",
                List.of(new ProcessTraceService.Process("PX001", "下料"),
                        new ProcessTraceService.Process("PX002", "车削")));
        ProcessTraceService.Doc pg1 = new ProcessTraceService.Doc("DISPATCH", "PG-001", "已审核",
                List.of("MO-001"), "PX001", "下料", List.of());
        List<ProcessTraceService.Doc> docs = List.of(
                doc("SO_ORDER", "SO-001", "已审核"), mo, pg1);

        ProcessTraceService.TraceResult result = service.trace("SO-001", docs, true);

        List<ProcessTraceService.RouteNode> dispatch = result.route().stream()
                .filter(n -> "DISPATCH".equals(n.panelCode())).toList();
        assertEquals(2, dispatch.size());
        assertEquals("下料", dispatch.get(0).processName());
        assertTrue(dispatch.get(0).present());
        assertEquals("车削", dispatch.get(1).processName());
        assertFalse(dispatch.get(1).present());
    }

    /** 有存货直接发货：跳过生产段，只保留 销售订单 → 销售出库 → 销货 → 发票。 */
    @Test
    void directShipmentRouteSkipsProduction() {
        List<ProcessTraceService.Doc> docs = List.of(doc("SO_ORDER", "SO-001", "已审核"));

        ProcessTraceService.TraceResult result = service.trace("SO-001", docs, false);

        List<String> codes = result.route().stream().map(ProcessTraceService.RouteNode::panelCode).toList();
        assertEquals(List.of("SO_ORDER", "SALE_OUT", "SALE_INV", "SALE_INVOICE"), codes);
        assertFalse(result.route().get(1).present());
    }

    /** 有加工单就算生产路线，即使调用方没传 needsProduction。 */
    @Test
    void manuOrderInTraceForcesProductionRoute() {
        List<ProcessTraceService.Doc> docs = List.of(
                doc("SO_ORDER", "SO-001", "已审核"),
                doc("MANU_ORDER", "MO-001", "已审核", "SO-001"));

        ProcessTraceService.TraceResult result = service.trace("SO-001", docs);

        assertTrue(result.route().stream().anyMatch(n -> "PROCESS_REPORT".equals(n.panelCode())),
                "生产路线必须包含工序汇报环节");
    }

    /** 工序汇报单按加工单工序逐条展开，并带上工序名称。 */
    @Test
    void processReportsExpandPerProcessWithNames() {
        ProcessTraceService.Doc mo = new ProcessTraceService.Doc("MANU_ORDER", "MO-001", "已完工",
                List.of("SO-001"), "", "",
                List.of(new ProcessTraceService.Process("PX001", "下料"),
                        new ProcessTraceService.Process("PX002", "车削")));
        ProcessTraceService.Doc gx1 = new ProcessTraceService.Doc("PROCESS_REPORT", "GX-001", "已审核",
                List.of("MO-001"), "PX001", "下料", List.of());
        List<ProcessTraceService.Doc> docs = List.of(
                doc("SO_ORDER", "SO-001", "已审核"), mo, gx1);

        ProcessTraceService.TraceResult result = service.trace("SO-001", docs, true);

        List<ProcessTraceService.RouteNode> reports = result.route().stream()
                .filter(n -> "PROCESS_REPORT".equals(n.panelCode())).toList();
        assertEquals(2, reports.size(), "两个工序就是两个汇报环节");
        assertEquals("下料", reports.get(0).processName());
        assertEquals("GX-001", reports.get(0).docNo());
        assertTrue(reports.get(0).present());
        assertEquals("车削", reports.get(1).processName());
        assertFalse(reports.get(1).present(), "车削还没汇报");
    }

    /** 从下游单据（加工单）追踪时，不把上游的销售订单显示成「待生成」。 */
    @Test
    void routeFromDownstreamSeedSkipsUpstreamPendingSteps() {
        List<ProcessTraceService.Doc> docs = List.of(
                doc("MANU_ORDER", "MO-001", "已审核"),
                doc("PROCESS_REPORT", "GX-001", "已审核", "MO-001"));

        ProcessTraceService.TraceResult result = service.trace("MO-001", docs);

        assertEquals("MANU_ORDER", result.route().get(0).panelCode());
    }

    /** 工序汇报单的追踪步骤要带工序名称，路线才看得懂。 */
    @Test
    void processReportStepCarriesProcessName() {
        ProcessTraceService.Doc gx = new ProcessTraceService.Doc("PROCESS_REPORT", "GX-001", "已审核",
                List.of("MO-001"), "PX001", "下料", List.of());
        List<ProcessTraceService.Doc> docs = List.of(
                doc("MANU_ORDER", "MO-001", "已审核"), gx);

        ProcessTraceService.TraceResult result = service.trace("MO-001", docs);

        ProcessTraceService.Step step = result.steps().stream()
                .filter(s -> "GX-001".equals(s.docNo())).findFirst().orElseThrow();
        assertEquals("下料", step.processName());
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

    /**
     * 并行排产线：一个订单行一条线，各走各的加工单；排产把该产品全部工序聚成一个节点，
     * 已排的工序带批次单号，后道还没开工的标「待生成」（一次排产，不是一道工序一个节点）。
     */
    /**
     * 首检单必须跟着自己的加工单：委外线的首检单不能串进自制线，作废单也不该画出来。
     * 实测问题：一张销售订单同时有自制行和委外行时，每道工序下面挂了 5 张首件报检单。
     */
    @Test
    void firstInspectBranchesStayWithOwnManufacturingOrder() {
        ProcessTraceService.Doc mo = new ProcessTraceService.Doc("MANU_ORDER", "SO-1-1", "已审核",
                List.of("SO-1"), "", "",
                List.of(new ProcessTraceService.Process("PX001", "车削")));
        ProcessTraceService.Doc batch = new ProcessTraceService.Doc("DISPATCH", "PG-1", "已审核",
                List.of("SO-1-1"), "PX001", "车削", List.of());
        ProcessTraceService.Doc report = new ProcessTraceService.Doc("PROCESS_REPORT", "GX-1", "已审核",
                List.of("SO-1-1"), "PX001", "车削", List.of());
        ProcessTraceService.Doc activeInspect = new ProcessTraceService.Doc("FIRST_INSPECT", "SJ-1", "已审核",
                List.of("SO-1-1"), "", "", List.of(new ProcessTraceService.Process("PX001", "车削")));
        ProcessTraceService.Doc voidInspect = new ProcessTraceService.Doc("FIRST_INSPECT", "SJ-OLD", "作废",
                List.of("SO-1-1"), "", "", List.of(new ProcessTraceService.Process("PX001", "车削")));
        ProcessTraceService.Doc outsourceOrder = new ProcessTraceService.Doc("OUTSOURCE_ORDER", "WW-1", "已审核",
                List.of("SO-1"));
        ProcessTraceService.Doc outsourceInspect = new ProcessTraceService.Doc("FIRST_INSPECT", "SJ-WW", "已审核",
                List.of("WW-1"), "", "", List.of(new ProcessTraceService.Process("PX001", "车削")));

        ProcessTraceService.TraceResult result = service.trace("SO-1", List.of(
                doc("SO_ORDER", "SO-1", "已审核"), mo, batch, report,
                activeInspect, voidInspect, outsourceOrder, outsourceInspect), true);

        assertEquals(1, result.lanes().size());
        ProcessTraceService.RouteNode reportNode = result.lanes().get(0).nodes().stream()
                .filter(n -> "PROCESS_REPORT".equals(n.panelCode())).findFirst().orElseThrow();
        List<ProcessTraceService.RouteNode> sub = reportNode.branches().get(0).branches();
        List<String> inspects = sub.stream()
                .filter(n -> "FIRST_INSPECT".equals(n.panelCode()))
                .map(ProcessTraceService.RouteNode::docNo).toList();
        assertEquals(List.of("SJ-1"), inspects,
                "一道工序只该挂自己加工单、且未作废的那张首检单");
    }

    @Test
    void lanesSplitByOrderLineAndPlanNodeHoldsAllProcesses() {
        ProcessTraceService.Doc mo1 = new ProcessTraceService.Doc("MANU_ORDER", "SO-1-1", "已审核",
                List.of("SO-1"), "", "",
                List.of(new ProcessTraceService.Process("PX001", "车削"),
                        new ProcessTraceService.Process("PX002", "钻孔"),
                        new ProcessTraceService.Process("PX003", "抛光")));
        ProcessTraceService.Doc mo2 = new ProcessTraceService.Doc("MANU_ORDER", "SO-1-2", "已审核",
                List.of("SO-1"), "", "",
                List.of(new ProcessTraceService.Process("PX004", "下料")));
        ProcessTraceService.Doc batch1 = new ProcessTraceService.Doc("DISPATCH", "PG-1", "已审核",
                List.of("SO-1-1"), "PX001", "车削", List.of());
        ProcessTraceService.Doc batch2 = new ProcessTraceService.Doc("DISPATCH", "PG-2", "已审核",
                List.of("SO-1-2"), "PX004", "下料", List.of());
        List<ProcessTraceService.Doc> docs = List.of(
                doc("SO_ORDER", "SO-1", "已审核"), mo1, mo2, batch1, batch2);

        ProcessTraceService.TraceResult result = service.trace("SO-1", docs, true);

        assertEquals(2, result.lanes().size(), "两个订单行就是两条并行线");
        ProcessTraceService.Lane lane1 = result.lanes().get(0);
        assertEquals("SO-1-1", lane1.moNo());
        // 排产在加工单之前，且只有一个排产节点
        assertEquals("DISPATCH", lane1.nodes().get(0).panelCode());
        assertEquals("MANU_ORDER", lane1.nodes().get(1).panelCode());
        assertEquals(1, lane1.nodes().stream().filter(n -> "DISPATCH".equals(n.panelCode())).count());
        // 三道工序都挂在这个排产节点下：第一道已排，后两道待生成
        List<ProcessTraceService.RouteNode> branches = lane1.nodes().get(0).branches();
        assertEquals(3, branches.size());
        assertTrue(branches.get(0).present());
        assertEquals("PG-1", branches.get(0).docNo());
        assertFalse(branches.get(1).present());
        assertFalse(branches.get(2).present());
        // 另一条线只有自己那道工序，不串单
        assertEquals(1, result.lanes().get(1).nodes().get(0).branches().size());
        // 工序汇报单按加工单收成一格：三道工序挂在同一个节点下，未报的按前后顺序标生产中/待生产
        List<ProcessTraceService.RouteNode> reports = lane1.nodes().stream()
                .filter(n -> "PROCESS_REPORT".equals(n.panelCode())).toList();
        assertEquals(1, reports.size(), "一张加工单的汇报只占一个节点");
        assertEquals(3, reports.get(0).branches().size());
        assertEquals("生产中", reports.get(0).branches().get(0).status(), "第一道轮到报工");
        assertEquals("待生产", reports.get(0).branches().get(1).status(), "前道没报完，后道待生产");
    }

    /** 作废单据不算卡点：它已经被后续单据取代。 */
    @Test
    void voidedDocumentIsNotABlocker() {
        ProcessTraceService.Doc so = doc("SO_ORDER", "SO-001", "已审核");
        ProcessTraceService.Doc mo = new ProcessTraceService.Doc("MANU_ORDER", "MO-001", "生产中",
                List.of("SO-001"), "", "", List.of(new ProcessTraceService.Process("PX001", "下料")));
        ProcessTraceService.Doc voided = new ProcessTraceService.Doc("FIRST_INSPECT", "SJ-001", "作废",
                List.of("MO-001"), "", "", List.of(new ProcessTraceService.Process("PX001", "下料")));

        ProcessTraceService.TraceResult result = service.trace("SO-001", List.of(so, mo, voided), true);

        assertTrue(!"SJ-001".equals(result.stalledDocNo()),
                "作废单不该被当成卡点，实际：" + result.stalledDocNo());
    }

    /** 工序分支里要先看到「排产批次」再看到「报工/首检」：报工必须挂在已排批次上。 */
    @Test
    void processBranchStartsWithBatchChip() {
        ProcessTraceService.Doc mo = new ProcessTraceService.Doc("MANU_ORDER", "SO-1-1", "已审核",
                List.of("SO-1"), "", "",
                List.of(new ProcessTraceService.Process("PX001", "车削")));
        ProcessTraceService.Doc batch = new ProcessTraceService.Doc("DISPATCH", "PG-1", "已审核",
                List.of("SO-1-1"), "PX001", "车削", List.of());
        ProcessTraceService.Doc report = new ProcessTraceService.Doc("PROCESS_REPORT", "GX-1", "已审核",
                List.of("SO-1-1"), "PX001", "车削", List.of());
        ProcessTraceService.TraceResult result = service.trace("SO-1",
                List.of(doc("SO_ORDER", "SO-1", "已审核"), mo, batch, report), true);

        ProcessTraceService.RouteNode reports = result.lanes().get(0).nodes().stream()
                .filter(n -> "PROCESS_REPORT".equals(n.panelCode())).findFirst().orElseThrow();
        List<ProcessTraceService.RouteNode> chips = reports.branches().get(0).branches();
        assertEquals("DISPATCH", chips.get(0).panelCode(), "第一个标签是排产批次");
        assertEquals("PG-1", chips.get(0).docNo());
        assertTrue(chips.size() >= 2, "批次之后还有首检标签");
        assertEquals("FIRST_INSPECT", chips.get(1).panelCode(), "第二个标签是首件报检");
    }

    /** 排产批次「中止」是主动调产能（未执行量已放回可排产量），不算流程卡点。 */
    @Test
    void abortedBatchIsNotABlocker() {
        ProcessTraceService.Doc so = doc("SO_ORDER", "SO-001", "已审核");
        ProcessTraceService.Doc mo = new ProcessTraceService.Doc("MANU_ORDER", "MO-001", "已完工",
                List.of("SO-001"), "", "", List.of(new ProcessTraceService.Process("PX001", "下料")));
        ProcessTraceService.Doc aborted = doc("DISPATCH", "PG-001", "中止", "MO-001");
        ProcessTraceService.Doc ok = doc("DISPATCH", "PG-002", "已审核", "MO-001");

        ProcessTraceService.TraceResult result = service.trace("SO-001", List.of(so, mo, aborted, ok), true);

        assertTrue(!"PG-001".equals(result.stalledDocNo()),
                "中止的批次不该被当成卡点，实际：" + result.stalledDocNo());
        ProcessTraceService.RouteNode node = result.route().stream()
                .filter(n -> "PG-001".equals(n.docNo())).findFirst().orElseThrow();
        assertEquals("已中止", node.state(), "中止批次在图上按已中止显示，不是进行中");
    }

    /** 一道工序分批投产：排产节点的分支要按批次全部列出来，不能只留第一条。 */
    @Test
    void planNodeListsEveryBatchOfSameProcess() {
        ProcessTraceService.Doc mo = new ProcessTraceService.Doc("MANU_ORDER", "MO-001", "已完工",
                List.of("SO-001"), "", "", List.of(new ProcessTraceService.Process("PX002", "车削")));
        ProcessTraceService.Doc b1 = new ProcessTraceService.Doc("DISPATCH", "PG-001", "已审核",
                List.of("MO-001"), "PX002", "车削", List.of());
        ProcessTraceService.Doc b2 = new ProcessTraceService.Doc("DISPATCH", "PG-002", "已审核",
                List.of("MO-001"), "PX002", "车削", List.of());

        ProcessTraceService.TraceResult result = service.trace("SO-001",
                List.of(doc("SO_ORDER", "SO-001", "已审核"), mo, b1, b2), true);

        ProcessTraceService.RouteNode plan = result.lanes().get(0).nodes().get(0);
        assertEquals("DISPATCH", plan.panelCode());
        List<String> batchNos = plan.branches().stream().map(ProcessTraceService.RouteNode::docNo).toList();
        assertEquals(List.of("PG-001", "PG-002"), batchNos, "同工序的两个批次都要出现在分支里");
    }

    /** 老路径（加工单直接报工、没落批次）：排产分支写「无批次报工」，不能写「待排产」。 */
    @Test
    void reportedProcessWithoutBatchIsNotMarkedPending() {
        ProcessTraceService.Doc mo = new ProcessTraceService.Doc("MANU_ORDER", "MO-001", "已完工",
                List.of("SO-001"), "", "", List.of(new ProcessTraceService.Process("PX002", "车削")));
        ProcessTraceService.Doc report = new ProcessTraceService.Doc("PROCESS_REPORT", "GX-001", "已审核",
                List.of("MO-001"), "PX002", "车削", List.of());

        ProcessTraceService.TraceResult result = service.trace("SO-001",
                List.of(doc("SO_ORDER", "SO-001", "已审核"), mo, report), true);

        // 没有批次时排产节点整体是「待排产」，工序口径落在汇报节点的分支上
        ProcessTraceService.RouteNode reportNode = result.lanes().get(0).nodes().stream()
                .filter(n -> "PROCESS_REPORT".equals(n.panelCode())).findFirst().orElseThrow();
        // 汇报节点收成一格：分支是「工序」，工序分支里第一个位置是排产批次
        ProcessTraceService.RouteNode procBranch = reportNode.branches().get(0);
        ProcessTraceService.RouteNode branch = procBranch.branches().get(0);
        assertEquals("DISPATCH", branch.panelCode(), "工序分支第一个是排产批次位");
        assertEquals("无批次报工", branch.status());
        assertEquals("完成", branch.state());
    }

    /** 领料申请单不再进路线：现场领料直接出材料出库单。 */
    @Test
    void materialReqIsNotARouteNode() {
        ProcessTraceService.Doc mo = new ProcessTraceService.Doc("MANU_ORDER", "MO-001", "已完工",
                List.of("SO-001"), "", "", List.of(new ProcessTraceService.Process("PX002", "车削")));
        ProcessTraceService.Doc req = doc("MATERIAL_REQ", "LL-001", "已审核", "MO-001");
        ProcessTraceService.Doc out = doc("MATERIAL_OUT", "CL-001", "已审核", "MO-001");

        ProcessTraceService.TraceResult result = service.trace("SO-001",
                List.of(doc("SO_ORDER", "SO-001", "已审核"), mo, req, out), true);

        List<String> codes = result.route().stream().map(ProcessTraceService.RouteNode::panelCode).toList();
        assertFalse(codes.contains("MATERIAL_REQ"), "路线里不该再有领料申请单：" + codes);
        assertTrue(codes.contains("MATERIAL_OUT"), "材料出库单仍在");
    }
}
