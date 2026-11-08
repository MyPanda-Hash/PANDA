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
 * 退货单方向语义：类名容易混淆，方向必须与业务一致。
 * - 采购退货（PURCHASE_RETURN）：货退回供应商 → 库存减少 OUT
 * - 销售退货（SALE_RETURN）：客户货退回 → 库存增加 IN
 */
class ReturnDirectionTest {

    private final StockTxnService stockTxn = new StockTxnService(null, null);
    private final FinanceSettleService finance = new FinanceSettleService(null, null);

    @Test
    void purchaseReturnTakesStockOut() {
        assertEquals("OUT", stockTxn.directionOf("PURCHASE_RETURN"));
    }

    @Test
    void saleReturnBringsStockIn() {
        assertEquals("IN", stockTxn.directionOf("SALE_RETURN"));
    }

    @Test
    void purchaseReturnReducesPayable() {
        assertEquals("AP_RETURN", finance.typeOf("PURCHASE_RETURN"));
    }

    @Test
    void saleReturnReducesReceivable() {
        assertEquals("AR_RETURN", finance.typeOf("SALE_RETURN"));
    }

    @Test
    void purchaseReturnBuildsOutboundTxnRows() {
        Map<String, Object> head = Map.of("单据编号", "CT-001", "供应商", "华东铝业", "仓库", "原料仓");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货编码", "CL001", "存货名称", "铝棒", "实收数量", 20, "单价", 5, "金额", 100)));

        List<StockTxnService.Txn> txns = stockTxn.buildTxns("PURCHASE_RETURN", "CT-001", head, detail);

        assertEquals(1, txns.size());
        assertEquals("OUT", txns.get(0).direction());
        assertEquals(0, new BigDecimal("20").compareTo(txns.get(0).quantity()));
        assertEquals("原料仓", txns.get(0).warehouse());
    }

    @Test
    void saleReturnBuildsInboundTxnRows() {
        Map<String, Object> head = Map.of("单据编号", "XT-001", "客户", "中天精工", "仓库", "成品仓");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货编码", "CP001", "存货名称", "铝棒 Φ80", "数量", 5, "售价", 30, "销售金额", 150)));

        List<StockTxnService.Txn> txns = stockTxn.buildTxns("SALE_RETURN", "XT-001", head, detail);

        assertEquals(1, txns.size());
        assertEquals("IN", txns.get(0).direction());
        assertEquals(0, new BigDecimal("5").compareTo(txns.get(0).quantity()));
        assertEquals("成品仓", txns.get(0).warehouse());
    }

    @Test
    void purchaseReturnSettlementIsReversePayable() {
        Map<String, Object> head = Map.of("单据编号", "CT-002", "供应商", "华东铝业");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货名称", "铝棒", "实收数量", 10, "单价", 5, "金额", 50)));

        FinanceSettleService.Settlement s = finance.buildSettlement("PURCHASE_RETURN", "CT-002", head, detail);

        assertNotNull(s);
        assertEquals("AP_RETURN", s.type());
        assertEquals("华东铝业", s.partner());
        assertEquals(0, new BigDecimal("50").compareTo(s.amount()));
    }

    @Test
    void saleReturnSettlementIsReverseReceivable() {
        Map<String, Object> head = Map.of("单据编号", "XT-002", "客户", "中天精工");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货名称", "成品A", "数量", 3, "售价", 40, "销售金额", 120)));

        FinanceSettleService.Settlement s = finance.buildSettlement("SALE_RETURN", "XT-002", head, detail);

        assertNotNull(s);
        assertEquals("AR_RETURN", s.type());
        assertEquals("中天精工", s.partner());
        assertEquals(0, new BigDecimal("120").compareTo(s.amount()));
    }

    @Test
    void normalPanelsStayUnchanged() {
        assertEquals("IN", stockTxn.directionOf("PURCHASE_IN"));
        assertEquals("OUT", stockTxn.directionOf("SALE_OUT"));
        assertNull(stockTxn.directionOf("SO_ORDER"));
        // 票到确认：采购入库只入库存，不再产生应付
        assertNull(finance.typeOf("PURCHASE_IN"));
        assertEquals("AR", finance.typeOf("SALE_OUT"));
        assertNull(finance.typeOf("SO_ORDER"));
    }

    @Test
    void returnWithZeroQuantityProducesNoTxn() {
        Map<String, Object> head = Map.of("单据编号", "CT-003", "供应商", "华东铝业");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货编码", "CL001", "实收数量", 0)));

        assertTrue(stockTxn.buildTxns("PURCHASE_RETURN", "CT-003", head, detail).isEmpty());
    }
}
