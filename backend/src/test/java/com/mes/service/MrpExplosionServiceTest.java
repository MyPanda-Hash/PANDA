package com.mes.service;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * MRP 的 BOM 递归展开：从成品需求逐层展开到用料，扣减库存后给出采购建议。
 * 支持多层 BOM、损耗率、循环引用保护、缺 BOM 时按成品自身处理。
 */
class MrpExplosionServiceTest {

    private final MrpExplosionService service = new MrpExplosionService();

    /** 单层 BOM：成品 CP001 用 2 个 CL001。 */
    private Map<String, List<MrpExplosionService.BomLine>> singleLevel() {
        return Map.of("CP001", List.of(
                new MrpExplosionService.BomLine("CP001", "CL001", "铝棒", bd("2"), bd("0"))));
    }

    @Test
    void explodesSingleLevel() {
        List<MrpExplosionService.Requirement> reqs = service.explode(
                Map.of("CP001", bd("10")), singleLevel(), 5);

        MrpExplosionService.Requirement req = findByCode(reqs, "CL001");
        assertEquals(0, bd("20").compareTo(req.grossQty()));
    }

    @Test
    void appliesScrapRate() {
        Map<String, List<MrpExplosionService.BomLine>> bom = Map.of("CP001", List.of(
                new MrpExplosionService.BomLine("CP001", "CL001", "铝棒", bd("2"), bd("10"))));

        List<MrpExplosionService.Requirement> reqs = service.explode(
                Map.of("CP001", bd("10")), bom, 5);

        // 2 * 10 * 1.1 = 22
        assertEquals(0, bd("22").compareTo(findByCode(reqs, "CL001").grossQty()));
    }

    @Test
    void explodesMultiLevel() {
        Map<String, List<MrpExplosionService.BomLine>> bom = Map.of(
                "CP001", List.of(new MrpExplosionService.BomLine("CP001", "SEMI01", "半成品", bd("1"), bd("0"))),
                "SEMI01", List.of(new MrpExplosionService.BomLine("SEMI01", "CL001", "原材料", bd("3"), bd("0"))));

        List<MrpExplosionService.Requirement> reqs = service.explode(
                Map.of("CP001", bd("10")), bom, 5);

        // 10 * 1 * 3 = 30
        assertEquals(0, bd("30").compareTo(findByCode(reqs, "CL001").grossQty()));
    }

    @Test
    void leafItemsAreMarkedPurchased() {
        List<MrpExplosionService.Requirement> reqs = service.explode(
                Map.of("CP001", bd("10")), singleLevel(), 5);

        MrpExplosionService.Requirement leaf = findByCode(reqs, "CL001");
        assertEquals("采购", leaf.supplyType());
    }

    @Test
    void nodeWithBomIsMarkedManufactured() {
        Map<String, List<MrpExplosionService.BomLine>> bom = Map.of(
                "CP001", List.of(new MrpExplosionService.BomLine("CP001", "SEMI01", "半成品", bd("1"), bd("0"))),
                "SEMI01", List.of(new MrpExplosionService.BomLine("SEMI01", "CL001", "原材料", bd("3"), bd("0"))));

        List<MrpExplosionService.Requirement> reqs = service.explode(
                Map.of("CP001", bd("10")), bom, 5);

        assertEquals("自制", findByCode(reqs, "SEMI01").supplyType());
    }

    @Test
    void stockDeductsNetRequirement() {
        List<MrpExplosionService.Requirement> reqs = service.explode(
                Map.of("CP001", bd("10")), singleLevel(), 5,
                Map.of("CL001", bd("8")));

        MrpExplosionService.Requirement req = findByCode(reqs, "CL001");
        assertEquals(0, bd("20").compareTo(req.grossQty()));
        assertEquals(0, bd("8").compareTo(req.stockQty()));
        assertEquals(0, bd("12").compareTo(req.netQty()));
    }

    @Test
    void stockExceedingDemandProducesZeroNet() {
        List<MrpExplosionService.Requirement> reqs = service.explode(
                Map.of("CP001", bd("10")), singleLevel(), 5,
                Map.of("CL001", bd("999")));

        MrpExplosionService.Requirement req = findByCode(reqs, "CL001");
        assertEquals(0, BigDecimal.ZERO.compareTo(req.netQty()), "净需求不应为负");
    }

    @Test
    void cyclicBomDoesNotHang() {
        Map<String, List<MrpExplosionService.BomLine>> bom = Map.of(
                "CP001", List.of(new MrpExplosionService.BomLine("CP001", "SEMI01", "半成品", bd("1"), bd("0"))),
                "SEMI01", List.of(new MrpExplosionService.BomLine("SEMI01", "CP001", "回指", bd("1"), bd("0"))));

        List<MrpExplosionService.Requirement> reqs = service.explode(
                Map.of("CP001", bd("10")), bom, 5);

        assertTrue(reqs.size() > 0, "循环引用应被截断而不是无限递归");
    }

    @Test
    void maxDepthIsRespected() {
        Map<String, List<MrpExplosionService.BomLine>> bom = Map.of(
                "L1", List.of(new MrpExplosionService.BomLine("L1", "L2", "层2", bd("1"), bd("0"))),
                "L2", List.of(new MrpExplosionService.BomLine("L2", "L3", "层3", bd("1"), bd("0"))),
                "L3", List.of(new MrpExplosionService.BomLine("L3", "L4", "层4", bd("1"), bd("0"))));

        List<MrpExplosionService.Requirement> reqs = service.explode(
                Map.of("L1", bd("1")), bom, 2);

        assertTrue(findByCode(reqs, "L4") == null, "超出最大层数应停止展开");
    }

    @Test
    void itemWithoutBomIsTreatedAsLeaf() {
        List<MrpExplosionService.Requirement> reqs = service.explode(
                Map.of("UNKNOWN", bd("5")), Map.of(), 5);

        MrpExplosionService.Requirement req = findByCode(reqs, "UNKNOWN");
        assertEquals("采购", req.supplyType());
        assertEquals(0, bd("5").compareTo(req.grossQty()));
    }

    @Test
    void zeroDemandProducesNothing() {
        assertTrue(service.explode(Map.of("CP001", BigDecimal.ZERO), singleLevel(), 5).isEmpty());
    }

    private MrpExplosionService.Requirement findByCode(List<MrpExplosionService.Requirement> reqs, String code) {
        return reqs.stream().filter(r -> code.equals(r.itemCode())).findFirst().orElse(null);
    }

    private static BigDecimal bd(String v) {
        return new BigDecimal(v);
    }
}
