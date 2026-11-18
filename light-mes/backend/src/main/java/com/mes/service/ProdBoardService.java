package com.mes.service;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 车间大屏：一屏看完当前生产要素。
 *
 * <p>车间大屏与「我的桌面」不是一回事：桌面是坐在电脑前逐项点开的，
 * 大屏是站着十几米外看的——所以要少、要大、要能一眼判断「哪张单掉了队」。
 *
 * <p>口径说明：
 * <ul>
 *   <li>在制 = 单据状态为「已审核 / 生产中」的加工单；</li>
 *   <li>进度按产品行的「累计汇报套数 ÷ 数量」，没有汇报记录就是 0；</li>
 *   <li>逾期 = 预完工日 &lt; 今天且仍在制；</li>
 *   <li>上报工只看今天，按单据日期严格当天。</li>
 * </ul>
 */
@Service
public class ProdBoardService {

    private static final int MAX_ORDERS = 8;
    private static final int MAX_REPORTS = 12;

    private final JdbcTemplate jdbc;

    public ProdBoardService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public Map<String, Object> board() {
        LocalDate today = LocalDate.now();
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("date", today.toString());
        out.put("updatedAt", java.time.LocalTime.now().withNano(0).toString());

        List<Map<String, Object>> orders = inProgressOrders();
        List<Map<String, Object>> overdue = new ArrayList<>();
        List<Map<String, Object>> stalled = new ArrayList<>();
        double planTotal = 0;
        double doneTotal = 0;
        Map<String, Map<String, Object>> byWorkshop = new LinkedHashMap<>();

        for (Map<String, Object> o : orders) {
            // 注意键名：这里是上面组装好的驼峰 key，不是 SQL 列名（写错过一次，合计全为 0）
            double plan = num(o.get("planQty"));
            double done = num(o.get("doneQty"));
            planTotal += plan;
            doneTotal += done;
            long overdueDays = ((Number) o.getOrDefault("overdue_days", 0)).longValue();
            if (overdueDays > 0) {
                overdue.add(o);
            }
            long idleDays = ((Number) o.getOrDefault("idle_days", 0)).longValue();
            if (done <= 0 && idleDays >= 2) {
                stalled.add(o);
            }
            String ws = String.valueOf(o.getOrDefault("workshop", "")).trim();
            if (ws.isEmpty()) {
                ws = "未指定";
            }
            Map<String, Object> agg = byWorkshop.computeIfAbsent(ws, k -> {
                Map<String, Object> m = new LinkedHashMap<>();
                m.put("workshop", k);
                m.put("orders", 0);
                m.put("plan", 0.0);
                m.put("done", 0.0);
                return m;
            });
            agg.put("orders", ((Number) agg.get("orders")).intValue() + 1);
            agg.put("plan", round(num(agg.get("plan")) + plan));
            agg.put("done", round(num(agg.get("done")) + done));
        }

        int reportedToday = countReportsToday(today);
        double reportedQtyToday = qtyReportsToday(today);
        BigDecimal avgProgress = planTotal <= 0
                ? BigDecimal.ZERO
                : BigDecimal.valueOf(doneTotal * 100 / planTotal).setScale(1, RoundingMode.HALF_UP);

        Map<String, Object> kpis = new LinkedHashMap<>();
        kpis.put("在制工单", orders.size());
        kpis.put("计划数量", round(planTotal));
        kpis.put("已汇报数量", round(doneTotal));
        kpis.put("平均进度", avgProgress);
        kpis.put("逾期工单", overdue.size());
        kpis.put("今日报工", reportedToday);
        kpis.put("今日报工数量", round(reportedQtyToday));
        kpis.put("停滞工单", stalled.size());
        out.put("kpis", kpis);

        out.put("orders", orders.stream().limit(MAX_ORDERS).toList());
        out.put("workshops", new ArrayList<>(byWorkshop.values()));
        out.put("overdue", overdue.stream().limit(MAX_ORDERS).toList());
        out.put("stalled", stalled.stream().limit(MAX_ORDERS).toList());
        out.put("reports", recentReports(MAX_REPORTS, today));
        return out;
    }

