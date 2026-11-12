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
 * 应收应付结算：把业务单据折算成应收单/应付单与核销记录。
 * 单厂单账套，不引入多币种与组织维度。
 */
class FinanceSettleServiceTest {

    private final FinanceSettleService service = new FinanceSettleService(null, null);

    @Test
    void purchaseInDoesNotCreatePayable() {
        Map<String, Object> head = Map.of("单据编号", "RK-001", "供应商", "华东铝业", "单据日期", "2026-11-02");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货名称", "铝棒", "实收数量", 100, "单价", 2, "金额", 200),
                Map.of("存货名称", "铝板", "实收数量", 10, "单价", 5, "金额", 50)));

        // 票到确认：入库只入库存，不产生应付；应付由采购发票审核产生
        assertNull(service.buildSettlement("PURCHASE_IN", "RK-001", head, detail));
        assertNull(service.typeOf("PURCHASE_IN"));
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
        Map<String, Object> head = Map.of("单据编号", "PF-002", "供应商", "华东铝业");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货名称", "铝棒", "数量", 0, "单价", 0, "金额", 0)));

        assertNull(service.buildSettlement("PU_INVOICE", "PF-002", head, detail));
    }

    @Test
    void amountFallsBackToQuantityTimesPrice() {
        Map<String, Object> head = Map.of("单据编号", "PF-003", "供应商", "华东铝业");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货名称", "铝棒", "数量", 4, "单价", 2.5)));

        FinanceSettleService.Settlement s = service.buildSettlement("PU_INVOICE", "PF-003", head, detail);

        assertEquals(0, new BigDecimal("10").compareTo(s.amount()));
    }

    @Test
    void outsourceFeeCreatesPayableWithOutsourceSupplier() {
        // 委外加工费用单：明细金额字段是「费用金额」，往来单位在表头「委外供应商」
        Map<String, Object> head = Map.of("单据编号", "WY-001", "委外供应商", "华东热处理厂",
                "委外加工单号", "WW-001");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("产品名称", "轴套 C", "数量", 10, "委外单价", 6.5, "费用金额", 65)));

        FinanceSettleService.Settlement s = service.buildSettlement("OUTSOURCE_FEE", "WY-001", head, detail);

        assertNotNull(s, "委外加工费用单必须产生应付");
        assertEquals("AP", s.type());
        assertEquals("华东热处理厂", s.partner(), "应付要挂委外供应商，否则账龄与核销找不到往来单位");
        assertEquals(0, new BigDecimal("65").compareTo(s.amount()));
    }

    @Test
    void outsourceInDoesNotCreatePayable() {
        // 票到确认口径：委外入库只落库存，加工费由「委外加工费用单」确认应付，
        // 否则同一笔加工费在入库和费用单各记一次（实测 WR/WY 各 65 → 应付翻倍）
        assertNull(service.typeOf("OUTSOURCE_IN"), "委外入库不挂应付，避免与费用单双记");
        assertEquals("AP", service.typeOf("OUTSOURCE_FEE"));
    }

    @Test
    void typeOfPanelsIsExposedForCallers() {
        // 票到确认：采购入库不再产生 AP，采购发票才是应付来源
        assertNull(service.typeOf("PURCHASE_IN"));
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
