package com.mes.service;

import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 对账与账龄：SETTLE_CUSTOMER / SETTLE_SUPPLIER / AGING_ANALYSIS 三张报表的数据源。
 *
 * 此前这三个面板只有 panel_config 定义，查询恒返回 0 行。口径严格对齐面板列：
 * - 对账单（客户/供应商）：逐张单据一行，列 业务日期/往来单位/来源单据/来源单号/
 *   金额/已核销金额/未核销金额/结算状态
 * - 账龄分析表：按往来单位汇总，列 往来类型/往来单位/未结金额/未到期/30天内/
 *   31-60天/61-90天/90天以上
 *
 * 未核销金额 = 单据金额 - 已核销金额。往来方为空的归入"未指定"，
 * 避免因表头漏填供应商导致金额在报表里凭空消失。
 *
 * 纯函数实现，便于单测与报表复用。
 */
@Service
public class FinanceStatementService {

    private static final BigDecimal ZERO = BigDecimal.ZERO;
    private static final String UNSPECIFIED = "未指定";

    /**
     * 一条往来记录。
     *
     * @param type        AR/AP/AR_RETURN/AP_RETURN
     * @param partner     往来单位名称（可能为空）
     * @param amount      单据金额
     * @param settled     已核销金额
     * @param bizDate     业务日期 yyyy-MM-dd
     * @param sourcePanel 来源面板编码
     * @param sourceNo    来源单号
     */
    public record OpenItem(String type, String partner, BigDecimal amount,
                           BigDecimal settled, String bizDate,
                           String sourcePanel, String sourceNo) {

        /** 兼容无来源信息的调用方。 */
        public OpenItem(String type, String partner, BigDecimal amount,
                        BigDecimal settled, String bizDate) {
            this(type, partner, amount, settled, bizDate, "", "");
        }

        BigDecimal open() {
            return nz(amount).subtract(nz(settled));
        }
    }

    /**
     * 对账单明细：逐张单据一行，只保留未结清的（已结清的不再出现在对账清单里）。
     * type 传 AR 得到客户对账单，传 AP 得到供应商对账单。
     */
    public List<Map<String, Object>> statement(List<OpenItem> items, String type) {
        List<Map<String, Object>> out = new ArrayList<>();
        for (OpenItem item : items) {
            if (!sameSide(item.type(), type)) continue;
            BigDecimal open = item.open();
            // 未核销为 0 才是「已结清」，不进来的；负数表示退货/红字信用，要留在对账单上冲减，
            // 否则客户退了货，对账单上反而看不到可抵扣的那笔钱。
            if (open.signum() == 0) continue;
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("业务日期", text(item.bizDate()));
            row.put("往来单位", nameOf(item.partner()));
            row.put("来源单据", panelName(item.sourcePanel()));
            row.put("来源单号", text(item.sourceNo()));
            row.put("金额", nz(item.amount()));
            row.put("已核销金额", nz(item.settled()));
            row.put("未核销金额", open);
            row.put("结算状态", open.compareTo(nz(item.amount())) == 0 ? "未结算" : "部分结算");
            out.add(row);
        }
        // 业务日期早的在前，便于按时间对账
        out.sort(Comparator.comparing(a -> String.valueOf(a.get("业务日期"))));
        return out;
    }

    /**
     * 账龄分布：按往来单位汇总未结金额并分档。
     * type 传 null 表示应收应付合并（AGING_ANALYSIS 面板用「往来类型」列区分）。
     *
     * @param baseDate 截止日期 yyyy-MM-dd；空则取当天
     */
    public List<Map<String, Object>> aging(List<OpenItem> items, String type, String baseDate) {
        LocalDate base = parseDate(baseDate);
        if (base == null) base = LocalDate.now();

        // key = 往来类型|往来单位，保证应收应付同名单位不混行
        Map<String, Bucket> buckets = new LinkedHashMap<>();
        for (OpenItem item : items) {
            if (type != null && !sameSide(item.type(), type)) continue;
            BigDecimal open = item.open();
            // 负数 = 退货/红字信用，要参与冲减；只跳过真正结清的（open = 0）
            if (open.signum() == 0) continue;
            String side = sideOf(item.type());
            String partner = nameOf(item.partner());
            Bucket bucket = buckets.computeIfAbsent(side + "|" + partner, k -> new Bucket(side, partner));
            int days = overdueDays(item.bizDate(), base);
            bucket.add(open, days);
        }

        List<Map<String, Object>> out = new ArrayList<>();
        for (Bucket bucket : buckets.values()) out.add(bucket.toRow());
        out.sort((a, b) -> ((BigDecimal) b.get("未结金额")).compareTo((BigDecimal) a.get("未结金额")));
        return out;
    }

