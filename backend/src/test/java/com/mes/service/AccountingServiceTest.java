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

    @Test
    void purchaseInProducesNoEntries() {
        // 票到确认（2026-11-06）：入库只落库存，凭证由采购发票审核生成，避免入库+发票双记
        assertTrue(service.buildEntries("PURCHASE_IN", "RK-001", bd("250"), "华东铝业", "铝棒等").isEmpty());
        assertNull(service.templateOf("PURCHASE_IN"));
    }

    @Test
    void saleOutDebitsReceivableCreditsRevenue() {
        List<AccountingService.Entry> entries = service.buildEntries(
                "SALE_OUT", "CK-001", bd("300"), "中天精工", "成品A");

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
        for (String panel : List.of("PU_INVOICE", "SALE_OUT", "SALE_INVOICE",
                "RECEIPT", "RECEIPT_PAYABLE", "PURCHASE_RETURN", "SALE_RETURN")) {
            assertTrue(service.templateOf(panel) != null, panel + " 缺少凭证模板");
        }
        assertNull(service.templateOf("PURCHASE_IN"), "票到确认：采购入库不再生成凭证");
    }

    @Test
    void summaryIncludesSourceAndPartner() {
        List<AccountingService.Entry> entries = service.buildEntries(
                "SALE_OUT", "CK-002", bd("500"), "西部材料", "铝板");

        for (AccountingService.Entry e : entries) {
            assertEquals("西部材料", e.partner());
        }
    }

    private static BigDecimal bd(String v) {
        return new BigDecimal(v);
    }
}
