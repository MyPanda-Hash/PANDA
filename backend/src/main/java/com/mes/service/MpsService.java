package com.mes.service;

import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * MPS 主生产计划：单厂按单生产，只认销售订单。
 * 计划行 = 已审核销售订单明细数量 - 已排产占用，得待排产数量。
 *
 * 与 MRP 的分工：MPS 决定"生产什么、多少、何时"，MRP 再按 BOM 展开算物料需求。
 */
@Service
public class MpsService {

    /** 一条主生产计划行：来自哪张销售订单、要生产什么、多少、何时要。 */
    public record PlanLine(String sourceType, String sourceNo, String customer,
                           String itemCode, String itemName, String spec, String unit,
                           BigDecimal planQty, String dueDate, String suggestedStart,
                           String priority) {}

    private static final DateTimeFormatter DATE = DateTimeFormatter.ofPattern("yyyy-MM-dd");
    /** 缺交货日期时的默认提前天数。 */
    private static final int DEFAULT_LEAD_DAYS = 10;
    /** 生产提前期（天）：建议开工日 = 交货日 - 提前期。 */
    private static final int PRODUCTION_LEAD_DAYS = 5;

    /**
     * MPS 目前是纯计算服务，不落库；参数保留用于后续接入 BOM 与库存。
     * 两个参数都有默认实现，Spring 可直接注入。
     */
    public MpsService(com.mes.mapper.FormDataMapper formMapper) {
        this.formMapper = formMapper;
    }

    private final com.mes.mapper.FormDataMapper formMapper;

    /** 由销售订单表头与明细生成计划行；已排产数量按存货编码扣减。 */
    public List<PlanLine> buildPlanLines(String sourceNo, Map<String, Object> head,
                                         Map<String, Object> detail) {
        return buildPlanLines(sourceNo, head, detail, Map.of());
    }

    /**
     * 由销售订单生成计划行。
     *
     * @param scheduled 已排产数量，键为存货编码
     */
    public List<PlanLine> buildPlanLines(String sourceNo, Map<String, Object> head,
                                         Map<String, Object> detail,
                                         Map<String, BigDecimal> scheduled) {
        List<PlanLine> out = new ArrayList<>();
        if (head == null) head = Map.of();
        String customer = text(first(head, "客户", "客户名称"));
        String dueDate = resolveDueDate(head);
        String start = suggestStart(dueDate, PRODUCTION_LEAD_DAYS);
        String priority = priorityOf(dueDate, LocalDate.now().format(DATE), PRODUCTION_LEAD_DAYS);

        for (Map<String, Object> row : allDetailRows(detail)) {
            String itemCode = text(first(row, "存货编码", "产品编码", "材料编码"));
            String itemName = text(first(row, "存货名称", "产品名称", "材料名称", "存货"));
            if (itemCode.isBlank() && itemName.isBlank()) continue;
            BigDecimal qty = number(first(row, "数量", "销售数量"));
            BigDecimal already = scheduled == null ? BigDecimal.ZERO
                    : scheduled.getOrDefault(itemCode, BigDecimal.ZERO);
            BigDecimal open = qty.subtract(already);
            if (open.signum() <= 0) continue;
            out.add(new PlanLine("销售订单", sourceNo, customer,
                    itemCode, itemName,
                    text(first(row, "规格型号")),
                    text(first(row, "销售单位", "计量单位", "主单位")),
                    open, dueDate, start, priority));
        }
        return out;
    }

    /** 建议开工日 = 交货日 - 提前期；缺交货日返回 null。 */
    public String suggestStart(String dueDate, int leadDays) {
        LocalDate due = parse(dueDate);
        if (due == null) return null;
        return due.minusDays(leadDays).format(DATE);
    }

    /**
     * 优先级按剩余天数判定：<=提前期特急，<=2倍提前期紧急，其余正常。
     * today 传字符串便于测试固定日期。
     */
    public String priorityOf(String dueDate, String today, int leadDays) {
        LocalDate due = parse(dueDate);
        LocalDate now = parse(today);
        if (due == null || now == null) return "正常";
        long days = ChronoUnit.DAYS.between(now, due);
        if (days <= leadDays) return "特急";
        if (days <= (long) leadDays * 2) return "紧急";
        return "正常";
    }

    // ---------- 取值助手 ----------

    private String resolveDueDate(Map<String, Object> head) {
        String explicit = text(first(head, "预计交货日期", "交货日期", "预完工日"));
        if (!explicit.isBlank()) return explicit;
        LocalDate orderDate = parse(text(first(head, "单据日期")));
        LocalDate base = orderDate == null ? LocalDate.now() : orderDate;
        return base.plusDays(DEFAULT_LEAD_DAYS).format(DATE);
    }

    private static LocalDate parse(String value) {
        if (value == null || value.isBlank()) return null;
        try {
            return LocalDate.parse(value.length() > 10 ? value.substring(0, 10) : value);
        } catch (Exception ignored) {
            return null;
        }
    }

    private static Object first(Map<String, Object> row, String... keys) {
        if (row == null) return null;
        for (String key : keys) {
            Object value = row.get(key);
            if (value != null && !String.valueOf(value).isBlank()) return value;
        }
        return null;
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> allDetailRows(Map<String, Object> detail) {
        List<Map<String, Object>> out = new ArrayList<>();
        if (detail == null) return out;
        for (Object value : detail.values()) {
            if (value instanceof List<?> list) {
                for (Object item : list) {
                    if (item instanceof Map<?, ?> map) out.add((Map<String, Object>) map);
                }
            }
        }
        return out;
    }

    private static String text(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }

    private static BigDecimal number(Object value) {
        if (value == null) return BigDecimal.ZERO;
        if (value instanceof BigDecimal decimal) return decimal;
        if (value instanceof Number num) return new BigDecimal(num.toString());
        try {
            return new BigDecimal(String.valueOf(value).trim());
        } catch (NumberFormatException ignored) {
            return BigDecimal.ZERO;
        }
    }
}
