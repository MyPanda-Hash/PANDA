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

    private FinanceVoucher voucherOf(String partner, String amount) {
        FinanceVoucher v = new FinanceVoucher();
        v.setVoucherType("AR");
        v.setPartner(partner);
        v.setAmount(new BigDecimal(amount));
        v.setSettledAmount(BigDecimal.ZERO);
        return v;
    }

    /**
     * 核销必须按往来单位隔离：此前候选池只按 voucherType 取，
     * A 客户的收款单能把 B 客户的应收核掉（账面对不上，且没人能发现）。
     */
    @Test
    void filterByPartnerKeepsOnlySamePartner() {
        var pool = java.util.List.of(
                voucherOf("中天精工", "100"),
                voucherOf("流程测试客户", "200"),
                voucherOf("中天精工", "300"));

        var scoped = FinancePaymentService.filterByPartner(pool, "中天精工");

        assertEquals(2, scoped.size());
        assertTrue(scoped.stream().allMatch(v -> "中天精工".equals(v.getPartner())));
        assertEquals(0, new BigDecimal("400").compareTo(
                scoped.stream().map(FinanceVoucher::getAmount).reduce(BigDecimal.ZERO, BigDecimal::add)));
    }

    @Test
    void filterByPartnerIgnoresSurroundingSpaces() {
        var pool = java.util.List.of(voucherOf(" 中天精工 ", "100"));

        assertEquals(1, FinancePaymentService.filterByPartner(pool, "中天精工").size());
        assertEquals(1, FinancePaymentService.filterByPartner(pool, " 中天精工").size());
    }

    @Test
    void emptyPartnerMatchesNothing() {
        var pool = java.util.List.of(voucherOf("中天精工", "100"), voucherOf("", "200"));

        // 没填往来单位就不猜：宁可报错也不能拿别人的应收去核
        assertTrue(FinancePaymentService.filterByPartner(pool, "").isEmpty());
        assertTrue(FinancePaymentService.filterByPartner(pool, null).isEmpty());
        assertTrue(FinancePaymentService.filterByPartner(null, "中天精工").isEmpty());
    }

    @Test
    void filterByPartnerDoesNotMutateInput() {
        var pool = java.util.List.of(voucherOf("中天精工", "100"), voucherOf("流程测试客户", "200"));

        FinancePaymentService.filterByPartner(pool, "中天精工");

        assertEquals(2, pool.size());
    }

    // ---------- 手工核销计划（指定到某张单 / 只核一部分）----------

    private FinanceVoucher voucherNo(String sourceNo, String amount, String settled) {
        FinanceVoucher v = new FinanceVoucher();
        v.setVoucherType("AR");
        v.setSourceNo(sourceNo);
        v.setAmount(new BigDecimal(amount));
        v.setSettledAmount(new BigDecimal(settled));
        return v;
    }

    private FinancePaymentService.PlanItem planItem(String sourceNo, String amount) {
        return new FinancePaymentService.PlanItem(sourceNo, new BigDecimal(amount));
    }

    @Test
    void manualPlanTakesOnlyTheRequestedAmount() {
        FinanceVoucher a = voucherNo("XS-001", "1000", "0");
        FinanceVoucher b = voucherNo("XS-002", "1000", "0");

        var plan = FinancePaymentService.validatePlan(
                java.util.List.of(planItem("XS-001", "300")), java.util.List.of(a, b), new BigDecimal("2000"));

        assertEquals(1, plan.size(), "只核指定的那一张，不碰别的");
        assertEquals(0, new BigDecimal("300").compareTo(plan.get(0).amount()));
    }

    @Test
    void manualPlanAllowsPartialPaymentLeavingRestUnapplied() {
        FinanceVoucher a = voucherNo("XS-001", "1000", "0");

        var plan = FinancePaymentService.validatePlan(
                java.util.List.of(planItem("XS-001", "400")), java.util.List.of(a), new BigDecimal("1000"));

        // 收 1000 只核 400：这就是「部分核销」，剩下的 600 留在收款单上
        assertEquals(0, new BigDecimal("400").compareTo(plan.get(0).amount()));
    }

    @Test
    void manualPlanRejectsAmountOverOpen() {
        FinanceVoucher a = voucherNo("XS-001", "1000", "600");

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                FinancePaymentService.validatePlan(
                        java.util.List.of(planItem("XS-001", "500")), java.util.List.of(a), new BigDecimal("500")));
        assertTrue(e.getMessage().contains("超出未结额"), e.getMessage());
    }

    @Test
    void manualPlanRejectsSumOverPayment() {
        FinanceVoucher a = voucherNo("XS-001", "1000", "0");
        FinanceVoucher b = voucherNo("XS-002", "1000", "0");

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                FinancePaymentService.validatePlan(
                        java.util.List.of(planItem("XS-001", "300"), planItem("XS-002", "300")),
                        java.util.List.of(a, b), new BigDecimal("500")));
        assertTrue(e.getMessage().contains("超出本次金额"), e.getMessage());
    }

    @Test
    void manualPlanRejectsUnknownSourceNo() {
        FinanceVoucher a = voucherNo("XS-001", "1000", "0");

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                FinancePaymentService.validatePlan(
                        java.util.List.of(planItem("XS-999", "100")), java.util.List.of(a), new BigDecimal("100")));
        assertTrue(e.getMessage().contains("不在此次可核销范围"), e.getMessage());
    }

    @Test
    void manualPlanRejectsNonPositiveAmountAndBlankSource() {
        FinanceVoucher a = voucherNo("XS-001", "1000", "0");

        assertThrows(IllegalStateException.class, () -> FinancePaymentService.validatePlan(
                java.util.List.of(planItem("XS-001", "0")), java.util.List.of(a), new BigDecimal("100")));
        assertThrows(IllegalStateException.class, () -> FinancePaymentService.validatePlan(
                java.util.List.of(planItem("", "10")), java.util.List.of(a), new BigDecimal("100")));
    }

    @Test
    void manualPlanAccumulatesDuplicateRowsForSameVoucher() {
        FinanceVoucher a = voucherNo("XS-001", "1000", "0");

        // 同一张单写两行 600+600 超过未结 1000，必须拦
        assertThrows(IllegalStateException.class, () -> FinancePaymentService.validatePlan(
                java.util.List.of(planItem("XS-001", "600"), planItem("XS-001", "600")),
                java.util.List.of(a), new BigDecimal("1200")));

        var ok = FinancePaymentService.validatePlan(
                java.util.List.of(planItem("XS-001", "600"), planItem("XS-001", "400")),
                java.util.List.of(a), new BigDecimal("1000"));
        assertEquals(2, ok.size());
    }

    @Test
    void emptyManualPlanFallsBackToAuto() {
        assertTrue(FinancePaymentService.validatePlan(
                java.util.List.of(), java.util.List.of(voucherNo("XS-001", "100", "0")), BigDecimal.TEN).isEmpty());
        assertTrue(FinancePaymentService.validatePlan(
                null, java.util.List.of(voucherNo("XS-001", "100", "0")), BigDecimal.TEN).isEmpty());
    }
}
