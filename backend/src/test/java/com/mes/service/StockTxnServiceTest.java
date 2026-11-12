package com.mes.service;

import com.mes.entity.StockBalance;
import com.mes.entity.StockTxn;
import com.mes.mapper.StockBalanceMapper;
import com.mes.mapper.StockTxnMapper;
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
    void outsourceIssuePostsOutboundRows() {
        // 委外发料单：审批后必须按明细出库，否则委外只进不出
        Map<String, Object> head = Map.of("单据编号", "WF-001", "仓库", "原料仓", "单据日期", "2026-11-12");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("材料编码", "CL001", "材料名称", "45#圆钢", "计量单位", "kg", "数量", 6, "仓库", "原料仓"),
                Map.of("材料编码", "CL004", "材料名称", "切削液", "计量单位", "升", "数量", 0.2, "仓库", "辅料仓")));

        List<StockTxnService.Txn> txns = service.buildTxns("OUTSOURCE_ISSUE", "WF-001", head, detail);

        assertEquals(2, txns.size());
        assertEquals("OUT", txns.get(0).direction());
        assertEquals("CL001", txns.get(0).itemCode());
        assertEquals("原料仓", txns.get(0).warehouse());
        assertEquals("辅料仓", txns.get(1).warehouse(), "每行按自己的仓库出库，不能全落表头仓");
        assertTrue(service.postsStock("OUTSOURCE_ISSUE"), "发料单要参与库存");
    }

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

    /** 结存平均成本：同类存货有多个仓库行时按数量加权。 */
    @Test
    void averageCostWeightsWarehouseBalances() {
        StockBalanceService balances = org.mockito.Mockito.mock(StockBalanceService.class);
        StockBalance a = new StockBalance();
        a.setItemCode("CP004");
        a.setItemName("减速箱体 A");
        a.setWarehouse("成品仓");
        a.setQuantity(new BigDecimal("10"));
        a.setAmount(new BigDecimal("3000"));
        StockBalance b = new StockBalance();
        b.setItemCode("CP004");
        b.setItemName("减速箱体 A");
        b.setWarehouse("半成品仓");
        b.setQuantity(new BigDecimal("10"));
        b.setAmount(new BigDecimal("3400"));
        org.mockito.Mockito.when(balances.list()).thenReturn(List.of(a, b));

        StockTxnService svc = new StockTxnService(null, balances);

        assertEquals(0, new BigDecimal("320").compareTo(svc.averageCostOf("CP004", "减速箱体 A")));
        assertEquals(0, new BigDecimal("300").compareTo(svc.averageCostOf("CP004", "减速箱体 A", "成品仓")));
        assertEquals(0, BigDecimal.ZERO.compareTo(svc.averageCostOf("NOT-EXIST", "", null)));
    }

    /** 调拨单是双仓语义：一条明细同时产生「调出仓出库」和「调入仓入库」两条事务。 */
    @Test
    void transferProducesBothDirections() {
        Map<String, Object> head = Map.of("单据编号", "DB-001", "调出仓库", "原料仓",
                "调入仓库", "线边仓A", "单据日期", "2026-11-12");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货编码", "CL002", "存货名称", "6061铝锭", "计量单位", "kg", "数量", 5)));

        List<StockTxnService.Txn> txns = service.buildTxns("TRANSFER", "DB-001", head, detail);

        assertEquals(2, txns.size(), "调拨要点两条：调出 + 调入");
        StockTxnService.Txn out = txns.get(0);
        StockTxnService.Txn in = txns.get(1);
        assertEquals("OUT", out.direction());
        assertEquals("原料仓", out.warehouse());
        assertEquals("IN", in.direction());
        assertEquals("线边仓A", in.warehouse());
        assertEquals(0, new BigDecimal("5").compareTo(out.quantity()));
        assertEquals(0, new BigDecimal("5").compareTo(in.quantity()));
    }

    /** 盘点单：审核即按盈亏调账——盘盈落 IN、盘亏落 OUT，盈亏为 0 的行跳过。 */
    @Test
    void stockCheckPostsGainAndLoss() {
        Map<String, Object> head = Map.of("单据编号", "PD-001", "仓库", "原料仓", "单据日期", "2026-11-12");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货编码", "CL001", "存货名称", "45#圆钢", "计量单位", "kg", "盈亏数量", 3),
                Map.of("存货编码", "CL002", "存货名称", "6061铝锭", "计量单位", "kg", "盈亏数量", -2),
                Map.of("存货编码", "CL003", "存货名称", "6063铝棒", "计量单位", "kg", "盈亏数量", 0)));

        List<StockTxnService.Txn> txns = service.buildTxns("STOCK_CHECK", "PD-001", head, detail);

        assertEquals(2, txns.size(), "盈亏为 0 的行不落事务");
        assertEquals("IN", txns.get(0).direction());
        assertEquals("原料仓", txns.get(0).warehouse());
        assertEquals(0, new BigDecimal("3").compareTo(txns.get(0).quantity()));
        assertEquals("OUT", txns.get(1).direction());
        assertEquals(0, new BigDecimal("2").compareTo(txns.get(1).quantity()));
        assertEquals("STOCK_CHECK", txns.get(1).panelCode());
    }

    /** 缺 盈亏数量 的历史/导入数据：按实盘 − 账面 兜底。 */
    @Test
    void stockCheckFallsBackToActualMinusBook() {
        Map<String, Object> head = Map.of("仓库", "原料仓", "单据日期", "2026-11-12");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货编码", "CL001", "存货名称", "45#圆钢", "计量单位", "kg",
                        "账面数量", 100, "实盘数量", 97)));

        List<StockTxnService.Txn> txns = service.buildTxns("STOCK_CHECK", "PD-002", head, detail);

        assertEquals(1, txns.size());
        assertEquals("OUT", txns.get(0).direction());
        assertEquals(0, new BigDecimal("3").compareTo(txns.get(0).quantity()));
    }

    /** 货位调整：一行两条事务，原货位出、新货位进，数量与仓库不变。 */
    @Test
    void locationAdjustMovesQuantityBetweenLocations() {
        Map<String, Object> head = Map.of("单据编号", "HW-001", "仓库", "原料仓", "单据日期", "2026-11-12");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货编码", "CL001", "存货名称", "45#圆钢", "计量单位", "kg", "数量", 2,
                        "原货位", "", "新货位", "B-02")));

        List<StockTxnService.Txn> txns = service.buildTxns("LOCATION_ADJUST", "HW-001", head, detail);

        assertEquals(2, txns.size(), "货位调整要落两条：原货位出 + 新货位进");
        assertEquals("OUT", txns.get(0).direction());
        assertEquals("", txns.get(0).location());
        assertEquals("IN", txns.get(1).direction());
        assertEquals("B-02", txns.get(1).location());
        assertEquals("原料仓", txns.get(1).warehouse(), "货位调整不动仓库");
        assertEquals(0, new BigDecimal("2").compareTo(txns.get(1).quantity()));
    }

    /** 新旧货位相同 / 新货位为空：不落事务，避免自己进自己出。 */
    @Test
    void locationAdjustSkipsSameOrBlankTarget() {
        Map<String, Object> head = Map.of("仓库", "原料仓", "单据日期", "2026-11-12");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货编码", "CL001", "存货名称", "45#圆钢", "计量单位", "kg", "数量", 1,
                        "原货位", "A-01", "新货位", "A-01"),
                Map.of("存货编码", "CL001", "存货名称", "45#圆钢", "计量单位", "kg", "数量", 1,
                        "原货位", "A-01", "新货位", "")));

        assertTrue(service.buildTxns("LOCATION_ADJUST", "HW-002", head, detail).isEmpty());
    }

    /** 会落库存事务的面板清单：调拨/货位调整没有单一方向，盘点按盈亏，都必须被覆盖。 */
    @Test
    void postsStockCoversAdjustPanels() {
        assertTrue(service.postsStock("TRANSFER"));
        assertTrue(service.postsStock("LOCATION_ADJUST"));
        assertTrue(service.postsStock("STOCK_CHECK"));
        assertTrue(service.postsStock("SALE_OUT"));
        assertTrue(!service.postsStock("SO_ORDER"));
    }

    /**
     * 调拨必须真的落账：buildTxns 单测只能证明「能算出两条」，
     * 曾经 post() 用 directionOf 做闸门把 TRANSFER 判成非库存面板直接返回 0，
     * 单测全绿但线上调拨审核后库存一动不动。这条用例盯的是 post 的闸门与写库。
     */
    @Test
    void postWritesTransferTxnsAndReverseClearsThem() {
        StockTxnMapper txnMapper = org.mockito.Mockito.mock(StockTxnMapper.class);
        StockBalanceMapper balanceMapper = org.mockito.Mockito.mock(StockBalanceMapper.class);
        org.mockito.Mockito.when(txnMapper.selectList(null)).thenReturn(List.of());
        StockTxnService svc = new StockTxnService(txnMapper, new StockBalanceService(balanceMapper));

        Map<String, Object> head = Map.of("单据编号", "DB-001", "调出仓库", "原料仓",
                "调入仓库", "线边仓A", "单据日期", "2026-11-12");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货编码", "CL002", "存货名称", "6061铝锭", "计量单位", "kg", "数量", 5)));

        assertEquals(2, svc.post("TRANSFER", "DB-001", head, detail), "调拨审核要写一出一进两条事务");
        org.mockito.Mockito.verify(txnMapper, org.mockito.Mockito.times(2))
                .insert(org.mockito.ArgumentMatchers.any(StockTxn.class));
        // 弃审：按面板+单号冲销，同样不能被闸门挡住
        org.mockito.Mockito.when(txnMapper.delete(org.mockito.ArgumentMatchers.any())).thenReturn(2);
        assertTrue(svc.reverse("TRANSFER", "DB-001") >= 0, "弃审要走进冲销分支");
        // post 会先清旧事务，reverse 再删一次，各一次
        org.mockito.Mockito.verify(txnMapper, org.mockito.Mockito.times(2))
                .delete(org.mockito.ArgumentMatchers.any());
    }
}
