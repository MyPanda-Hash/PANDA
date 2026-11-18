package com.mes.service;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.math.BigDecimal;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

/**
 * 委外发料单必须出库：只发料不扣库存，材料账永远对不上（2026-11-13 实查发现的一张已审核
 * 发料单 0 条库存事务，正是本类要防的情形）。
 */
class StockTxnOutsourceIssueTest {

    private final StockTxnService service = new StockTxnService(null, null);

    @Test
    @DisplayName("委外发料单在出库面板集合里，审核后要落 OUT 事务")
    void outsourceIssuePostsStock() {
        assertTrue(service.postsStock("OUTSOURCE_ISSUE"), "委外发料必须扣库存");
        assertEquals("OUT", service.directionOf("OUTSOURCE_ISSUE"));
    }

    @Test
    @DisplayName("委外发料明细按材料编码/数量/仓库折成 OUT 事务")
    void buildsOutboundTxnsFromMaterials() {
        Map<String, Object> head = new LinkedHashMap<>();
        head.put("单据日期", "2026-11-13");
        head.put("仓库", "原料仓");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("材料编码", "CL001", "材料名称", "45#圆钢", "规格型号", "Φ60",
                        "计量单位", "kg", "数量", 9, "单价", 4.2078, "金额", 37.87, "仓库", "原料仓"),
                Map.of("材料编码", "CL004", "材料名称", "切削液", "规格型号", "20L/桶",
                        "计量单位", "升", "数量", 0.3, "单价", 18.0281, "金额", 5.41, "仓库", "辅料仓")));

        List<StockTxnService.Txn> txns = service.buildTxns("OUTSOURCE_ISSUE", "WF-1", head, detail);

        assertEquals(2, txns.size(), "两行材料落两条事务");
        assertEquals("OUT", txns.get(0).direction());
        assertEquals("CL001", txns.get(0).itemCode());
        assertEquals(0, new BigDecimal("9").compareTo(txns.get(0).quantity()));
        assertEquals("原料仓", txns.get(0).warehouse());
        assertEquals("辅料仓", txns.get(1).warehouse(), "每行用自己的仓库，不能一律用表头仓");
    }

    @Test
    @DisplayName("数量为 0 的行不出事务；不发料的单据不产生任何事务")
    void skipsZeroQuantityRows() {
        Map<String, Object> head = Map.of("单据日期", "2026-11-13", "仓库", "原料仓");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("材料编码", "CL001", "材料名称", "45#圆钢", "数量", 0, "仓库", "原料仓")));

        assertTrue(service.buildTxns("OUTSOURCE_ISSUE", "WF-2", head, detail).isEmpty());
    }

    @Test
    @DisplayName("非库存面板不落事务（对照：委外加工单本身不动库存）")
    void nonStockPanelPostsNothing() {
        assertFalse(service.postsStock("OUTSOURCE_ORDER"));
        assertFalse(service.postsStock("OUTSOURCE_FEE"));
        assertTrue(service.buildTxns("OUTSOURCE_ORDER", "WW-1", Map.of(), Map.of()).isEmpty());
    }
}
