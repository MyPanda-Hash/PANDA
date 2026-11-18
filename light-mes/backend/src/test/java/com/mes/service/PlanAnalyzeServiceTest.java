package com.mes.service;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 计划分析引擎：一张销售订单 → 成品层 → BOM 展开 → 物料缺口 → 工艺路线 → 风险。
 *
 * <p>纯计算契约：调用方负责从库读数据传进来，引擎不碰数据库，
 * 便于用固定 fixture 断言输出形状。
 *
 * <p>关键语义：成品自身库存为 0 不算阻塞（排产就是把它做出来）；
 * 只有「没 BOM / 没存货档案」才阻塞排产。
 */
class PlanAnalyzeServiceTest {

    private final PlanAnalyzeService service = new PlanAnalyzeService();

    // ---------- fixture ----------

    private PlanAnalyzeService.Data fullData() {
        Map<String, PlanAnalyzeService.ItemMaster> items = new LinkedHashMap<>();
        items.put("CP001", item("CP001", "铝棒 Φ80", "Φ80×3000", "件", "自制", "GY-001"));
        items.put("CL002", item("CL002", "6061铝锭", "A00", "kg", "外购", ""));
        items.put("CL004", item("CL004", "切削液", "20L/桶", "升", "外购", ""));

        Map<String, PlanAnalyzeService.BomHeader> bom = new LinkedHashMap<>();
        bom.put("CP001", new PlanAnalyzeService.BomHeader("BOM-001", "CP001", "GY-001",
                List.of(bomLine("CL002", "6061铝锭", "kg", "1.05", "5"),
                        bomLine("CL004", "切削液", "升", "0.02", "2"))));

        Map<String, List<PlanAnalyzeService.RouteStep>> routes = new LinkedHashMap<>();
        routes.put("GY-001", List.of(
                step(1, "PX001", "下料", "自制", "熔铸车间", false, "100"),
                step(2, "PX002", "车削", "自制", "熔铸车间", true, "98"),
                step(3, "PX007", "检验", "自制", "测试车间", false, "100")));

        Map<String, BigDecimal> stock = new LinkedHashMap<>();
        stock.put("CL002", bd("3000"));
        stock.put("CL004", bd("280.6"));

        return new PlanAnalyzeService.Data(items, bom, routes, stock,
                Set.of("CL002", "CL004"), Map.of());
    }

    private PlanAnalyzeService.OrderHeader order() {
        return new PlanAnalyzeService.OrderHeader("SO-2026-08-0001", "华东铝业",
                "2026-08-14", "2026-08-20", "特急");
    }

    private List<PlanAnalyzeService.OrderLine> lines() {
        return List.of(new PlanAnalyzeService.OrderLine("CP001", "铝棒 Φ80", "Φ80×3000",
                "件", bd("200"), "2026-08-20"));
    }

    // ---------- 断言 ----------

    @Test
    void stockedMaterialsAllowPlanning() {
        PlanAnalyzeService.Result r = service.analyze(order(), lines(), fullData());

        assertEquals(0, bd("0").compareTo(r.products().get(0).stockQty()), "成品库存 0");
        assertTrue(r.products().get(0).shortQty().signum() > 0, "成品现存量不足应先记账，但不阻塞");
        assertTrue(r.summary().readyToPlan(), "物料齐 + BOM/工艺齐全，应可排产");
        assertEquals(0, r.summary().blockingCount());
        assertTrue(r.risks().isEmpty(), "不应有结构性风险：" + r.risks());
    }

    @Test
    void materialQuantityAppliesScrapRate() {
        PlanAnalyzeService.Result r = service.analyze(order(), lines(), fullData());

        PlanAnalyzeService.MaterialLine cl002 = material(r, "CL002");
        // 200 × 1.05 × (1 + 5%) = 220.5
        assertEquals(0, bd("220.5").compareTo(cl002.grossQty()));
        assertEquals(1, cl002.level());
        assertEquals("CP001 > CL002", cl002.path());
        assertFalse(cl002.shortage(), "库存 3000 足够 220.5");

        PlanAnalyzeService.MaterialLine cl004 = material(r, "CL004");
        // 200 × 0.02 × 1.02 = 4.08
        assertEquals(0, bd("4.08").compareTo(cl004.grossQty()));
    }

