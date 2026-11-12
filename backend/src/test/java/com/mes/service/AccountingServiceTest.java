package com.mes.service;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 会计凭证生成：业务单据按模板折成借贷分录。
 * 注意术语区分：FinanceVoucher=往来单据；AccountingEntry=会计凭证分录。
 */
class AccountingServiceTest {

    private final AccountingService service = new AccountingService(null);

    @Test
    void purchaseInvoiceDebitsInventoryCreditsPayable() {
        List<AccountingService.Entry> entries = service.buildEntries(
                "PU_INVOICE", "PI-001", bd("250"), "华东铝业", "铝棒等");

        assertEquals(2, entries.size());
        AccountingService.Entry debit = entries.get(0);
        AccountingService.Entry credit = entries.get(1);
        assertEquals("1405", debit.accountCode());
        assertEquals("库存商品", debit.accountName());
        assertEquals("借", debit.direction());
        assertEquals(0, bd("250").compareTo(debit.amount()));

        assertEquals("2202", credit.accountCode());
        assertEquals("应付账款", credit.accountName());
        assertEquals("贷", credit.direction());
        assertEquals(0, bd("250").compareTo(credit.amount()));
    }

    /** 收入与应收都在销货单确认；出库单只结转成本（借 主营业务成本 / 贷 库存商品）。 */
    @Test
    void purchaseInProducesNoEntries() {
        // 票到确认（2026-11-06）：入库只落库存，凭证由采购发票审核生成，避免入库+发票双记
        assertTrue(service.buildEntries("PURCHASE_IN", "RK-001", bd("250"), "华东铝业", "铝棒等").isEmpty());
        assertNull(service.templateOf("PURCHASE_IN"));
    }

    /** 委外加工费：借 生产成本 / 贷 应付账款（应付与分录同口径，入库不记账避免双记）。 */
    @Test
    void outsourceFeeDebitsProductionCostCreditsPayable() {
        List<AccountingService.Entry> entries = service.buildEntries(
                "OUTSOURCE_FEE", "WY-001", bd("65"), "华东热处理厂", "委外加工费用");

        assertEquals(2, entries.size());
        assertEquals("5001", entries.get(0).accountCode());
        assertEquals("生产成本", entries.get(0).accountName());
        assertEquals("借", entries.get(0).direction());
        assertEquals("2202", entries.get(1).accountCode());
        assertEquals("应付账款", entries.get(1).accountName());
        assertEquals("贷", entries.get(1).direction());
        assertEquals(0, bd("65").compareTo(entries.get(1).amount()));
    }

    /** 委外入库只落库存，凭证由费用单出，否则加工费双记。 */
    @Test
    void outsourceInProducesNoEntries() {
        assertTrue(service.buildEntries("OUTSOURCE_IN", "WR-001", bd("65"), "华东热处理厂", "委外入库").isEmpty());
        assertNull(service.templateOf("OUTSOURCE_IN"));
    }

    @Test
    void saleOutDebitsReceivableCreditsRevenue() {
        List<AccountingService.Entry> entries = service.buildEntries(
                "SALE_OUT", "CK-001", bd("300"), "中天精工", "成品A");

        assertEquals(2, entries.size());
        assertEquals("6401", entries.get(0).accountCode());   // 主营业务成本 借
        assertEquals("主营业务成本", entries.get(0).accountName());
        assertEquals("借", entries.get(0).direction());
        assertEquals("1405", entries.get(1).accountCode());   // 库存商品 贷
        assertEquals("贷", entries.get(1).direction());
        assertTrue(service.buildEntries("SALE_INVOICE", "FP-001", bd("300"), "中天精工", "成品A").isEmpty(),
                "发票不重复入账");
    }

    @Test
    void saleInvDebitsReceivableCreditsRevenue() {
        List<AccountingService.Entry> entries = service.buildEntries(
                "SALE_INV", "XS-001", bd("300"), "中天精工", "成品A");

        assertEquals(2, entries.size());
        assertEquals("1122", entries.get(0).accountCode());   // 应收账款 借
        assertEquals("借", entries.get(0).direction());
        assertEquals("6001", entries.get(1).accountCode());   // 主营业务收入 贷
        assertEquals("贷", entries.get(1).direction());
    }

    @Test
    void receiptDebitsBankCreditsReceivable() {
        List<AccountingService.Entry> entries = service.buildEntries(
                "RECEIPT", "SK-001", bd("200"), "中天精工", "收款");

        assertEquals(2, entries.size());
        assertEquals("1002", entries.get(0).accountCode());   // 银行存款 借
        assertEquals("1122", entries.get(1).accountCode());   // 应收账款 贷
    }

    @Test
    void paymentDebitsPayableCreditsBank() {
        List<AccountingService.Entry> entries = service.buildEntries(
                "RECEIPT_PAYABLE", "FK-001", bd("150"), "华东铝业", "付款");

        assertEquals(2, entries.size());
        assertEquals("2202", entries.get(0).accountCode());   // 应付账款 借
        assertEquals("1002", entries.get(1).accountCode());   // 银行存款 贷
    }

