package com.mes.service;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 按期率：管理者的两个核心指标。
 *
 * <p>为什么单独一个服务：这两个数不是「某张表的行数」，而是「实际日期 vs 承诺日期」的比对，
 * 只能在对数据最熟的 MES 侧用 SQL 算，避免 AI 侧拉全表再拼。
 *
 * <p>两个口径（使用者明确要求都要）：
 * <ul>
 *   <li><b>订单按期交付率</b>：销售订单首次销货日期 ≤ 预计交货日期的比例。回答「对客户的承诺守没守住」。</li>
 *   <li><b>工单按期完工率</b>：加工单完工日期 ≤ 预完工日的比例。回答「车间自己的计划准不准」。</li>
 * </ul>
 * 分母只算「已经有结果」的单据（已交付/已完工），没到期的单不进分母，否则月初必然是 0%。
 */
@Service
public class OnTimeService {

    private static final DateTimeFormatter MONTH = DateTimeFormatter.ofPattern("yyyy-MM");

    private final JdbcTemplate jdbc;

    public OnTimeService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    /**
     * 两个按期率。
     *
     * @param period 会计期间口径的月份 yyyy-MM；为空表示不限月份（全部历史）
     */
    public Map<String, Object> onTime(String period) {
        String month = (period == null || period.isBlank()) ? null : period.trim();
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("period", month == null ? "全部" : month);
        out.put("order", orderOnTime(month));
        out.put("manuOrder", manuOrderOnTime(month));
        return out;
    }

    /** 订单按期交付率：首次销货日期 ≤ 预计交货日期。 */
    private Map<String, Object> orderOnTime(String month) {
        String monthFilter = month == null ? "" : " AND to_char(s.ship_date, 'YYYY-MM') = ? ";
        Object[] args = month == null ? new Object[]{} : new Object[]{month};
        List<Map<String, Object>> rows = jdbc.queryForList(
                "WITH shipped AS ( "
                        + "  SELECT nullif(data::jsonb->>'销售订单号','') AS so, "
                        + "         min(nullif(data::jsonb->>'单据日期','')::date) AS ship_date "
                        + "  FROM form_data WHERE panel_code = 'SALE_INV' "
                        + "    AND coalesce(data::jsonb->>'销售订单号','') <> '' "
                        + "    AND status IN ('已审核','已关闭') "
                        + "  GROUP BY 1 "
                        + "), orders AS ( "
                        + "  SELECT form_no, nullif(data::jsonb->>'预计交货日期','')::date AS due "
                        + "  FROM form_data WHERE panel_code = 'SO_ORDER' "
                        + "    AND coalesce(data::jsonb->>'预计交货日期','') <> '' "
                        + ") "
                        + "SELECT count(*) AS total, "
                        + "       count(*) FILTER (WHERE s.ship_date <= o.due) AS on_time "
                        + "FROM orders o JOIN shipped s ON s.so = o.form_no "
                        + "WHERE o.due IS NOT NULL AND s.ship_date IS NOT NULL " + monthFilter,
                args);
        return summarize(rows, "订单按期交付率");
    }

    /** 工单按期完工率：完工日期 ≤ 预完工日。 */
    private Map<String, Object> manuOrderOnTime(String month) {
        String monthFilter = month == null ? "" : " AND to_char(t.finish_date, 'YYYY-MM') = ? ";
        Object[] args = month == null ? new Object[]{} : new Object[]{month};
        List<Map<String, Object>> rows = jdbc.queryForList(
                "WITH t AS ( "
                        + "  SELECT nullif(data::jsonb->>'完工日期','')::date AS finish_date, "
                        + "         nullif(data::jsonb->>'预完工日','')::date AS due_date "
                        + "  FROM form_data WHERE panel_code = 'MANU_ORDER' "
                        + "    AND coalesce(data::jsonb->>'完工日期','') <> '' "
                        + "    AND coalesce(data::jsonb->>'预完工日','') <> '' "
                        + ") "
                        + "SELECT count(*) AS total, "
                        + "       count(*) FILTER (WHERE t.finish_date <= t.due_date) AS on_time "
                        + "FROM t WHERE t.finish_date IS NOT NULL AND t.due_date IS NOT NULL " + monthFilter,
                args);
        return summarize(rows, "工单按期完工率");
    }

    private Map<String, Object> summarize(List<Map<String, Object>> rows, String label) {
        long total = 0;
        long onTime = 0;
        if (!rows.isEmpty()) {
            Object t = rows.get(0).get("total");
            Object o = rows.get(0).get("on_time");
            total = t == null ? 0 : ((Number) t).longValue();
            onTime = o == null ? 0 : ((Number) o).longValue();
        }
        BigDecimal rate = total == 0
                ? null
                : BigDecimal.valueOf(onTime * 100.0 / total).setScale(1, RoundingMode.HALF_UP);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("label", label);
        out.put("total", total);
        out.put("onTime", onTime);
        // 没有样本时返回 null 而不是 0：0% 会被误读成「全都不按期」
        out.put("rate", rate);
        out.put("hasData", total > 0);
        return out;
    }

    /** 当月字符串，供报表默认值。 */
    public String currentMonth() {
        return LocalDate.now().format(MONTH);
    }
}