    @Test
    void shortageProductExpandsItsOwnChildren() {
        Map<String, PlanAnalyzeService.ItemMaster> items = new LinkedHashMap<>();
        items.put("CP001", item("CP001", "铝棒 Φ80", "Φ80×3000", "件", "自制", "GY-001"));
        items.put("CL002", item("CL002", "半成品 A", "", "kg", "自制", ""));
        items.put("CL003", item("CL003", "6063铝棒", "Φ120", "kg", "外购", ""));

        Map<String, PlanAnalyzeService.BomHeader> bom = new LinkedHashMap<>();
        bom.put("CP001", new PlanAnalyzeService.BomHeader("BOM-001", "CP001", "GY-001",
                List.of(bomLine("CL002", "半成品 A", "kg", "1", "0"))));
        bom.put("CL002", new PlanAnalyzeService.BomHeader("BOM-002", "CL002", "",
                List.of(bomLine("CL003", "6063铝棒", "kg", "2", "0"))));

        Map<String, BigDecimal> stock = new LinkedHashMap<>();
        stock.put("CL002", bd("10"));
        stock.put("CL003", bd("500"));

        PlanAnalyzeService.Data data = new PlanAnalyzeService.Data(items, bom, Map.of(), stock,
                Set.of("CL002", "CL003"), Map.of());

        PlanAnalyzeService.Result r = service.analyze(order(), lines(), data);

        PlanAnalyzeService.MaterialLine cl002 = material(r, "CL002");
        assertTrue(cl002.shortage(), "CL002 需 200 库存 10，应缺料");
        assertTrue(cl002.expandable(), "自制件缺料应标记可继续展开");

        PlanAnalyzeService.MaterialLine cl003 = material(r, "CL003");
        assertEquals(2, cl003.level());
        assertEquals("CP001 > CL002 > CL003", cl003.path());
        // 缺料 190 × 2 = 380，库存 500 足
        assertEquals(0, bd("380").compareTo(cl003.grossQty()));
        assertFalse(cl003.shortage());
    }

    @Test
    void quotaIsDividedByBomBaseQuantity() {
        // 生产数量是 BOM 的基准产出：基准 10 件、定额 25kg → 单件 2.5kg
        assertEquals(0, bd("2.5").compareTo(
                PlanAnalyzeService.unitUsage(bd("25"), bd("10"))));
        // 基准缺省/为 0 时退回「定额即单件」，不能除零
        assertEquals(0, bd("25").compareTo(PlanAnalyzeService.unitUsage(bd("25"), null)));
        assertEquals(0, bd("25").compareTo(PlanAnalyzeService.unitUsage(bd("25"), bd("0"))));
    }

    @Test
    void baseQuantityScalesMaterialRequirement() {
        // 基准 10 件、定额 25kg、损耗 0 → 生产 20 件需要 50kg，不是 500kg
        Map<String, PlanAnalyzeService.ItemMaster> items = new LinkedHashMap<>();
        items.put("CP001", item("CP001", "铝棒 Φ80", "", "件", "自制", "GY-001"));
        items.put("CL001", item("CL001", "45#圆钢", "", "kg", "外购", ""));
        Map<String, PlanAnalyzeService.BomHeader> bom = new LinkedHashMap<>();
        bom.put("CP001", new PlanAnalyzeService.BomHeader("BOM-001", "CP001", "GY-001",
                List.of(new PlanAnalyzeService.BomLine("CL001", "45#圆钢", "kg",
                        PlanAnalyzeService.unitUsage(bd("25"), bd("10")), bd("0")))));
        PlanAnalyzeService.Data data = new PlanAnalyzeService.Data(items, bom, Map.of(),
                Map.of("CL001", bd("1000")), Set.of("CL001"), Map.of());

        PlanAnalyzeService.Result r = service.analyze(order(),
                List.of(new PlanAnalyzeService.OrderLine("CP001", "铝棒 Φ80", "", "件", bd("20"), "")),
                data);

        PlanAnalyzeService.MaterialLine cl001 = material(r, "CL001");
        assertEquals(0, bd("50").compareTo(cl001.grossQty()), "20 件 × 2.5kg/件 = 50kg");
    }

