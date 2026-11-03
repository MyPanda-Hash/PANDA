package com.mes.service;

import com.mes.entity.StockBalance;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 库存余额四维聚合：存货 + 仓库 + 库位 + 批次。
 * 单厂自用，不引入组织维度。
 */
class StockBalanceServiceTest {

    private final StockBalanceService service = new StockBalanceService(null);

    @Test
    void aggregatesInAndOutIntoOneBalanceRow() {
        List<StockBalanceService.Movement> movements = List.of(
                new StockBalanceService.Movement("CP001", "成品A", "", "", "成品仓", "A-01", "", "IN", bd("10"), bd("50")),
                new StockBalanceService.Movement("CP001", "成品A", "", "", "成品仓", "A-01", "", "OUT", bd("4"), bd("20")));

        List<StockBalance> balances = service.aggregate(movements);

        assertEquals(1, balances.size());
        StockBalance row = balances.get(0);
        assertEquals("CP001", row.getItemCode());
        assertEquals("成品A", row.getItemName());
        assertEquals("成品仓", row.getWarehouse());
        assertEquals(0, bd("6").compareTo(row.getQuantity()));
        assertEquals(0, bd("30").compareTo(row.getAmount()));
    }

    @Test
    void keepsDifferentBatchAndLocationApart() {
        List<StockBalanceService.Movement> movements = List.of(
                new StockBalanceService.Movement("CP001", "成品A", "", "", "成品仓", "A-01", "B2026-01", "IN", bd("10"), bd("5")),
                new StockBalanceService.Movement("CP001", "成品A", "", "", "成品仓", "A-02", "B2026-01", "IN", bd("3"), bd("5")),
                new StockBalanceService.Movement("CP001", "成品A", "", "", "成品仓", "A-01", "B2026-02", "IN", bd("7"), bd("5")));

        List<StockBalance> balances = service.aggregate(movements);

        assertEquals(3, balances.size());
        assertTrue(balances.stream().anyMatch(b -> "A-02".equals(b.getLocation()) && bd("3").compareTo(b.getQuantity()) == 0));
        assertTrue(balances.stream().anyMatch(b -> "B2026-02".equals(b.getBatchNo()) && bd("7").compareTo(b.getQuantity()) == 0));
    }

    @Test
    void blankLocationAndBatchCollapseIntoOneRow() {
        List<StockBalanceService.Movement> movements = List.of(
                new StockBalanceService.Movement("CL001", "铝棒", "", "", "原料仓", null, null, "IN", bd("100"), bd("2")),
                new StockBalanceService.Movement("CL001", "铝棒", "", "", "原料仓", "", "", "IN", bd("50"), bd("2")));

        List<StockBalance> balances = service.aggregate(movements);

        assertEquals(1, balances.size());
        assertEquals(0, bd("150").compareTo(balances.get(0).getQuantity()));
    }

    @Test
    void negativeBalanceIsKeptForAllowNegativeStock() {
        List<StockBalanceService.Movement> movements = List.of(
                new StockBalanceService.Movement("CL001", "铝棒", "", "", "原料仓", "", "", "OUT", bd("5"), bd("2")));

        List<StockBalance> balances = service.aggregate(movements);

        assertEquals(0, bd("-5").compareTo(balances.get(0).getQuantity()));
    }

    /** 历史单据只有名称没有编码：必须按名称区分，否则不同存货会被合并成一行。 */
    @Test
    void missingItemCodeFallsBackToNameSoDifferentItemsDoNotMerge() {
        List<StockBalanceService.Movement> movements = List.of(
                new StockBalanceService.Movement("", "6063铝棒", "", "kg", "原料仓", "", "", "OUT", bd("411.4"), bd("0")),
                new StockBalanceService.Movement("", "包装木箱", "", "件", "辅料仓", "", "", "OUT", bd("6.8"), bd("0")));

        List<StockBalance> balances = service.aggregate(movements);

        assertEquals(2, balances.size());
        assertTrue(balances.stream().anyMatch(b -> "6063铝棒".equals(b.getItemName())));
        assertTrue(balances.stream().anyMatch(b -> "包装木箱".equals(b.getItemName())));
    }

    private static BigDecimal bd(String value) {
        return new BigDecimal(value);
    }
}
