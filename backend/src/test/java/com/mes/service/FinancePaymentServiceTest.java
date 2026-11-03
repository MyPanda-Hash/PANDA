package com.mes.service;

import com.mes.entity.FinanceVoucher;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 收付款单 → 往来核销：金额分配、超额拒绝、多单分摊。
 * 单厂单账套，人民币。
 */
class FinancePaymentServiceTest {

    private final FinancePaymentService service = new FinancePaymentService(null, null);

    @Test
    void allocateAcrossVouchersInDateOrder() {
        FinanceVoucher a = voucher("AR", "100", "0");
        FinanceVoucher b = voucher("AR", "150", "0");

        var plan = service.allocate(new BigDecimal("200"), java.util.List.of(a, b));

        assertEquals(2, plan.size());
        assertEquals(0, new BigDecimal("100").compareTo(plan.get(0).amount()));
        assertEquals(0, new BigDecimal("100").compareTo(plan.get(1).amount()));
    }

    @Test
    void skipsFullySettledVouchers() {
        FinanceVoucher settled = voucher("AR", "100", "100");
        FinanceVoucher open = voucher("AR", "150", "50");

        var plan = service.allocate(new BigDecimal("120"), java.util.List.of(settled, open));

        assertEquals(1, plan.size());
        assertEquals(0, new BigDecimal("100").compareTo(plan.get(0).amount()));
    }

    @Test
    void stopsWhenPaymentIsExhausted() {
        FinanceVoucher a = voucher("AR", "100", "0");
        FinanceVoucher b = voucher("AR", "150", "0");

        var plan = service.allocate(new BigDecimal("80"), java.util.List.of(a, b));

        assertEquals(1, plan.size());
        assertEquals(0, new BigDecimal("80").compareTo(plan.get(0).amount()));
    }

    @Test
    void zeroPaymentProducesEmptyPlan() {
        FinanceVoucher a = voucher("AR", "100", "0");

        assertTrue(service.allocate(BigDecimal.ZERO, java.util.List.of(a)).isEmpty());
        assertTrue(service.allocate(new BigDecimal("-5"), java.util.List.of(a)).isEmpty());
        assertTrue(service.allocate(new BigDecimal("100"), java.util.List.of()).isEmpty());
    }

    @Test
    void rejectsOverPayment() {
        FinanceVoucher a = voucher("AR", "100", "0");

        IllegalStateException e = assertThrows(IllegalStateException.class,
                () -> service.allocateWithinLimit(new BigDecimal("150"), java.util.List.of(a)));
        assertTrue(e.getMessage().contains("超出"));
    }

    @Test
    void plainAllocateToleratesOverPaymentByLeavingRemainderUnapplied() {
        FinanceVoucher a = voucher("AR", "100", "0");

        // allocate 只按可用余额分配，多出的钱不进入核销计划（预收应另立单据）
        var plan = service.allocate(new BigDecimal("150"), java.util.List.of(a));

        assertEquals(0, new BigDecimal("100").compareTo(plan.get(0).amount()));
    }

    @Test
    void remainingAmountIsComputed() {
        assertEquals(0, new BigDecimal("50").compareTo(service.remaining(new BigDecimal("100"), new BigDecimal("50"))));
        assertEquals(0, BigDecimal.ZERO.compareTo(service.remaining(new BigDecimal("100"), new BigDecimal("120"))));
    }

    @Test
    void voucherIsSettledOnlyWhenFullyCovered() {
        assertFalse(service.isFullySettled(new BigDecimal("100"), new BigDecimal("99.99")));
        assertTrue(service.isFullySettled(new BigDecimal("100"), new BigDecimal("100")));
        assertTrue(service.isFullySettled(BigDecimal.ZERO, BigDecimal.ZERO));
    }

    private FinanceVoucher voucher(String type, String amount, String settled) {
        FinanceVoucher v = new FinanceVoucher();
        v.setVoucherType(type);
        v.setAmount(new BigDecimal(amount));
        v.setSettledAmount(new BigDecimal(settled));
        return v;
    }
}