    @Test
    void missingBomIsBlockingRisk() {
        Map<String, PlanAnalyzeService.ItemMaster> items = Map.of(
                "CP009", item("CP009", "无 BOM 产品", "", "件", "自制", ""));
        PlanAnalyzeService.Data data = new PlanAnalyzeService.Data(items, Map.of(), Map.of(),
                Map.of(), Set.of(), Map.of());

        List<PlanAnalyzeService.OrderLine> ls = List.of(new PlanAnalyzeService.OrderLine(
                "CP009", "无 BOM 产品", "", "件", bd("10"), "2026-08-20"));
        PlanAnalyzeService.Result r = service.analyze(order(), ls, data);

        assertFalse(r.summary().readyToPlan(), "没 BOM 不能排产");
        assertTrue(risk(r, "NO_BOM"), "应报 NO_BOM");
        assertEquals(1, r.summary().blockingCount());
    }

    @Test
    void missingRouteIsRiskButNotBlocking() {
        Map<String, PlanAnalyzeService.ItemMaster> items = new LinkedHashMap<>();
        items.put("CP001", item("CP001", "铝棒 Φ80", "Φ80×3000", "件", "自制", ""));
        Map<String, PlanAnalyzeService.BomHeader> bom = new LinkedHashMap<>();
        bom.put("CP001", new PlanAnalyzeService.BomHeader("BOM-001", "CP001", "",
                List.of(bomLine("CL002", "6061铝锭", "kg", "1", "0"))));

        PlanAnalyzeService.Data data = new PlanAnalyzeService.Data(items, bom, Map.of(),
                Map.of("CL002", bd("1000")), Set.of("CL002"), Map.of());

        PlanAnalyzeService.Result r = service.analyze(order(), lines(), data);

        assertTrue(risk(r, "NO_ROUTE"), "应报 NO_ROUTE");
        assertFalse(r.routing().configured());
        assertTrue(r.summary().readyToPlan(), "缺工艺路线不阻塞排产");
    }

    @Test
    void routeStepsAreOrderedAndFlagsCollected() {
        PlanAnalyzeService.Result r = service.analyze(order(), lines(), fullData());

        assertTrue(r.routing().configured());
        assertEquals("GY-001", r.routing().routeCode());
        assertEquals(3, r.routing().steps().size());
        assertEquals(1, r.routing().steps().get(0).seq());
        assertEquals("下料", r.routing().steps().get(0).opName());
        assertEquals(List.of("车削"), r.routing().keySteps());
    }

    /** 订单里有几个成品，就返回几条工艺路线，各自挂自己的路线。 */
    @Test
    void oneRoutePerProductInOrder() {
        Map<String, PlanAnalyzeService.ItemMaster> items = new LinkedHashMap<>();
        items.put("CP001", item("CP001", "铝棒 Φ80", "", "件", "自制", "GY-CP001"));
        items.put("CP005", item("CP005", "轴套 C", "", "件", "自制", "GY-CP005"));

        Map<String, PlanAnalyzeService.BomHeader> bom = new LinkedHashMap<>();
        bom.put("CP001", new PlanAnalyzeService.BomHeader("BOM-001", "CP001", "GY-CP001",
                List.of(bomLine("CL002", "6061铝锭", "kg", "1", "0"))));
        bom.put("CP005", new PlanAnalyzeService.BomHeader("BOM-005", "CP005", "GY-CP005",
                List.of(bomLine("CL002", "6061铝锭", "kg", "0.5", "0"))));

        Map<String, List<PlanAnalyzeService.RouteStep>> routes = new LinkedHashMap<>();
        routes.put("GY-CP001", List.of(step(1, "PX001", "下料", "自制", "熔铸车间", false, "100")));
        routes.put("GY-CP005", List.of(
                step(1, "PX002", "车削", "自制", "熔铸车间", true, "98"),
                step(2, "PX010", "抛光", "委外", "精整车间", false, "100")));

        PlanAnalyzeService.Data data = new PlanAnalyzeService.Data(items, bom, routes,
                Map.of("CL002", bd("1000")), Set.of("CL002"), Map.of());

        PlanAnalyzeService.Result r = service.analyze(order(), List.of(
                new PlanAnalyzeService.OrderLine("CP001", "铝棒 Φ80", "", "件", bd("10"), ""),
                new PlanAnalyzeService.OrderLine("CP005", "轴套 C", "", "件", bd("20"), "")), data);

        assertEquals(2, r.routings().size(), "两个成品各一条路线");
        assertEquals("CP001", r.routings().get(0).productCode());
        assertEquals("GY-CP001", r.routings().get(0).routeCode());
        assertEquals("CP005", r.routings().get(1).productCode());
        assertEquals("GY-CP005", r.routings().get(1).routeCode());
        assertEquals(2, r.routings().get(1).steps().size());
        assertEquals(List.of("抛光"), r.routings().get(1).outsourced());
        assertEquals(List.of("车削"), r.routings().get(1).keySteps());
    }

