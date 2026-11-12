package com.mes.service;

import com.mes.entity.StockBalance;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 库存状况表：按「仓库类别 → 仓库 → 存货类别 → 存货」组织，一行一个存货。
 *
 * <p>三条硬规则：
 * <ul>
 *   <li>数量不为负：历史出库多于入库的行，展示为 0，不是 -100；</li>
 *   <li>单价取存货档案的「最新成本」，不是台账均价，也不是 0；</li>
 *   <li>金额 = 数量 × 单价，行与合计都能对上。</li>
 * </ul>
 */
class ReportQueryServiceStockStatusTest {

    private final ReportQueryService service = new ReportQueryService(null, new com.fasterxml.jackson.databind.ObjectMapper());

    private static StockBalance balance(String warehouse, String code, String name,
                                        String unit, double qty, double amount) {
        StockBalance b = new StockBalance();
        b.setWarehouse(warehouse);
        b.setItemCode(code);
        b.setItemName(name);
        b.setSpec("Φ60");
        b.setUnit(unit);
        b.setQuantity(BigDecimal.valueOf(qty));
        b.setAmount(BigDecimal.valueOf(amount));
        return b;
    }

    private Map<String, ReportQueryService.WarehouseInfo> warehouses() {
        Map<String, ReportQueryService.WarehouseInfo> out = new LinkedHashMap<>();
        out.put("原料仓", new ReportQueryService.WarehouseInfo("CK01", "原料仓", "原料仓"));
        out.put("成品仓", new ReportQueryService.WarehouseInfo("CK03", "成品仓", "成品仓"));
        return out;
    }

    private Map<String, ReportQueryService.ItemInfo> items() {
        Map<String, ReportQueryService.ItemInfo> out = new LinkedHashMap<>();
        out.put("CL001", new ReportQueryService.ItemInfo("原材料", new BigDecimal("4.2")));
        out.put("CP001", new ReportQueryService.ItemInfo("产成品", new BigDecimal("10.2")));
        out.put("CL999", new ReportQueryService.ItemInfo("原材料", BigDecimal.ZERO));
        return out;
    }

    @Test
    void rowsCarryWarehouseCategoryAndItemCategory() {
        List<Map<String, Object>> rows = service.stockStatusRows(
                List.of(balance("原料仓", "CL001", "45#圆钢", "kg", 100, 420)),
                warehouses(), items());

        assertEquals(1, rows.size());
        Map<String, Object> row = rows.get(0);
        assertEquals("原料仓", row.get("仓库类别"));
        assertEquals("CK01", row.get("仓库编码"));
        assertEquals("原料仓", row.get("仓库"));
        assertEquals("原材料", row.get("存货类别"));
        assertEquals("CL001", row.get("存货编码"));
    }

    @Test
    void quantityIsNeverNegative() {
        List<Map<String, Object>> rows = service.stockStatusRows(
                List.of(balance("原料仓", "CL001", "45#圆钢", "kg", -80, -336)),
                warehouses(), items());

        assertEquals(0, BigDecimal.ZERO.compareTo((BigDecimal) rows.get(0).get("数量")),
                "负库存展示为 0");
    }

    @Test
    void unitPriceComesFromItemLatestCost() {
        List<Map<String, Object>> rows = service.stockStatusRows(
                List.of(balance("原料仓", "CL001", "45#圆钢", "kg", 100, 999)),
                warehouses(), items());

        // 单价取 INV 最新成本 4.2，不取台账金额/数量
        assertEquals(0, new BigDecimal("4.2").compareTo((BigDecimal) rows.get(0).get("单价")));
        // 金额 = 100 × 4.2
        assertEquals(0, new BigDecimal("420.00").compareTo((BigDecimal) rows.get(0).get("金额")));
    }

    @Test
    void missingItemFallsBackToZeroPriceNotBreak() {
        List<Map<String, Object>> rows = service.stockStatusRows(
                List.of(balance("原料仓", "CL404", "未知物料", "kg", 5, 0)),
                warehouses(), items());

        assertFalse(rows.isEmpty());
        assertEquals(0, BigDecimal.ZERO.compareTo((BigDecimal) rows.get(0).get("单价")));
        assertEquals("", rows.get(0).get("存货类别"));
    }

    @Test
    void rowsAreSortedByWarehouseThenCategoryThenCode() {
        List<Map<String, Object>> rows = service.stockStatusRows(List.of(
                balance("成品仓", "CP001", "铝棒 Φ80", "件", 10, 102),
                balance("原料仓", "CP001", "铝棒 Φ80", "件", 3, 30),
                balance("原料仓", "CL001", "45#圆钢", "kg", 100, 420)), warehouses(), items());

        assertEquals(List.of("原料仓|原材料|CL001", "原料仓|产成品|CP001", "成品仓|产成品|CP001"),
                rows.stream().map((r) -> r.get("仓库") + "|" + r.get("存货类别") + "|" + r.get("存货编码")).toList());
    }

    @Test
    void sameWarehouseAndItemAreAggregatedAcrossBatches() {
        // 同一仓库同一存货分多个批次/库位时合并成一行，数量与金额相加
        List<Map<String, Object>> rows = service.stockStatusRows(List.of(
                balance("原料仓", "CL001", "45#圆钢", "kg", 60, 0),
                balance("原料仓", "CL001", "45#圆钢", "kg", 40, 0)), warehouses(), items());

        assertEquals(1, rows.size());
        assertEquals(0, new BigDecimal("100").compareTo((BigDecimal) rows.get(0).get("数量")));
        assertEquals(0, new BigDecimal("420.00").compareTo((BigDecimal) rows.get(0).get("金额")));
    }

    @Test
    void unknownWarehouseStillListedWithItsOwnName() {
        List<Map<String, Object>> rows = service.stockStatusRows(
                List.of(balance("临时仓", "CL001", "45#圆钢", "kg", 5, 0)), warehouses(), items());

        assertEquals("临时仓", rows.get(0).get("仓库"));
        assertEquals("临时仓", rows.get(0).get("仓库类别"), "档案里没有时用仓库名兜底");
        assertTrue(rows.get(0).get("仓库编码") == null || "".equals(rows.get(0).get("仓库编码")));
    }
}
