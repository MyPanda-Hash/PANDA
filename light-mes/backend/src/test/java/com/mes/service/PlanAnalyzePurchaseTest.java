package com.mes.service;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 计划分析的后续动作：从缺料里挑出「该去买」的行。
 *
 * <p>规则：只有同时满足「外购件」「有净需求」的物料行才进请购单。
 * 自制件缺料要自己做（走生产），不生成请购；库存够的不用买。
 */
class PlanAnalyzePurchaseTest {

    private final PlanAnalyzeService service = new PlanAnalyzeService();

    private PlanAnalyzeService.OrderHeader order() {
        return new PlanAnalyzeService.OrderHeader("SO-2026-08-0001", "华东铝业",
                "2026-08-14", "2026-08-20", "特急");
    }

    /** 一个成品，BOM 下三个子件：外购缺料、外购充足、自制缺料。 */
    private PlanAnalyzeService.Result result() {
        Map<String, PlanAnalyzeService.ItemMaster> items = new LinkedHashMap<>();
        items.put("CP001", new PlanAnalyzeService.ItemMaster(
                "CP001", "铝棒 Φ80", "Φ80×3000", "件", "自制", "GY-001"));
        items.put("CL001", new PlanAnalyzeService.ItemMaster(
                "CL001", "45#圆钢", "Φ60", "kg", "外购", ""));
        items.put("CL004", new PlanAnalyzeService.ItemMaster(
                "CL004", "切削液", "20L/桶", "升", "外购", ""));
        items.put("CL002", new PlanAnalyzeService.ItemMaster(
                "CL002", "半成品 A", "", "kg", "自制", ""));

        Map<String, PlanAnalyzeService.BomHeader> bom = new LinkedHashMap<>();
        bom.put("CP001", new PlanAnalyzeService.BomHeader("BOM-001", "CP001", "GY-001",
                List.of(new PlanAnalyzeService.BomLine("CL001", "45#圆钢", "kg", bd("2"), bd("0")),
                        new PlanAnalyzeService.BomLine("CL004", "切削液", "升", bd("0.02"), bd("0")),
                        new PlanAnalyzeService.BomLine("CL002", "半成品 A", "kg", bd("1"), bd("0")))));

        Map<String, BigDecimal> stock = new LinkedHashMap<>();
        stock.put("CL001", bd("50"));    // 需 200，缺 150 → 该买
        stock.put("CL004", bd("999"));   // 充足 → 不买
        stock.put("CL002", bd("10"));    // 自制缺料 → 自己做，不买

        PlanAnalyzeService.Data data = new PlanAnalyzeService.Data(
                items, bom, Map.of(), stock, Set.of("CL001", "CL004", "CL002"), Map.of());

        return service.analyze(order(),
                List.of(new PlanAnalyzeService.OrderLine(
                        "CP001", "铝棒 Φ80", "Φ80×3000", "件", bd("100"), "2026-08-20")),
                data);
    }

    @Test
    void onlyPurchasedShortageRowsArePurchasable() {
        // 只请求可请购的那一行
        List<PlanAnalyzeService.MaterialLine> lines =
                service.purchasableLines(result(), List.of("CL001"));

        assertEquals(1, lines.size());
        assertEquals("CL001", lines.get(0).code());
        assertEquals(0, bd("150").compareTo(lines.get(0).netQty()));
    }

    @Test
    void mixedRequestRejectsUnpurchasableCodes() {
        // 混进充足件或自制件时整批拒绝，并点名是哪几个，避免静默丢行
        IllegalArgumentException e = assertThrows(IllegalArgumentException.class,
                () -> service.purchasableLines(result(), List.of("CL001", "CL004", "CL002")));
        assertTrue(e.getMessage().contains("CL004"), e.getMessage());
        assertTrue(e.getMessage().contains("CL002"), e.getMessage());
    }

    @Test
    void otherOwnShortageIsNotPurchasable() {
        // 只请求自制件：不能进请购单
        assertThrows(IllegalArgumentException.class,
                () -> service.purchasableLines(result(), List.of("CL002")));
    }

    @Test
    void stockedItemIsNotPurchasable() {
        assertThrows(IllegalArgumentException.class,
                () -> service.purchasableLines(result(), List.of("CL004")));
    }

    @Test
    void unknownCodeIsRejected() {
        assertThrows(IllegalArgumentException.class,
                () -> service.purchasableLines(result(), List.of("NOT-EXIST")));
    }

    @Test
    void emptyRequestMeansAllPurchasableRows() {
        // 不指定编码时，把全部可请购行都带上（页面「一键全部请购」用）
        List<PlanAnalyzeService.MaterialLine> lines = service.purchasableLines(result(), List.of());
        assertEquals(1, lines.size());
        assertEquals("CL001", lines.get(0).code());
    }

    @Test
    void noPurchaseNeededThrows() {
        Map<String, PlanAnalyzeService.ItemMaster> items = new LinkedHashMap<>();
        items.put("CP001", new PlanAnalyzeService.ItemMaster(
                "CP001", "铝棒 Φ80", "", "件", "自制", "GY-001"));
        items.put("CL004", new PlanAnalyzeService.ItemMaster("CL004", "切削液", "", "升", "外购", ""));
        Map<String, PlanAnalyzeService.BomHeader> bom = new LinkedHashMap<>();
        bom.put("CP001", new PlanAnalyzeService.BomHeader("BOM-001", "CP001", "GY-001",
                List.of(new PlanAnalyzeService.BomLine("CL004", "切削液", "升", bd("1"), bd("0")))));
        PlanAnalyzeService.Data data = new PlanAnalyzeService.Data(
                items, bom, Map.of(), Map.of("CL004", bd("999")), Set.of("CL004"), Map.of());

        PlanAnalyzeService.Result r = service.analyze(order(),
                List.of(new PlanAnalyzeService.OrderLine("CP001", "铝棒 Φ80", "", "件", bd("10"), "")),
                data);

        IllegalArgumentException e = assertThrows(IllegalArgumentException.class,
                () -> service.purchasableLines(r, List.of()));
        assertTrue(e.getMessage().contains("没有需要请购"), e.getMessage());
    }

    private static BigDecimal bd(String v) {
        return new BigDecimal(v);
    }
}