    @Test
    void draftRouteIsReportedAsDraftNotMissing() {
        // 工艺路线是审批面板：新建即草稿。草稿不能驱动排产，但也不该说成「没有路线」，
        // 否则用户建完路线回来还看到「未配置」，以为没保存成功。
        Map<String, PlanAnalyzeService.ItemMaster> items = new LinkedHashMap<>();
        items.put("CP001", item("CP001", "铝棒 Φ80", "", "件", "自制", "GY-CP001"));
        Map<String, PlanAnalyzeService.BomHeader> bom = new LinkedHashMap<>();
        bom.put("CP001", new PlanAnalyzeService.BomHeader("BOM-001", "CP001", "GY-CP001",
                List.of(bomLine("CL002", "6061铝锭", "kg", "1", "0"))));
        Map<String, String> status = new LinkedHashMap<>();
        status.put("GY-CP001", "草稿");
        PlanAnalyzeService.Data data = new PlanAnalyzeService.Data(items, bom, Map.of(),
                Map.of("CL002", bd("1000")), Set.of("CL002"), Map.of(), status);

        PlanAnalyzeService.Result r = service.analyze(order(), lines(), data);

        assertFalse(r.products().get(0).routingConfigured(), "草稿不能算已配置");
        assertEquals("草稿", r.products().get(0).routeStatus());
        assertTrue(risk(r, "ROUTE_DRAFT"), "应报 ROUTE_DRAFT 而不是 NO_ROUTE");
        assertFalse(risk(r, "NO_ROUTE"));
    }

    @Test
    void approvedRouteIsConfigured() {
        Map<String, PlanAnalyzeService.ItemMaster> items = new LinkedHashMap<>();
        items.put("CP001", item("CP001", "铝棒 Φ80", "", "件", "自制", "GY-CP001"));
        Map<String, PlanAnalyzeService.BomHeader> bom = new LinkedHashMap<>();
        bom.put("CP001", new PlanAnalyzeService.BomHeader("BOM-001", "CP001", "GY-CP001",
                List.of(bomLine("CL002", "6061铝锭", "kg", "1", "0"))));
        Map<String, String> status = new LinkedHashMap<>();
        status.put("GY-CP001", "已审核");
        PlanAnalyzeService.Data data = new PlanAnalyzeService.Data(items, bom,
                Map.of("GY-CP001", List.of(step(1, "PX001", "下料", "自制", "熔铸车间", false, "100"))),
                Map.of("CL002", bd("1000")), Set.of("CL002"), Map.of(), status);

        PlanAnalyzeService.Result r = service.analyze(order(), lines(), data);

        assertTrue(r.products().get(0).routingConfigured());
        assertEquals("已审核", r.products().get(0).routeStatus());
    }

    @Test
    void outsourcedStepIsListedSeparately() {
        Map<String, List<PlanAnalyzeService.RouteStep>> routes = new LinkedHashMap<>();
        routes.put("GY-001", List.of(
                step(1, "PX001", "下料", "自制", "熔铸车间", false, "100"),
                step(2, "PX005", "热处理", "委外", "轧制车间", true, "100")));
        PlanAnalyzeService.Data base = fullData();
        PlanAnalyzeService.Data data = new PlanAnalyzeService.Data(base.items(), base.bom(),
                routes, base.stock(), base.stockRecorded(), base.moByOrder());

        PlanAnalyzeService.Result r = service.analyze(order(), lines(), data);

        assertEquals(List.of("热处理"), r.routing().outsourced());
    }

