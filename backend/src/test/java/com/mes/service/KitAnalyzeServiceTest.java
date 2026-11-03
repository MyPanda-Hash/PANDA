package com.mes.service;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 齐套分析：判断某产品能否按需求数量开工，缺哪些料、缺多少。
 * 只分析一层 BOM（直接子件），因为齐套关心的是"这一单现在能不能开工"。
 */
class KitAnalyzeServiceTest {

    private final KitAnalyzeService service = new KitAnalyzeService();

    private Map<String, List<MrpExplosionService.BomLine>> bom() {
        return Map.of("CP001", List.of(
                new MrpExplosionService.BomLine("CP001", "CL001", "铝棒", bd("2"), bd("0")),
                new MrpExplosionService.BomLine("CP001", "CL002", "铝锭", bd("3"), bd("0"))));
    }

    @Test
    void fullyStockedIsReady() {
        KitAnalyzeService.KitResult r = service.analyze("CP001", bd("10"), bom(),
                Map.of("CL001", bd("20"), "CL002", bd("30")));

        assertTrue(r.ready(), "料齐应可开工");
        assertEquals(0, r.shortageCount());
        assertEquals(2, r.lines().size());
    }

    @Test
    void partialStockReportsShortage() {
        KitAnalyzeService.KitResult r = service.analyze("CP001", bd("10"), bom(),
                Map.of("CL001", bd("20"), "CL002", bd("10")));

        assertTrue(!r.ready(), "缺料不应可开工");
        assertEquals(1, r.shortageCount());
        KitAnalyzeService.KitLine cl002 = findLine(r, "CL002");
        assertEquals(0, bd("30").compareTo(cl002.requiredQty()));
        assertEquals(0, bd("10").compareTo(cl002.stockQty()));
        assertEquals(0, bd("20").compareTo(cl002.shortageQty()));
    }

    @Test
    void noStockReportsFullShortage() {
        KitAnalyzeService.KitResult r = service.analyze("CP001", bd("5"), bom(), Map.of());

        assertEquals(2, r.shortageCount());
        assertEquals(0, bd("10").compareTo(findLine(r, "CL001").shortageQty()));
    }

    @Test
    void scrapRateIsAppliedToRequirement() {
        Map<String, List<MrpExplosionService.BomLine>> bom = Map.of("CP001", List.of(
                new MrpExplosionService.BomLine("CP001", "CL001", "铝棒", bd("2"), bd("10"))));

        KitAnalyzeService.KitResult r = service.analyze("CP001", bd("10"), bom, Map.of());

        // 2 * 10 * 1.1 = 22
        assertEquals(0, bd("22").compareTo(findLine(r, "CL001").requiredQty()));
    }

    @Test
    void itemWithoutBomUsesOwnStockAndIsShortWhenInsufficient() {
        // 无 BOM：把成品自身当作唯一需求项，库存 5 不足需求 10，应判不齐套
        KitAnalyzeService.KitResult r = service.analyze("NOBOM", bd("10"),
                Map.of(), Map.of("NOBOM", bd("5")));

        assertTrue(!r.ready(), "库存不足不应判可开工");
        assertEquals(1, r.lines().size());
        assertEquals("NOBOM", r.lines().get(0).itemCode());
        assertEquals(0, bd("5").compareTo(findLine(r, "NOBOM").shortageQty()));
    }

    @Test
    void itemWithoutBomIsReadyWhenStockSufficient() {
        KitAnalyzeService.KitResult r = service.analyze("NOBOM", bd("10"),
                Map.of(), Map.of("NOBOM", bd("15")));

        assertTrue(r.ready(), "库存充足应判可开工");
        assertEquals(0, r.shortageCount());
    }

    @Test
    void itemWithoutBomAndNoStockIsShort() {
        KitAnalyzeService.KitResult r = service.analyze("NOBOM", bd("10"), Map.of(), Map.of());

        assertTrue(!r.ready());
        assertEquals(0, bd("10").compareTo(findLine(r, "NOBOM").shortageQty()));
    }

    @Test
    void zeroDemandProducesNoLines() {
        KitAnalyzeService.KitResult r = service.analyze("CP001", BigDecimal.ZERO, bom(), Map.of());

        assertEquals(0, r.lines().size());
        assertTrue(r.ready());
    }

    @Test
    void maxShortageRatioIsReported() {
        KitAnalyzeService.KitResult r = service.analyze("CP001", bd("10"), bom(),
                Map.of("CL001", bd("20"), "CL002", bd("15")));

        // CL002 需 30 有 15，满足率 50%
        assertEquals(0, bd("50").compareTo(r.minFulfillRate()));
    }

    @Test
    void resultCarriesProductCode() {
        KitAnalyzeService.KitResult r = service.analyze("CP001", bd("10"), bom(), Map.of());

        assertEquals("CP001", r.productCode());
        assertEquals(0, bd("10").compareTo(r.demandQty()));
    }

    private KitAnalyzeService.KitLine findLine(KitAnalyzeService.KitResult r, String code) {
        return r.lines().stream().filter(l -> code.equals(l.itemCode())).findFirst().orElse(null);
    }

    private static BigDecimal bd(String v) {
        return new BigDecimal(v);
    }
}