    /** 未结金额分档容器。 */
    private static final class Bucket {
        private final String side;
        private final String partner;
        private BigDecimal notDue = ZERO;
        private BigDecimal within30 = ZERO;
        private BigDecimal days31to60 = ZERO;
        private BigDecimal days61to90 = ZERO;
        private BigDecimal over90 = ZERO;
        private int maxOverdue = 0;

        Bucket(String side, String partner) {
            this.side = side;
            this.partner = partner;
        }

        void add(BigDecimal amount, int days) {
            if (days <= 0) notDue = notDue.add(amount);
            else if (days <= 30) within30 = within30.add(amount);
            else if (days <= 60) days31to60 = days31to60.add(amount);
            else if (days <= 90) days61to90 = days61to90.add(amount);
            else over90 = over90.add(amount);
            maxOverdue = Math.max(maxOverdue, days);
        }

        Map<String, Object> toRow() {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("往来类型", side);
            row.put("往来单位", partner);
            row.put("未结金额", notDue.add(within30).add(days31to60).add(days61to90).add(over90));
            row.put("未到期", notDue);
            row.put("30天内", within30);
            row.put("31-60天", days31to60);
            row.put("61-90天", days61to90);
            row.put("90天以上", over90);
            row.put("最长逾期天数", maxOverdue);
            return row;
        }
    }

    /** 应收侧（AR/AR_RETURN）→ 应收；应付侧 → 应付。 */
    private static String sideOf(String type) {
        String t = type == null ? "" : type.trim().toUpperCase();
        return t.startsWith("AP") ? "应付" : "应收";
    }

    /** AR 侧含 AR_RETURN，AP 侧含 AP_RETURN；用前缀判断避免精确匹配漏项。 */
    private static boolean sameSide(String itemType, String queryType) {
        String a = itemType == null ? "" : itemType.trim().toUpperCase();
        String b = queryType == null ? "" : queryType.trim().toUpperCase();
        if (a.isEmpty() || b.isEmpty()) return false;
        return a.equals(b) || a.startsWith(b + "_");
    }

    /** 逾期天数：业务日期到截止日的天数，未到期记 0。 */
    private static int overdueDays(String bizDate, LocalDate base) {
        LocalDate date = parseDate(bizDate);
        if (date == null) return 0;
        long days = ChronoUnit.DAYS.between(date, base);
        return (int) Math.max(0, days);
    }

    private static LocalDate parseDate(String value) {
        if (value == null || value.isBlank()) return null;
        String t = value.trim();
        try {
            return LocalDate.parse(t.length() > 10 ? t.substring(0, 10) : t);
        } catch (Exception ignored) {
            return null;
        }
    }

    private static String nameOf(String partner) {
        String name = partner == null ? "" : partner.trim();
        return name.isEmpty() ? UNSPECIFIED : name;
    }

    /** 来源面板编码 → 中文单据名，对账单「来源单据」列直接展示。 */
    private static String panelName(String panelCode) {
        String code = panelCode == null ? "" : panelCode.trim();
        return switch (code) {
            case "PURCHASE_IN" -> "采购入库单";
            case "SALE_OUT" -> "销售出库单";
            case "PURCHASE_RETURN" -> "采购退货单";
            case "SALE_RETURN" -> "销售退货单";
            case "PU_INVOICE" -> "采购发票";
            case "SALE_INVOICE" -> "销售发票";
            default -> code;
        };
    }

    private static String text(String value) {
        return value == null ? "" : value.trim();
    }

    private static BigDecimal nz(BigDecimal value) {
        return value == null ? ZERO : value;
    }
}
