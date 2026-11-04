package com.mes.service;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 对账与账龄：SETTLE_CUSTOMER / SETTLE_SUPPLIER / AGING_ANALYSIS 三张报表。
 *
 * 这三个面板此前只有 panel_config 定义，没有数据源，查询恒返回 0 行。
 * 列定义来自 FinancePanelRegistry，测试按真实列锁定行为：
 * - 对账单：逐张单据一行，列 业务日期/往来单位/来源单据/来源单号/金额/已核销/未核销/结算状态
 * - 账龄表：按往来单位汇总，列 往来类型/往来单位/未结金额/未到期/30天内/31-60/61-90/90以上
 */
class FinanceStatementServiceTest {

    private final FinanceStatementService service = new FinanceStatementService();

    private FinanceStatementService.OpenItem item(String type, String partner, String amount,
                                                  String settled, String bizDate) {
        return new FinanceStatementService.OpenItem(type, partner,
                new BigDecimal(amount), new BigDecimal(settled), bizDate);
    }

    // ---------- 对账单 ----------

    @Test
    void customerStatementListsEachOpenDocument() {
        List<FinanceStatementService.OpenItem> items = List.of(
                item("AR", "中天精工", "250", "0", "2026-11-02"),
                item("AR", "中天精工", "150", "50", "2026-11-03"),
                item("AR", "华东铝业", "300", "0", "2026-10-01"),
                item("AP", "华东铝业", "120", "0", "2026-11-02"));

        List<Map<String, Object>> rows = service.statement(items, "AR");

        assertEquals(3, rows.size(), "应收侧 3 张未结单据，AP 不出现");
        for (Map<String, Object> row : rows) {
            assertNotNull(row.get("业务日期"));
            assertNotNull(row.get("往来单位"));
            assertNotNull(row.get("来源单据"));
            assertNotNull(row.get("来源单号"));
            assertNotNull(row.get("金额"));
            assertNotNull(row.get("已核销金额"));
            assertNotNull(row.get("未核销金额"));
            assertNotNull(row.get("结算状态"));
        }
    }

    @Test
    void statementMarksFullyOpenAsUnsettledAndPartialAsPartial() {
        List<FinanceStatementService.OpenItem> items = List.of(
                item("AR", "客户A", "250", "0", "2026-11-02"),
                item("AR", "客户B", "150", "50", "2026-11-03"));

        List<Map<String, Object>> rows = service.statement(items, "AR");

        assertEquals("未结算", rows.get(0).get("结算状态"));
        assertEquals("部分结算", rows.get(1).get("结算状态"));
        assertEquals(0, new BigDecimal("100").compareTo((BigDecimal) rows.get(1).get("未核销金额")));
    }

    @Test
    void statementExcludesFullySettledDocuments() {
        List<FinanceStatementService.OpenItem> items = List.of(
                item("AR", "客户A", "200", "200", "2026-11-01"),
                item("AR", "客户B", "100", "40", "2026-11-02"));

        List<Map<String, Object>> rows = service.statement(items, "AR");

        assertEquals(1, rows.size());
        assertEquals("客户B", rows.get(0).get("往来单位"));
    }

    @Test
    void statementReturnsEmptyWhenNothingOpen() {
        assertTrue(service.statement(List.of(), "AR").isEmpty());
        assertTrue(service.statement(
                List.of(item("AP", "供应商A", "100", "100", "2026-11-01")), "AR").isEmpty());
    }

    @Test
    void returnsAreCountedOnTheirOwnSide() {
        List<FinanceStatementService.OpenItem> items = List.of(
                item("AR", "客户A", "250", "0", "2026-11-02"),
                item("AR_RETURN", "客户A", "150", "0", "2026-11-03"));

        List<Map<String, Object>> rows = service.statement(items, "AR");

        assertEquals(2, rows.size(), "AR_RETURN 属于应收侧，必须一并列出");
    }

    @Test
    void emptyPartnerIsGroupedAsUnspecified() {
        List<FinanceStatementService.OpenItem> items = List.of(
                item("AP", "", "120", "0", "2026-11-02"));

        List<Map<String, Object>> rows = service.statement(items, "AP");

        assertEquals(1, rows.size());
        assertEquals("未指定", rows.get(0).get("往来单位"));
    }

    // ---------- 账龄 ----------

