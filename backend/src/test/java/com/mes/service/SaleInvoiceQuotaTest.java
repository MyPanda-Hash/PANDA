package com.mes.service;

import org.junit.jupiter.api.Test;

import java.util.LinkedHashMap;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 销售发票额度与红冲口径：
 * - 开票额度按「销货单含税金额 - 已占用」算，超出必须拦下，否则同一张销货单能被开两次；
 * - 红字发票数量/税额/价税合计取负、无税单价保持正数，红蓝两单相加必须归零；
 * - 红字票是冲回动作，不受额度上限约束，否则红冲会被自己的额度挡住。
 */
class SaleInvoiceQuotaTest {

    private static Map<String, Object> invoiceRow(double qty, double netPrice, double tax, double gross) {
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("存货编码", "CP001");
        row.put("存货名称", "铝棒 Φ80");
        row.put("数量", qty);
        row.put("无税单价", netPrice);
        row.put("税率%", 13);
        row.put("税额", tax);
        row.put("价税合计", gross);
        return row;
    }

    @Test
    void redRowFlipsQuantityTaxAndGrossOnly() {
        Map<String, Object> red = PxService.negateInvoiceRow(invoiceRow(200, 15.5, 403.12, 3504));
        assertEquals(-200.0, ((Number) red.get("数量")).doubleValue(), 1e-9);
        assertEquals(-403.12, ((Number) red.get("税额")).doubleValue(), 1e-9);
        assertEquals(-3504.0, ((Number) red.get("价税合计")).doubleValue(), 1e-9);
        // 无税单价保持正数：无税金额 = Σ 数量×无税单价，符号由数量承担
        assertEquals(15.5, ((Number) red.get("无税单价")).doubleValue(), 1e-9);
        assertEquals("CP001", red.get("存货编码"));
    }

    @Test
    void redAndBlueRowsCancelOut() {
        Map<String, Object> blue = invoiceRow(200, 15.5, 403.12, 3504);
        Map<String, Object> red = PxService.negateInvoiceRow(blue);
        double netDiff = ((Number) blue.get("数量")).doubleValue() * ((Number) blue.get("无税单价")).doubleValue()
                + ((Number) red.get("数量")).doubleValue() * ((Number) red.get("无税单价")).doubleValue();
        double taxDiff = ((Number) blue.get("税额")).doubleValue() + ((Number) red.get("税额")).doubleValue();
        double grossDiff = ((Number) blue.get("价税合计")).doubleValue() + ((Number) red.get("价税合计")).doubleValue();
        assertEquals(0.0, netDiff, 1e-9);
        assertEquals(0.0, taxDiff, 1e-9);
        assertEquals(0.0, grossDiff, 1e-9);
    }

    @Test
    void quotaLeftIsSourceMinusIssued() {
        assertEquals(0.0, PxService.remainingInvoiceQuota(19640, 19640), 1e-9);
        assertEquals(9820.0, PxService.remainingInvoiceQuota(19640, 9820), 1e-9);
        // 红冲后已占用变负，额度自动恢复并多出可开空间
        assertEquals(19640.0, PxService.remainingInvoiceQuota(19640, 0), 1e-9);
        assertEquals(19639.99, PxService.remainingInvoiceQuota(19640, 0.01), 0.001);
    }

    @Test
    void overQuotaIsRejected() {
        double remaining = PxService.remainingInvoiceQuota(19640, 0);
        assertTrue(PxService.withinInvoiceQuota(remaining, 19640));
        assertTrue(PxService.withinInvoiceQuota(remaining, 19639.99));
        assertFalse(PxService.withinInvoiceQuota(remaining, 19640.01));
        // 已占用满额度后再开一分钱必须被拦
        double exhausted = PxService.remainingInvoiceQuota(19640, 19640);
        assertFalse(PxService.withinInvoiceQuota(exhausted, 0.01));
        assertTrue(PxService.remainingInvoiceQuota(19640, 19640) <= 0);
    }

    @Test
    void redInvoiceIsNotBlockedByQuota() {
        // 额度已用完（剩余 0）时，红字票仍要能开出，否则无法冲回
        double exhausted = PxService.remainingInvoiceQuota(19640, 19640);
        assertTrue(PxService.withinInvoiceQuota(exhausted, -3504));
        assertTrue(PxService.withinInvoiceQuota(exhausted, 0));
    }

    /**
     * 实测踩到的坑：真实销货单明细只有「数量/单价」，没有「含税金额/含税单价」，
     * 只认含税字段会把额度算成 0，直接报「额度已用完 0.00」把人挡死。
     */
    @Test
    void itemGrossFallsBackToUnitPrice() {
        Map<String, Object> onlyUnitPrice = new LinkedHashMap<>();
        onlyUnitPrice.put("数量", 10);
        onlyUnitPrice.put("单价", 100);
        assertEquals(1000.0, PxService.itemGrossAmount(onlyUnitPrice), 1e-9);

        Map<String, Object> withTaxPrice = new LinkedHashMap<>();
        withTaxPrice.put("数量", 10);
        withTaxPrice.put("单价", 100);
        withTaxPrice.put("含税单价", 113);
        assertEquals(1130.0, PxService.itemGrossAmount(withTaxPrice), 1e-9);

        Map<String, Object> withGross = new LinkedHashMap<>();
        withGross.put("数量", 10);
        withGross.put("单价", 100);
        withGross.put("含税金额", 1200);
        assertEquals(1200.0, PxService.itemGrossAmount(withGross), 1e-9);

        Map<String, Object> amountPlusTax = new LinkedHashMap<>();
        amountPlusTax.put("金额", 800);
        amountPlusTax.put("税额", 104);
        assertEquals(904.0, PxService.itemGrossAmount(amountPlusTax), 1e-9);
    }
}