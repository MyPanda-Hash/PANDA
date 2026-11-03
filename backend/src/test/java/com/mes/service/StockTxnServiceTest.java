package com.mes.service;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 单据 → 库存事务：审核出入库单时把明细转成 stock_txn 行。
 * 字段名与 ReportQueryService 的既有聚合保持一致，避免两套口径。
 */
class StockTxnServiceTest {

    private final StockTxnService service = new StockTxnService(null, null);

    @Test
    void inboundPanelsProduceInDirection() {
        Map<String, Object> head = Map.of("单据编号", "RK-001", "仓库", "原料仓", "单据日期", "2026-11-01");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货编码", "CL001", "存货名称", "铝棒", "规格型号", "80mm",
                        "计量单位", "kg", "实收数量", 100, "单价", 2, "金额", 200)));

        List<StockTxnService.Txn> txns = service.buildTxns("PURCHASE_IN", "RK-001", head, detail);

        assertEquals(1, txns.size());
        StockTxnService.Txn txn = txns.get(0);
        assertEquals("IN", txn.direction());
        assertEquals("CL001", txn.itemCode());
        assertEquals("原料仓", txn.warehouse());
        assertEquals(0, new BigDecimal("100").compareTo(txn.quantity()));
        assertEquals(0, new BigDecimal("200").compareTo(txn.amount()));
        assertEquals(1, txn.rowNo());
    }

    @Test
    void outboundPanelsProduceOutDirection() {
        Map<String, Object> head = Map.of("单据编号", "CK-001", "仓库", "成品仓");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货编码", "CP001", "存货名称", "成品A", "数量", 30,
                        "成本价", 10, "成本金额", 300)));

        List<StockTxnService.Txn> txns = service.buildTxns("SALE_OUT", "CK-001", head, detail);

        assertEquals(1, txns.size());
        assertEquals("OUT", txns.get(0).direction());
        assertEquals(0, new BigDecimal("30").compareTo(txns.get(0).quantity()));
        assertEquals(0, new BigDecimal("300").compareTo(txns.get(0).amount()));
    }

    @Test
    void nonStockPanelsProduceNothing() {
        Map<String, Object> detail = Map.of("items", List.of(Map.of("存货编码", "X", "数量", 1)));

        assertTrue(service.buildTxns("SO_ORDER", "SO-001", Map.of(), detail).isEmpty());
        assertTrue(service.buildTxns("PU_ORDER", "PO-001", Map.of(), detail).isEmpty());
    }

    @Test
    void locationAndBatchFallBackToBlank() {
        Map<String, Object> head = Map.of("单据编号", "RK-002", "仓库", "原料仓");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货编码", "CL001", "存货名称", "铝棒", "实收数量", 5, "单价", 1, "金额", 5)));

        StockTxnService.Txn txn = service.buildTxns("PURCHASE_IN", "RK-002", head, detail).get(0);

        assertEquals("", txn.location());
        assertEquals("", txn.batchNo());
    }

    @Test
    void detailLocationOverridesHeadWarehouseDefault() {
        Map<String, Object> head = Map.of("单据编号", "RK-003", "仓库", "原料仓");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货编码", "CL001", "存货名称", "铝棒", "仓库", "辅料仓",
                        "库位", "B-02", "批次号", "B2026-11", "实收数量", 8, "单价", 1, "金额", 8)));

        StockTxnService.Txn txn = service.buildTxns("PURCHASE_IN", "RK-003", head, detail).get(0);

        assertEquals("辅料仓", txn.warehouse());
        assertEquals("B-02", txn.location());
        assertEquals("B2026-11", txn.batchNo());
    }

    @Test
    void rowsWithoutQuantityAreSkipped() {
        Map<String, Object> head = Map.of("单据编号", "RK-004", "仓库", "原料仓");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货编码", "CL001", "存货名称", "铝棒", "实收数量", 0),
                Map.of("存货编码", "CL002", "存货名称", "铝板", "实收数量", 3, "单价", 1, "金额", 3)));

        List<StockTxnService.Txn> txns = service.buildTxns("PURCHASE_IN", "RK-004", head, detail);

        assertEquals(1, txns.size());
        assertEquals("CL002", txns.get(0).itemCode());
    }

    @Test
    void amountFallsBackToQuantityTimesPrice() {
        Map<String, Object> head = Map.of("单据编号", "RK-005", "仓库", "原料仓");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货编码", "CL001", "存货名称", "铝棒", "实收数量", 4, "单价", 2.5)));

        StockTxnService.Txn txn = service.buildTxns("PURCHASE_IN", "RK-005", head, detail).get(0);

        assertEquals(0, new BigDecimal("10").compareTo(txn.amount()));
    }

    @Test
    void multiTabDetailIsScanned() {
        Map<String, Object> head = Map.of("单据编号", "MO-001", "仓库", "原料仓");
        Map<String, Object> detail = Map.of(
                "items", List.of(Map.of("存货编码", "CL001", "存货名称", "铝棒", "数量", 2, "单价", 1, "金额", 2)),
                "materials", List.of(Map.of("材料编码", "CL002", "材料名称", "铝板", "数量", 3, "单价", 1, "金额", 3)));

        List<StockTxnService.Txn> txns = service.buildTxns("MATERIAL_OUT", "MO-001", head, detail);

        assertEquals(2, txns.size());
    }

    @Test
    void unknownPanelReturnsEmptyNotNull() {
        List<StockTxnService.Txn> txns = service.buildTxns("NOT_A_PANEL", "X-1", Map.of(), Map.of());
        assertNotNull(txns);
        assertTrue(txns.isEmpty());
    }

    @Test
    void panelDirectionIsExposedForCallers() {
        assertEquals("IN", service.directionOf("PURCHASE_IN"));
        assertEquals("IN", service.directionOf("FINISH_IN"));
        assertEquals("IN", service.directionOf("OTHER_IN"));
        assertEquals("IN", service.directionOf("OUTSOURCE_IN"));
        assertEquals("OUT", service.directionOf("SALE_OUT"));
        assertEquals("OUT", service.directionOf("MATERIAL_OUT"));
        assertEquals("OUT", service.directionOf("OTHER_OUT"));
        assertNull(service.directionOf("SO_ORDER"));
    }
}
