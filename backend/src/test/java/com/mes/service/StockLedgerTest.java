package com.mes.service;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 库存台账口径：
 * - 结存按「仓库 + 存货」各自滚动，不能把不同存货加在一起（否则与现存量对不上）；
 * - 行序按「存货 → 仓库 → 单据日期 → 单据编号」，同一存货的流水必须连续，
 *   否则同一天里多张单据交叉，结存数字上下跳，看不了；
 * - 销售退货是入库方向，退货造成的入库必须进台账。
 */
class StockLedgerTest {

    private static Map<String, Object> movement(String direction, String warehouse, String code,
                                                String name, String quantity, String amount, String date, String docNo) {
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("单据日期", date);
        row.put("方向", direction);
        row.put("单据类型", "IN".equals(direction) ? "销售退货单" : "销售出库单");
        row.put("单据编号", docNo);
        row.put("仓库", warehouse);
        row.put("存货编码", code);
        row.put("存货", name);
        row.put("数量", new BigDecimal(quantity));
        row.put("金额", new BigDecimal(amount));
        row.put("单价", BigDecimal.ZERO);
        return row;
    }

    private static List<Map<String, Object>> movements() {
        return List.of(
                movement("IN", "成品仓", "CP001", "铝棒 Φ80", "10", "1000", "2026-11-01", "CK-001"),
                movement("IN", "成品仓", "CL001", "45#圆钢", "5", "250", "2026-11-01", "XT-002"),
                movement("OUT", "成品仓", "CP001", "铝棒 Φ80", "4", "400", "2026-11-01", "CK-003"),
                movement("IN", "原料仓", "CP001", "铝棒 Φ80", "3", "300", "2026-11-02", "XT-004"),
                movement("IN", "成品仓", "CP001", "铝棒 Φ80", "2", "200", "2026-11-03", "XT-005"));
    }

    private static Map<String, Map<String, Object>> byDoc(List<Map<String, Object>> rows) {
        Map<String, Map<String, Object>> out = new LinkedHashMap<>();
        for (Map<String, Object> row : rows) out.put(String.valueOf(row.get("单据编号")), row);
        return out;
    }

    @Test
    void ledgerBalanceRollsPerItemInsteadOfAcrossItems() {
        Map<String, Map<String, Object>> rows = byDoc(ReportQueryService.ledgerRows(movements()));

        assertDecimal("10", rows.get("CK-001").get("结存数量"),
                "成品仓铝棒从 0 起算");
        assertDecimal("5", rows.get("XT-002").get("结存数量"), "圆钢不叠加铝棒的数量");
        assertDecimal("6", rows.get("CK-003").get("结存数量"), "成品仓铝棒 10 - 4 = 6");
        assertDecimal("600", rows.get("CK-003").get("结存金额"));
        assertDecimal("100", rows.get("CK-003").get("结存平均单价"));
        assertDecimal("3", rows.get("XT-004").get("结存数量"), "原料仓铝棒单独从 0 起算");
        assertDecimal("8", rows.get("XT-005").get("结存数量"), "成品仓铝棒 6 + 2 = 8");
    }

    @Test
    void sameItemRowsStayContiguousInsteadOfInterleavingByDate() {
        List<Map<String, Object>> rows = ReportQueryService.ledgerRows(movements());
        List<String> itemOrder = new ArrayList<>();
        for (Map<String, Object> row : rows) itemOrder.add(String.valueOf(row.get("存货编码")));

        assertEquals(List.of("CL001", "CP001", "CP001", "CP001", "CP001"), itemOrder,
                "同一存货的流水必须连续（按存货编码分块）；整表按日期混排会让人以为结存跳变");
    }

    @Test
    void sameItemAndWarehouseKeepChronologicalOrderWithinTheBlock() {
        List<Map<String, Object>> rows = ReportQueryService.ledgerRows(movements()).stream()
                .filter((row) -> "成品仓".equals(row.get("仓库")) && "CP001".equals(row.get("存货编码")))
                .toList();

        assertEquals(List.of("CK-001", "CK-003", "XT-005"),
                rows.stream().map((row) -> String.valueOf(row.get("单据编号"))).toList(),
                "同一仓库同一存货按单据日期升序，结存才能顺读");
        assertDecimal("8", rows.get(2).get("结存数量"));
    }

    @Test
    void ledgerSeparatesWarehousesOfTheSameItem() {
        List<Map<String, Object>> rows = ReportQueryService.ledgerRows(List.of(
                movement("IN", "成品仓", "CP001", "铝棒 Φ80", "10", "1000", "2026-11-01", "A-1"),
                movement("IN", "原料仓", "CP001", "铝棒 Φ80", "3", "300", "2026-11-02", "A-2")));

        Map<String, Map<String, Object>> indexed = byDoc(rows);
        assertDecimal("10", indexed.get("A-1").get("结存数量"));
        assertDecimal("3", indexed.get("A-2").get("结存数量"), "不同仓库各自滚动");
    }

    @Test
    void saleReturnIsInboundAndCountsIntoBalance() {
        assertEquals("IN", StockTxnService.directionOfPanel("SALE_RETURN"));
        assertEquals("OUT", StockTxnService.directionOfPanel("PURCHASE_RETURN"));
        assertEquals(null, StockTxnService.directionOfPanel("SO_ORDER"));

        List<Map<String, Object>> rows = ReportQueryService.ledgerRows(List.of(
                movement("IN", "成品仓", "CP001", "铝棒 Φ80", "10", "1000", "2026-11-01", "A-1"),
                movement("OUT", "成品仓", "CP001", "铝棒 Φ80", "10", "1000", "2026-11-02", "A-2"),
                movement("IN", "成品仓", "CP001", "铝棒 Φ80", "2", "200", "2026-11-03", "A-3")));

        assertDecimal("2", rows.get(2).get("结存数量"), "退货入库回到结存");
        assertDecimal("2", rows.get(2).get("收入数量"));
        assertDecimal("0", rows.get(2).get("发出数量"));
    }

    @Test
    void stockPanelListCoversReturns() {
        assertTrue(StockTxnService.stockPanels().contains("SALE_RETURN"));
        assertTrue(StockTxnService.stockPanels().contains("PURCHASE_RETURN"));
        assertTrue(StockTxnService.stockPanels().contains("INIT_BALANCE"));
    }

    private static void assertDecimal(String expected, Object actual) {
        assertDecimal(expected, actual, null);
    }

    private static void assertDecimal(String expected, Object actual, String message) {
        BigDecimal value = actual instanceof BigDecimal decimal ? decimal : new BigDecimal(String.valueOf(actual));
        assertEquals(0, new BigDecimal(expected).compareTo(value), message);
    }
}
