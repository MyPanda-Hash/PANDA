package com.mes.service;

import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;

/**
 * 拣货作业：把出库单明细转成拣货任务，按库位排序减少走位，记录实拣与差异。
 *
 * 分工：出库单是计划层（要出什么），拣货任务是执行层（谁去哪个库位拿多少）。
 */
@Service
public class PickTaskService {

    /** 一条拣货任务。 */
    public record PickTask(String sourceNo, String itemCode, String itemName,
                           String warehouse, String location,
                           BigDecimal planQty, BigDecimal pickedQty, String status) {
        /** 差异数量 = 计划 - 实拣，不为负。 */
        public BigDecimal shortageQty() {
            BigDecimal d = planQty.subtract(pickedQty);
            return d.signum() < 0 ? BigDecimal.ZERO : d;
        }
    }

    /** 拣货汇总：用于判断整单是否拣完。 */
    public record PickSummary(int total, int pickedCount, int partialCount, int pendingCount,
                              BigDecimal shortageTotal) {
        public boolean complete() {
            return pendingCount == 0 && partialCount == 0;
        }
    }

    public PickTaskService() {
    }

    /** 由出库单表头与明细生成拣货任务，按库位排序。 */
    public List<PickTask> buildTasks(String sourceNo, Map<String, Object> head,
                                     Map<String, Object> detail) {
        List<PickTask> tasks = new ArrayList<>();
        if (head == null) head = Map.of();
        String headWarehouse = text(first(head, "仓库"));

        for (Map<String, Object> row : allDetailRows(detail)) {
            String code = text(first(row, "存货编码", "产品编码", "材料编码"));
            String name = text(first(row, "存货名称", "产品名称", "材料名称", "存货"));
            if (code.isBlank() && name.isBlank()) continue;
            BigDecimal qty = number(first(row, "数量", "实收数量"));
            if (qty.signum() <= 0) continue;
            String warehouse = text(first(row, "仓库"));
            if (warehouse.isBlank()) warehouse = headWarehouse;
            tasks.add(new PickTask(sourceNo, code, name,
                    warehouse, text(first(row, "库位", "货位")),
                    qty, BigDecimal.ZERO, "待拣"));
        }
        // 按库位排序：库位为空的排最后，减少仓库内走位
        tasks.sort(Comparator
                .comparing((PickTask t) -> t.location().isBlank() ? "~" : t.location()));
        return tasks;
    }

    /**
     * 确认拣货：记录实拣数量并更新状态。
     * 实拣不得超过计划（超拣按计划计），负数按 0 计。
     */
    public PickTask confirm(PickTask task, BigDecimal actual) {
        BigDecimal picked = actual == null ? BigDecimal.ZERO : actual;
        if (picked.signum() < 0) picked = BigDecimal.ZERO;
        if (picked.compareTo(task.planQty()) > 0) picked = task.planQty();
        String status;
        if (picked.signum() == 0) status = "未拣";
        else if (picked.compareTo(task.planQty()) >= 0) status = "已拣货";
        else status = "部分拣货";
        return new PickTask(task.sourceNo(), task.itemCode(), task.itemName(),
                task.warehouse(), task.location(), task.planQty(), picked, status);
    }

    /** 汇总拣货进度：各状态条数与总缺口。 */
    public PickSummary summarize(List<PickTask> tasks) {
        if (tasks == null || tasks.isEmpty()) {
            return new PickSummary(0, 0, 0, 0, BigDecimal.ZERO);
        }
        int picked = 0;
        int partial = 0;
        int pending = 0;
        BigDecimal shortage = BigDecimal.ZERO;
        for (PickTask t : tasks) {
            switch (t.status()) {
                case "已拣货" -> picked++;
                case "部分拣货" -> {
                    partial++;
                    shortage = shortage.add(t.shortageQty());
                }
                default -> {
                    pending++;
                    shortage = shortage.add(t.shortageQty());
                }
            }
        }
        return new PickSummary(tasks.size(), picked, partial, pending, shortage);
    }

    // ---------- 取值助手 ----------

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
        if (value instanceof BigDecimal d) return d;
        if (value instanceof Number n) return new BigDecimal(n.toString());
        try {
            return new BigDecimal(String.valueOf(value).trim());
        } catch (NumberFormatException ignored) {
            return BigDecimal.ZERO;
        }
    }
}
