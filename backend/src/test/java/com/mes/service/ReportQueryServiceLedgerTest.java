package com.mes.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;

/**
 * 库存台账：结存必须按「存货 + 仓库」分别滚动。
 *
 * <p>修复前整张台账共用一个余额：期初 45#圆钢 5000 的下一行（6061铝锭 3000）
 * 结存直接变成 8000，任何一行都看不出这种存货还剩多少。
 */
class ReportQueryServiceLedgerTest {

    private static Map<String, Object> mv(String dir, String date, String warehouse, String item,
                                          double qty, double amount) {
        return mv(dir, date, warehouse, item, qty, amount, 0, "");
    }

    private static Map<String, Object> mv(String dir, String date, String warehouse, String item,
                                          double qty, double amount, long seq) {
        return mv(dir, date, warehouse, item, qty, amount, seq, "");
    }

    private static Map<String, Object> mv(String dir, String date, String warehouse, String item,
                                          double qty, double amount, long seq, String time) {
        Map<String, Object> row = new java.util.LinkedHashMap<>();
        row.put("方向", dir);
        row.put("单据日期", date);
        row.put("单据类型", dir.equals("OUT") ? "销售出库单" : "产成品入库单");
        row.put("单据编号", dir + "-" + date + "-" + item);
        row.put("业务类型", "");
        row.put("往来单位", "");
        row.put("项目", "");
        row.put("仓库", warehouse);
        row.put("存货编码", item);
        row.put("存货", item);
        row.put("数量", new BigDecimal(String.valueOf(qty)));
        row.put("金额", new BigDecimal(String.valueOf(amount)));
        row.put("时间序", seq);
        row.put("单据时间", time);
        return row;
    }

    @Test
    void balanceRollsPerItemInsteadOfOneGlobalRunningTotal() {
        ReportQueryService service = new ReportQueryService(null, new com.fasterxml.jackson.databind.ObjectMapper());
        List<Map<String, Object>> movements = List.of(
                mv("IN", "2026-11-06", "原料仓", "45#圆钢", 5000, 32500),
                mv("IN", "2026-11-06", "原料仓", "6061铝锭", 3000, 54000),
                mv("OUT", "2026-11-06", "原料仓", "45#圆钢", 882, 5733),
                mv("IN", "2026-11-06", "成品仓", "减速箱体 A", 10, 0),
                mv("IN", "2026-11-06", "成品仓", "减速箱体 A", 20, 0),
                mv("OUT", "2026-11-06", "成品仓", "减速箱体 A", 30, 0));

        List<Map<String, Object>> rows = service.ledgerRows(movements, Map.of());

        Map<String, List<Map<String, Object>>> byItem = new java.util.LinkedHashMap<>();
        for (Map<String, Object> row : rows) {
            byItem.computeIfAbsent(String.valueOf(row.get("存货")), k -> new java.util.ArrayList<>()).add(row);
        }
        // 圆钢：5000 → 4118，不能把铝锭的 3000 累进来
        List<Map<String, Object>> steel = byItem.get("45#圆钢");
        assertEquals(0, new BigDecimal("5000").compareTo((BigDecimal) steel.get(0).get("结存数量")));
        assertEquals(0, new BigDecimal("4118").compareTo((BigDecimal) steel.get(1).get("结存数量")));
        // 铝锭只有一条入库，结存就是它自己的 3000
        assertEquals(0, new BigDecimal("3000").compareTo((BigDecimal) byItem.get("6061铝锭").get(0).get("结存数量")));
        // 成品：10 → 30 → 0（入 30 出 30）
        List<Map<String, Object>> finished = byItem.get("减速箱体 A");
        assertEquals(0, new BigDecimal("10").compareTo((BigDecimal) finished.get(0).get("结存数量")));
        assertEquals(0, new BigDecimal("30").compareTo((BigDecimal) finished.get(1).get("结存数量")));
        assertEquals(0, BigDecimal.ZERO.compareTo((BigDecimal) finished.get(2).get("结存数量")));
    }

    /** 同一天按单据创建先后排，不能把当天的入库都堆到出库前面。 */
    @Test
    void sameDayRowsFollowDocumentOrder() {
        ReportQueryService service = new ReportQueryService(null, new com.fasterxml.jackson.databind.ObjectMapper());
        List<Map<String, Object>> movements = List.of(
                mv("IN", "2026-11-06", "成品仓", "减速箱体 A", 10, 2044, 11),
                mv("OUT", "2026-11-06", "成品仓", "减速箱体 A", 30, 6132, 12),
                mv("IN", "2026-11-06", "成品仓", "减速箱体 A", 20, 4088, 13));

        List<Map<String, Object>> rows = service.ledgerRows(movements, Map.of());

        assertEquals(List.of("10", "-20", "0"),
                List.of(((BigDecimal) rows.get(0).get("结存数量")).stripTrailingZeros().toPlainString(),
                        ((BigDecimal) rows.get(1).get("结存数量")).stripTrailingZeros().toPlainString(),
                        ((BigDecimal) rows.get(2).get("结存数量")).stripTrailingZeros().toPlainString()),
                "结存应为 10 → -20（当天超发）→ 0，说明按单据先后而不是按类型排");
    }

    /** 同一天按单据时分排：上午出库、下午入库，结存先欠后补。 */
    @Test
    void sameDayRowsFollowClockTime() {
        ReportQueryService service = new ReportQueryService(null, new com.fasterxml.jackson.databind.ObjectMapper());
        List<Map<String, Object>> movements = List.of(
                mv("IN", "2026-11-06", "成品仓", "减速箱体 A", 2, 408.8, 30, "15:40"),
                mv("OUT", "2026-11-06", "成品仓", "减速箱体 A", 4, 817.6, 10, "09:15"));

        List<Map<String, Object>> rows = service.ledgerRows(movements, Map.of());

        assertEquals("09:15", rows.get(0).get("单据时间"), "按时分排，09:15 的出货在 15:40 的入库之前");
        assertEquals("-4", ((BigDecimal) rows.get(0).get("结存数量")).stripTrailingZeros().toPlainString());
        assertEquals("-2", ((BigDecimal) rows.get(1).get("结存数量")).stripTrailingZeros().toPlainString());
    }
}