    @Test
    void agingBucketsSplitByDays() {
        List<FinanceStatementService.OpenItem> items = List.of(
                item("AR", "客户A", "100", "0", "2026-11-15"), // 15 天 → 30 天内
                item("AR", "客户A", "200", "0", "2026-10-15"), // 46 天 → 31-60
                item("AR", "客户A", "300", "0", "2026-09-15"), // 76 天 → 61-90
                item("AR", "客户A", "400", "0", "2026-06-15")); // 168 天 → 90 以上

        List<Map<String, Object>> rows = service.aging(items, null, "2026-11-30");

        assertEquals(1, rows.size());
        Map<String, Object> row = rows.get(0);
        assertEquals("应收", row.get("往来类型"));
        assertEquals("客户A", row.get("往来单位"));
        assertEquals(0, new BigDecimal("100").compareTo((BigDecimal) row.get("30天内")));
        assertEquals(0, new BigDecimal("200").compareTo((BigDecimal) row.get("31-60天")));
        assertEquals(0, new BigDecimal("300").compareTo((BigDecimal) row.get("61-90天")));
        assertEquals(0, new BigDecimal("400").compareTo((BigDecimal) row.get("90天以上")));
        assertEquals(0, new BigDecimal("1000").compareTo((BigDecimal) row.get("未结金额")));
    }

    @Test
    void agingSeparatesReceivableFromPayableForSamePartner() {
        List<FinanceStatementService.OpenItem> items = List.of(
                item("AR", "华东铝业", "300", "0", "2026-11-20"),
                item("AP", "华东铝业", "120", "0", "2026-11-20"));

        List<Map<String, Object>> rows = service.aging(items, null, "2026-11-30");

        assertEquals(2, rows.size(), "同名单位的应收与应付不能合并成一行");
        assertTrue(rows.stream().anyMatch(r -> "应收".equals(r.get("往来类型"))));
        assertTrue(rows.stream().anyMatch(r -> "应付".equals(r.get("往来类型"))));
    }

    @Test
    void agingUsesSettledAmountNetOfWriteOff() {
        List<FinanceStatementService.OpenItem> items = List.of(
                item("AR", "客户B", "500", "200", "2026-11-20"));

        List<Map<String, Object>> rows = service.aging(items, "AR", "2026-11-30");

        assertEquals(1, rows.size());
        assertEquals(0, new BigDecimal("300").compareTo((BigDecimal) rows.get(0).get("30天内")));
    }

    @Test
    void agingSkipsFullySettledAndCountsOverdueDays() {
        List<FinanceStatementService.OpenItem> items = List.of(
                item("AR", "客户C", "100", "100", "2026-01-01"),
                item("AR", "客户C", "50", "0", "2026-11-25"));

        List<Map<String, Object>> rows = service.aging(items, "AR", "2026-11-30");

        assertEquals(1, rows.size());
        assertEquals(0, new BigDecimal("50").compareTo((BigDecimal) rows.get(0).get("未结金额")));
        assertEquals(5, ((Number) rows.get(0).get("最长逾期天数")).intValue());
    }

    @Test
    void notDueAmountGoesToNotDueBucket() {
        // 业务日期晚于截止日：未到期，不进逾期档
        List<FinanceStatementService.OpenItem> items = List.of(
                item("AR", "客户D", "80", "0", "2026-12-15"));

        List<Map<String, Object>> rows = service.aging(items, "AR", "2026-11-30");

        assertEquals(1, rows.size());
        assertEquals(0, new BigDecimal("80").compareTo((BigDecimal) rows.get(0).get("未到期")));
        assertEquals(0, new BigDecimal("0").compareTo((BigDecimal) rows.get(0).get("30天内")));
        assertEquals(0, ((Number) rows.get(0).get("最长逾期天数")).intValue());
    }

    @Test
    void agingReturnsEmptyWhenNothingOpen() {
        assertTrue(service.aging(List.of(), "AR", "2026-11-30").isEmpty());
    }

    /**
     * 退货是应收的负数冲减：对账单上要看得见（否则客户退了货却查不到可抵扣的钱），
     * 账龄里要参与冲减（否则应收被退货单撑大）。
     */
    @Test
    void statementListsReturnAsNegativeCreditRow() {
        List<FinanceStatementService.OpenItem> items = List.of(
                item("AR", "客户A", "250", "0", "2026-11-02"),
                item("AR_RETURN", "客户A", "-150", "0", "2026-11-03"));

        List<Map<String, Object>> rows = service.statement(items, "AR");

        assertEquals(2, rows.size());
        assertEquals(0, new BigDecimal("-150").compareTo((BigDecimal) rows.get(1).get("未核销金额")));
        assertEquals(0, new BigDecimal("-150").compareTo((BigDecimal) rows.get(1).get("金额")));
    }

    @Test
    void agingNetsReturnAgainstReceivable() {
        List<FinanceStatementService.OpenItem> items = List.of(
                item("AR", "客户A", "250", "0", "2026-11-02"),
                item("AR_RETURN", "客户A", "-150", "0", "2026-11-03"));

        List<Map<String, Object>> rows = service.aging(items, "AR", "2026-11-30");

        assertEquals(1, rows.size());
        assertEquals(0, new BigDecimal("100").compareTo((BigDecimal) rows.get(0).get("未结金额")),
                "应收 250 减退货 150 = 100");
    }
}