    /** 在制加工单 + 进度（明细里取产品行，与一线看板口径一致）。 */
    private List<Map<String, Object>> inProgressOrders() {
        List<Map<String, Object>> rows = jdbc.queryForList(
                "SELECT form_no, "
                        + "       data::jsonb->>'生产车间' AS workshop, "
                        + "       data::jsonb->>'预完工日' AS due_date, "
                        + "       data::jsonb->>'开工日期' AS start_date, "
                        + "       detail_data::jsonb->'products'->0->>'产品名称' AS product, "
                        + "       detail_data::jsonb->'products'->0->>'数量' AS plan_qty, "
                        + "       detail_data::jsonb->'products'->0->>'累计汇报套数(工序单位)' AS done_qty "
                        + "FROM form_data "
                        + "WHERE panel_code = 'MANU_ORDER' AND status IN ('已审核','生产中') "
                        + "ORDER BY data::jsonb->>'预完工日' NULLS LAST, form_no");
        LocalDate today = LocalDate.now();
        List<Map<String, Object>> out = new ArrayList<>();
        for (Map<String, Object> r : rows) {
            double plan = num(r.get("plan_qty"));
            double done = num(r.get("done_qty"));
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("docNo", str(r.get("form_no")));
            item.put("product", str(r.get("product")));
            item.put("workshop", str(r.get("workshop")));
            item.put("planQty", round(plan));
            item.put("doneQty", round(done));
            item.put("progress", plan <= 0 ? 0.0 : round(done * 100 / plan));
            item.put("dueDate", str(r.get("due_date")));
            item.put("overdue_days", daysBetween(str(r.get("due_date")), today));
            item.put("idle_days", daysBetween(str(r.get("start_date")), today));
            out.add(item);
        }
        // 进度低的排前面：大屏最该先看到没动的
        out.sort((a, b) -> Double.compare(num(a.get("progress")), num(b.get("progress"))));
        return out;
    }

    private int countReportsToday(LocalDate day) {
        Integer n = jdbc.queryForObject(
                "SELECT count(*) FROM form_data WHERE panel_code = 'PROCESS_REPORT' "
                        + "AND data::jsonb->>'单据日期' = ?", Integer.class, day.toString());
        return n == null ? 0 : n;
    }

    private double qtyReportsToday(LocalDate day) {
        Double q = jdbc.queryForObject(
                "SELECT coalesce(sum((it->>'报工数量')::numeric), 0) "
                        + "FROM form_data f, jsonb_array_elements(coalesce(f.detail_data::jsonb->'items','[]'::jsonb)) it "
                        + "WHERE f.panel_code = 'PROCESS_REPORT' AND f.data::jsonb->>'单据日期' = ?",
                Double.class, day.toString());
        return q == null ? 0 : q;
    }

    /** 最新报工：大屏底部滚动条，让人看到「产线还在动」。 */
    private List<Map<String, Object>> recentReports(int limit, LocalDate today) {
        List<Map<String, Object>> rows = jdbc.queryForList(
                "SELECT f.form_no, f.data::jsonb->>'单据日期' AS day, "
                        + "       it->>'工序名称' AS process, it->>'报工数量' AS qty, "
                        + "       it->>'工人名称' AS worker, it->>'加工单号' AS mo "
                        + "FROM form_data f, jsonb_array_elements(coalesce(f.detail_data::jsonb->'items','[]'::jsonb)) it "
                        + "WHERE f.panel_code = 'PROCESS_REPORT' "
                        + "ORDER BY f.data::jsonb->>'单据日期' DESC, f.form_no DESC LIMIT ?", limit);
        List<Map<String, Object>> out = new ArrayList<>();
        for (Map<String, Object> r : rows) {
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("docNo", str(r.get("form_no")));
            item.put("day", str(r.get("day")));
            item.put("process", str(r.get("process")));
            item.put("qty", round(num(r.get("qty"))));
            item.put("worker", str(r.get("worker")));
            item.put("mo", str(r.get("mo")));
            out.add(item);
        }
        return out;
    }

    private static long daysBetween(String date, LocalDate today) {
        if (date == null || date.isBlank() || "null".equals(date)) {
            return 0;
        }
        try {
            LocalDate d = LocalDate.parse(date.substring(0, Math.min(10, date.length())));
            return d.isBefore(today) ? today.toEpochDay() - d.toEpochDay() : 0;
        } catch (Exception e) {
            return 0;
        }
    }

    private static double num(Object v) {
        if (v == null) return 0;
        try {
            return Double.parseDouble(String.valueOf(v).replace(",", "").trim());
        } catch (NumberFormatException e) {
            return 0;
        }
    }

    private static double round(double v) {
        return BigDecimal.valueOf(v).setScale(2, RoundingMode.HALF_UP).doubleValue();
    }

    private static String str(Object v) {
        return v == null ? "" : String.valueOf(v);
    }
}
