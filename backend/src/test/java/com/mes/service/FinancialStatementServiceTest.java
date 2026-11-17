package com.mes.service;

import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;

import java.math.BigDecimal;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/**
 * 三大财务报表：符号口径与结转后的取数。
 *
 * <p>盯的是实测踩到的两个坑：
 * ① 累计折旧(1602) 是资产类但贷方，按 direction 判正负会让资产虚增一倍折旧；
 * ② 利润表取余额，被期末结转凭证冲平后整张表变 0。
 */
class FinancialStatementServiceTest {

    private final JdbcTemplate jdbc = mock(JdbcTemplate.class);
    private final FinancialStatementService service = new FinancialStatementService(jdbc);

    private Map<String, Object> balance(String code, String name, String category, String direction, double net) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("account_code", code);
        m.put("account_name", name);
        m.put("category", category);
        m.put("direction", direction);
        m.put("net", BigDecimal.valueOf(net));
        return m;
    }

    private Map<String, Object> profit(double income, double expense) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("income", BigDecimal.valueOf(income));
        m.put("expense", BigDecimal.valueOf(expense));
        return m;
    }

    private BigDecimal amountOf(List<Map<String, Object>> rows, String name) {
        return rows.stream()
                .filter(r -> name.equals(String.valueOf(r.get("项目名称"))))
                .map(r -> new BigDecimal(String.valueOf(r.get("金额"))))
                .findFirst().orElseThrow();
    }

    @Test
    void contraAssetReducesTotalAssets() {
        // 银行存款 937.8（借）／累计折旧 950（资产类、贷方余额）
        when(jdbc.queryForList(anyString(), any(Object[].class))).thenReturn(
                List.of(balance("1002", "银行存款", "资产", "借", 937.8),
                        balance("1602", "累计折旧", "资产", "贷", -950)),
                List.of());
        when(jdbc.queryForMap(anyString(), any(Object[].class))).thenReturn(profit(0, 0));

        List<Map<String, Object>> rows = service.balanceSheet("2026-11");

        assertEquals(0, new BigDecimal("-950.0").compareTo(amountOf(rows, "累计折旧")),
                "累计折旧必须冲减资产，显示为负数");
        assertEquals(0, new BigDecimal("-12.2").compareTo(amountOf(rows, "资产合计")),
                "资产合计 = 937.8 − 950");
    }

    @Test
    void creditNatureAccountsShowPositiveBalance() {
        when(jdbc.queryForList(anyString(), any(Object[].class))).thenReturn(
                List.of(balance("2202", "应付账款", "负债", "贷", -11124)),
                List.of());
        when(jdbc.queryForMap(anyString(), any(Object[].class))).thenReturn(profit(0, 0));

        List<Map<String, Object>> rows = service.balanceSheet("2026-11");

        assertEquals(0, new BigDecimal("11124").compareTo(amountOf(rows, "应付账款")),
                "负债类贷方余额应显示为正数");
    }

    @Test
    void incomeStatementIgnoresClosingVoucherAndUsesPeriodAmounts() {
        // 结转后损益科目净额为 0，但利润表要展示本期发生额
        when(jdbc.queryForList(anyString(), any(Object[].class)))
                .thenReturn(List.of(
                        withCategory("6001", "主营业务收入", "收入", "贷", -29.6),
                        withCategory("6401", "主营业务成本", "费用", "借", 45.77),
                        withCategory("6602", "折旧费用", "费用", "借", 950)));

        List<Map<String, Object>> rows = service.incomeStatement("2026-11");

        assertEquals(0, new BigDecimal("29.6").compareTo(amountOf(rows, "营业收入合计")));
        assertEquals(0, new BigDecimal("995.77").compareTo(amountOf(rows, "成本费用合计")));
        assertEquals(0, new BigDecimal("-966.17").compareTo(amountOf(rows, "净利润")));

        // 光断言数字不够：mock 不经过 SQL 条件，得直接盯住取数口径，
        // 否则把「排除结转凭证」这行删掉测试照样绿（实测过这个盲区）。
        org.mockito.ArgumentCaptor<String> sql = org.mockito.ArgumentCaptor.forClass(String.class);
        org.mockito.Mockito.verify(jdbc, org.mockito.Mockito.atLeastOnce())
                .queryForList(sql.capture(), any(Object[].class));
        assertTrue(sql.getAllValues().stream().anyMatch(s -> s.contains("PERIOD_CLOSE")),
                "利润表必须排除期末结转凭证，否则结账后整张表变成 0");
    }

    @Test
    void balanceSheetSurfacesUnregisteredAccountsInsteadOfDroppingThem() {
        when(jdbc.queryForList(anyString(), any(Object[].class))).thenReturn(
                List.of(balance("1002", "银行存款", "资产", "借", 100)),
                List.of(balance("9999", "野科目", null, "借", 100))); // 科目表没有的科目
        when(jdbc.queryForMap(anyString(), any(Object[].class))).thenReturn(profit(0, 0));

        List<Map<String, Object>> rows = service.balanceSheet("2026-11");

        assertTrue(rows.stream().anyMatch(r -> "异常".equals(String.valueOf(r.get("项目类别")))),
                "科目表缺失的科目必须显式列出，不能静默丢金额");
    }

    /**
     * 封账后结转必须被拒 —— 即使该期间已经没有可结转的损益发生额。
     *
     * <p>回归保护：早先「无发生额就返回 0」的分支在校验之前短路，
     * 导致已封账的期间点「结转损益」返回 200 成功、实际什么也没做（实测踩到）。
     */
    @Test
    void carryForwardRejectsClosedPeriodEvenWhenNothingToCarry() {
        AccountingPeriodService periods = mock(AccountingPeriodService.class);
        AccountingService accounting = mock(AccountingService.class);
        FinancialStatementService local = new FinancialStatementService(jdbc);
        local.setPeriodService(periods);
        local.setAccountingService(accounting);

        when(jdbc.queryForList(anyString(), any(Object[].class))).thenReturn(List.of());
        org.mockito.Mockito.doThrow(new IllegalStateException("会计期间 2026-11 已封账，不能结转损益"))
                .when(periods).requireOpen("2026-11", "结转损益");

        IllegalStateException e = org.junit.jupiter.api.Assertions.assertThrows(
                IllegalStateException.class, () -> local.carryForward("2026-11"));
        assertTrue(e.getMessage().contains("已封账"));
    }

    @Test
    void cashFlowSplitsByCounterpartAccount() {
        when(jdbc.queryForList(anyString(), any(Object[].class))).thenReturn(List.of(
                // 凭证 V1：借 银行存款 100 / 贷 主营业务收入 100 → 经营
                entry("V1", "1002", "借", 100),
                entry("V1", "6001", "贷", 100),
                // 凭证 V2：借 固定资产 500 / 贷 银行存款 500 → 投资
                entry("V2", "1002", "贷", 500),
                entry("V2", "1601", "借", 500)));

        List<Map<String, Object>> rows = service.cashFlow("2026-11");

        assertEquals(0, new BigDecimal("100").compareTo(amountOf(rows, "经营活动产生的现金流量净额")));
        assertEquals(0, new BigDecimal("-500").compareTo(amountOf(rows, "投资活动产生的现金流量净额")));
        assertEquals(0, new BigDecimal("-400").compareTo(amountOf(rows, "现金及现金等价物净增加额")));
    }

    private Map<String, Object> withCategory(String code, String name, String category,
                                             String direction, double net) {
        Map<String, Object> m = balance(code, name, category, direction, net);
        return m;
    }

    private Map<String, Object> entry(String voucher, String code, String direction, double amount) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("voucher_no", voucher);
        m.put("account_code", code);
        m.put("direction", direction);
        m.put("amount", BigDecimal.valueOf(amount));
        return m;
    }
}