    @Test
    void existingManuOrderIsReported() {
        PlanAnalyzeService.Data base = fullData();
        Map<String, List<String>> moByOrder = new LinkedHashMap<>();
        moByOrder.put("SO-2026-08-0001", List.of("MO-2026-08-0007"));
        PlanAnalyzeService.Data data = new PlanAnalyzeService.Data(base.items(), base.bom(),
                base.routes(), base.stock(), base.stockRecorded(), moByOrder);

        PlanAnalyzeService.Result r = service.analyze(order(), lines(), data);

        assertTrue(risk(r, "HAS_MO"), "已生成加工单要给提示");
        assertTrue(r.risks().stream().anyMatch(x -> x.item().contains("MO-2026-08-0007")));
    }

    @Test
    void stockRecordMissingIsDistinctFromZero() {
        Map<String, PlanAnalyzeService.ItemMaster> items = new LinkedHashMap<>();
        items.put("CP001", item("CP001", "铝棒 Φ80", "", "件", "自制", "GY-001"));
        Map<String, PlanAnalyzeService.BomHeader> bom = new LinkedHashMap<>();
        bom.put("CP001", new PlanAnalyzeService.BomHeader("BOM-001", "CP001", "GY-001",
                List.of(bomLine("CL009", "全新物料", "kg", "1", "0"))));

        PlanAnalyzeService.Data data = new PlanAnalyzeService.Data(items, bom, Map.of(),
                Map.of(), Set.of(), Map.of());

        PlanAnalyzeService.Result r = service.analyze(order(), lines(), data);

        PlanAnalyzeService.MaterialLine cl009 = material(r, "CL009");
        assertFalse(cl009.stockRecordExists(), "无库存记录要能分辨");
        assertEquals(0, BigDecimal.ZERO.compareTo(cl009.stockQty()));
        assertTrue(cl009.shortage(), "无记录即无货，应缺料");
    }

    @Test
    void unknownItemIsBlockingRisk() {
        PlanAnalyzeService.Data data = new PlanAnalyzeService.Data(Map.of(), Map.of(), Map.of(),
                Map.of(), Set.of(), Map.of());
        List<PlanAnalyzeService.OrderLine> ls = List.of(new PlanAnalyzeService.OrderLine(
                "CP999", "不存在", "", "件", bd("5"), "2026-08-20"));

        PlanAnalyzeService.Result r = service.analyze(order(), ls, data);

        assertTrue(risk(r, "NO_ITEM"), "存货档案缺失应报 NO_ITEM");
        assertFalse(r.summary().readyToPlan());
    }

    // ---------- helpers ----------

    private static PlanAnalyzeService.MaterialLine material(PlanAnalyzeService.Result r, String code) {
        return r.materials().stream().filter(m -> m.code().equals(code)).findFirst()
                .orElseThrow(() -> new AssertionError("找不到物料行 " + code + "，实际="
                        + r.materials().stream().map(PlanAnalyzeService.MaterialLine::code).toList()));
    }

    private static boolean risk(PlanAnalyzeService.Result r, String code) {
        return r.risks().stream().anyMatch(x -> x.code().equals(code));
    }

    private static PlanAnalyzeService.ItemMaster item(String code, String name, String spec,
                                                      String unit, String attr, String route) {
        return new PlanAnalyzeService.ItemMaster(code, name, spec, unit, attr, route);
    }

    private static PlanAnalyzeService.BomLine bomLine(String code, String name, String unit,
                                                      String qty, String scrap) {
        return new PlanAnalyzeService.BomLine(code, name, unit, bd(qty), bd(scrap));
    }

    private static PlanAnalyzeService.RouteStep step(int seq, String code, String name,
                                                     String workType, String workshop,
                                                     boolean key, String rate) {
        return new PlanAnalyzeService.RouteStep(seq, code, name, workType, workshop, key, bd(rate));
    }

    private static BigDecimal bd(String v) {
        return new BigDecimal(v);
    }
}
