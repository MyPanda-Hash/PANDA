package com.mes.service;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 应收应付结算：把业务单据折算成应收单/应付单与核销记录。
 * 单厂单账套，不引入多币种与组织维度。
 */
class FinanceSettleServiceTest {

    private final FinanceSettleService service = new FinanceSettleService(null, null);

    @Test
    void purchaseInCreatesPayable() {
        Map<String, Object> head = Map.of("单据编号", "RK-001", "供应商", "华东铝业", "单据日期", "2026-11-02");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货名称", "铝棒", "实收数量", 100, "单价", 2, "金额", 200),
                Map.of("存货名称", "铝板", "实收数量", 10, "单价", 5, "金额", 50)));

        FinanceSettleService.Settlement s = service.buildSettlement("PURCHASE_IN", "RK-001", head, detail);

        assertEquals("AP", s.type());
        assertEquals("华东铝业", s.partner());
        assertEquals(0, new BigDecimal("250").compareTo(s.amount()));
        assertEquals("RK-001", s.sourceNo());
    }

    /** 出库单只做出库与成本，应收挂在销货单上：不然一张发货单会挂两笔应收。 */
    @Test
    void saleOutDoesNotCreateReceivable() {
        Map<String, Object> head = Map.of("单据编号", "CK-001", "客户", "中天精工");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货名称", "成品A", "数量", 30, "售价", 10, "销售金额", 300)));

        assertNull(service.buildSettlement("SALE_OUT", "CK-001", head, detail), "出库单不挂应收");
    }

    /** 销货单是唯一的应收来源。 */
    @Test
    void saleInvCreatesReceivable() {
        Map<String, Object> head = Map.of("单据编号", "XS-001", "客户", "中天精工");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货名称", "成品A", "数量", 30, "含税单价", 10, "含税金额", 300)));

        FinanceSettleService.Settlement s = service.buildSettlement("SALE_INV", "XS-001", head, detail);

        assertEquals("AR", s.type());
        assertEquals("中天精工", s.partner());
        assertEquals(0, new BigDecimal("300").compareTo(s.amount()));
    }

    /** 发票只是票据，应收已经由销货单挂过，不能再挂一次。 */
    @Test
    void saleInvoiceDoesNotCreateReceivable() {
        Map<String, Object> head = Map.of("单据编号", "FP-001", "客户", "西部材料");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货名称", "成品B", "数量", 5, "含税金额", 500)));

        assertNull(service.buildSettlement("SALE_INVOICE", "FP-001", head, detail), "发票不重复挂应收");
    }

    @Test
    void purchaseInvoiceCreatesPayable() {
        Map<String, Object> head = Map.of("单据编号", "PF-001", "供应商", "南方重工");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货名称", "辅料", "含税金额", 120)));

        FinanceSettleService.Settlement s = service.buildSettlement("PU_INVOICE", "PF-001", head, detail);

        assertEquals("AP", s.type());
        assertEquals("南方重工", s.partner());
        assertEquals(0, new BigDecimal("120").compareTo(s.amount()));
    }

    @Test
    void nonFinancialPanelsProduceNull() {
        Map<String, Object> detail = Map.of("items", List.of(Map.of("存货名称", "X", "数量", 1, "金额", 1)));

        assertNull(service.buildSettlement("SO_ORDER", "SO-001", Map.of(), detail));
        assertNull(service.buildSettlement("PURCHASE_IN_DETAIL", "R-1", Map.of(), detail));
        assertNull(service.buildSettlement("MATERIAL_OUT", "M-001", Map.of(), detail));
    }

    @Test
    void zeroAmountProducesNull() {
        Map<String, Object> head = Map.of("单据编号", "RK-002", "供应商", "华东铝业");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货名称", "铝棒", "实收数量", 0, "单价", 0, "金额", 0)));

        assertNull(service.buildSettlement("PURCHASE_IN", "RK-002", head, detail));
    }

    @Test
    void amountFallsBackToQuantityTimesPrice() {
        Map<String, Object> head = Map.of("单据编号", "RK-003", "供应商", "华东铝业");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货名称", "铝棒", "实收数量", 4, "单价", 2.5)));

        FinanceSettleService.Settlement s = service.buildSettlement("PURCHASE_IN", "RK-003", head, detail);

        assertEquals(0, new BigDecimal("10").compareTo(s.amount()));
    }

    @Test
    void typeOfPanelsIsExposedForCallers() {
        assertEquals("AP", service.typeOf("PURCHASE_IN"));
        assertEquals("AP", service.typeOf("PU_INVOICE"));
        assertEquals("AR", service.typeOf("SALE_INV"));
        assertNull(service.typeOf("SALE_OUT"), "出库单不挂应收");
        assertNull(service.typeOf("SALE_INVOICE"), "发票不重复挂应收");
        assertNull(service.typeOf("SO_ORDER"));
    }

    @Test
    void settleMatchesWriteOffAgainstOutstanding() {
        FinanceSettleService.SettleResult r = service.computeSettle(
                new BigDecimal("100"), new BigDecimal("250"));

        assertEquals(0, new BigDecimal("100").compareTo(r.settled()));
        assertEquals(0, new BigDecimal("150").compareTo(r.outstanding()));
    }

    @Test
    void settleCannotExceedOutstanding() {
        FinanceSettleService.SettleResult r = service.computeSettle(
                new BigDecimal("500"), new BigDecimal("250"));

        assertEquals(0, new BigDecimal("250").compareTo(r.settled()));
        assertEquals(0, BigDecimal.ZERO.compareTo(r.outstanding()));
    }

    @Test
    void fullySettledOutstandingIsZero() {
        FinanceSettleService.SettleResult r = service.computeSettle(
                new BigDecimal("250"), new BigDecimal("250"));

        assertEquals(0, BigDecimal.ZERO.compareTo(r.outstanding()));
        assertTrue(r.settledCoversAll());
    }
}