    @Test
    void purchaseReturnReversesInventoryAndPayable() {
        List<AccountingService.Entry> entries = service.buildEntries(
                "PURCHASE_RETURN", "CT-001", bd("100"), "华东铝业", "退货");

        assertEquals(2, entries.size());
        assertEquals("2202", entries.get(0).accountCode());   // 应付账款 借（冲减）
        assertEquals("1405", entries.get(1).accountCode());   // 库存商品 贷（冲减）
    }

    @Test
    void saleReturnReversesRevenueAndReceivable() {
        List<AccountingService.Entry> entries = service.buildEntries(
                "SALE_RETURN", "XT-001", bd("150"), "中天精工", "退货");

        assertEquals(2, entries.size());
        assertEquals("6001", entries.get(0).accountCode());   // 主营业务收入 借（冲减）
        assertEquals("1122", entries.get(1).accountCode());   // 应收账款 贷（冲减）
    }

    @Test
    void balancedEntriesSumToZero() {
        List<AccountingService.Entry> entries = service.buildEntries(
                "PU_INVOICE", "PI-002", bd("888.88"), "供应商A", "混合物料");

        BigDecimal debit = BigDecimal.ZERO;
        BigDecimal credit = BigDecimal.ZERO;
        for (AccountingService.Entry e : entries) {
            if ("借".equals(e.direction())) debit = debit.add(e.amount());
            else credit = credit.add(e.amount());
        }
        assertEquals(0, debit.compareTo(credit), "借贷必须相等");
        assertTrue(service.isBalanced(entries));
    }

    @Test
    void nonAccountingPanelsProduceNoEntries() {
        assertTrue(service.buildEntries("SO_ORDER", "SO-001", bd("100"), "客户", "备注").isEmpty());
        assertTrue(service.buildEntries("MATERIAL_OUT", "CL-001", bd("100"), "车间", "领料").isEmpty());
        assertNull(service.templateOf("SO_ORDER"));
    }

    @Test
    void zeroAmountProducesNoEntries() {
        assertTrue(service.buildEntries("PU_INVOICE", "PI-003", BigDecimal.ZERO, "供应商", "零金额").isEmpty());
        assertTrue(service.buildEntries("PU_INVOICE", "PI-004", null, "供应商", "空金额").isEmpty());
    }

    @Test
    void rejectsUnbalancedEntries() {
        List<AccountingService.Entry> bad = List.of(
                new AccountingService.Entry("1405", "库存商品", "借", bd("100"), "测试"),
                new AccountingService.Entry("2202", "应付账款", "贷", bd("90"), "测试"));

        assertThrows(IllegalStateException.class, () -> service.assertBalanced(bad));
    }

    @Test
    void templateCoversAllFinancialPanels() {
        for (String panel : List.of("PU_INVOICE", "SALE_INV", "SALE_OUT",
                "RECEIPT", "RECEIPT_PAYABLE", "PURCHASE_RETURN", "SALE_RETURN")) {
            assertTrue(service.templateOf(panel) != null, panel + " 缺少凭证模板");
        }
        assertNull(service.templateOf("PURCHASE_IN"), "票到确认：采购入库不再生成凭证");
        assertNull(service.templateOf("SALE_INVOICE"), "销售发票只是票据，应收在销货单确认");
    }

    @Test
    void summaryIncludesSourceAndPartner() {
        List<AccountingService.Entry> entries = service.buildEntries(
                "SALE_INV", "XS-002", bd("500"), "西部材料", "铝板");

        for (AccountingService.Entry e : entries) {
            assertEquals("西部材料", e.partner());
        }
    }

    private static BigDecimal bd(String v) {
        return new BigDecimal(v);
    }

    /** 销货单拆销项税：借应收（含税），贷收入（不含税）+ 贷销项税额。 */
    @Test
    void saleInvoiceSplitsOutputTax() {
        List<AccountingService.Entry> entries = service.buildSaleEntries(
                "XS-001", bd("1130"), bd("130"), "中天精工", "普通销售");

        assertEquals(3, entries.size());
        assertEquals("1122", entries.get(0).accountCode());
        assertEquals("借", entries.get(0).direction());
        assertEquals(0, bd("1130").compareTo(entries.get(0).amount()));
        assertEquals("6001", entries.get(1).accountCode());
        assertEquals("贷", entries.get(1).direction());
        assertEquals(0, bd("1000").compareTo(entries.get(1).amount()));
        assertEquals("222101", entries.get(2).accountCode());
        assertEquals("贷", entries.get(2).direction());
        assertEquals(0, bd("130").compareTo(entries.get(2).amount()));
        assertTrue(service.isBalanced(entries), "借贷必须平衡");
    }

    /** 没有税额时退化为两行（借应收 / 贷收入），不凭空造销项税。 */
    @Test
    void saleInvoiceWithoutTaxFallsBackToTwoEntries() {
        List<AccountingService.Entry> entries = service.buildSaleEntries(
                "XS-002", bd("500"), BigDecimal.ZERO, "西部材料", "普通销售");

        assertEquals(2, entries.size());
        assertEquals(0, bd("500").compareTo(entries.get(1).amount()));
    }
}