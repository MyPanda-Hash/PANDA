package com.mes.service;

import java.util.List;
import java.util.Map;

/**
 * 销售订单排产规则（排产 → 生产加工单）。
 *
 * <pre>
 *   销售订单 SO-20261110-001  行1 CP005 ×10
 *        └─ 加工单 SO-20261110-001-1   CP005 ×10   工序 = 该产品工艺路线
 * </pre>
 *
 * 三条硬规则（均有单测守护）：
 * <ol>
 *   <li>一张加工单只装一个订单行，单号 = 销售订单号 + "-" + 行号，行号按明细次序从 1 起；</li>
 *   <li>可排数量 = 订单行数量 − 该行已生成加工单占用数量，可分批投产但不能超订单；</li>
 *   <li>老加工单没有行号字段时，按「销售订单号 + 产品编码」认占用，避免同一行被重复投产。</li>
 * </ol>
 *
 * 本类只放规则，不碰数据库：占用数量由调用方查出来传进来，便于单测。
 */
public final class SaleOrderPlanRules {

    /** 表头里记录订单行号的字段名 */
    public static final String LINE_FIELD = "销售订单行号";

    private SaleOrderPlanRules() {
    }

    /** 加工单号：销售订单号 + "-" + 行号 */
    public static String manuOrderNo(String saleOrderNo, int lineNo) {
        return String.valueOf(saleOrderNo).trim() + "-" + lineNo;
    }

    /** 明细下标（0 起）→ 行号（1 起） */
    public static int lineNoOf(int zeroBasedIndex) {
        return zeroBasedIndex + 1;
    }

    /** 可排数量：订单行数量减去已占用，不出现负数 */
    public static double plannable(double orderQty, double occupiedQty) {
        double left = orderQty - occupiedQty;
        return left <= 0 ? 0 : Math.round(left * 100) / 100.0;
    }

    /**
     * 排产进度（加工单「选销售订单」弹窗上显示）：未排产 / 部分排产 / 已排产。
     *
     * <p>只看数量不看单据状态：还可排数量为 0 才算排完；订单行数量为 0 的（既没排也没得排）
     * 不能显示成「已排产」，否则计划员会以为这行已经投过产。
     *
     * @param plannedQty 该订单各行已生成加工单占用的数量合计（按行封顶到订单数量）
     * @param plannableQty 该订单各行还可排数量合计
     */
    public static String planStatus(double plannedQty, double plannableQty) {
        if (plannableQty > 0) return plannedQty > 0 ? "部分排产" : "未排产";
        return plannedQty > 0 ? "已排产" : "未排产";
    }

    /** 投产数量必须大于 0 且不超过可排数量 */
    public static void requireWithin(double qty, double plannable, String product, double orderQty) {
        if (qty <= 0) {
            throw new IllegalArgumentException("生产数量必须大于 0");
        }
        if (qty > plannable + 1e-6) {
            throw new IllegalStateException("产品「" + product + "」本行可排产 " + trim(plannable)
                    + "，本次 " + trim(qty) + " 超过可排产量（订单数量 " + trim(orderQty)
                    + "，已排 " + trim(orderQty - plannable) + "）");
        }
    }

    /**
     * 这条加工单产品行应该占用销售订单的哪一行（返回明细下标，0 起；占不到返回 -1）。
     *
     * <p>这是排产看板与建加工单共用的占用归属规则，抽成纯函数以便单测覆盖。
     * 早先生产代码在 PxService 里内联了同一段判断，而本类里另有一个判定方法没人调用，
     * 形成「测试测的函数不干活、干活的函数没测试」的假安全感；现在只留这一份实现。
     *
     * <p>归属顺序：优先认表头写的行号（且产品要对得上）；行号缺失或对不上时，
     * 退回按产品编码找「还没排满」的行 —— 老单据没有行号，不这样做会把第二行卡死。
     *
     * @param lineField   加工单表头的「销售订单行号」原值，可为空
     * @param productCode 加工单产品编码
     * @param items       销售订单明细行（读存货编码、数量）
     * @param occupied    各订单行已占用数量，长度与 items 对齐
     */
    public static int targetLine(String lineField, String productCode,
                                 List<Map<String, Object>> items, double[] occupied) {
        if (items == null || items.isEmpty()) return -1;
        String code = productCode == null ? "" : productCode.trim();
        if (code.isEmpty()) return -1;

        String field = lineField == null ? "" : lineField.trim();
        if (!field.isEmpty()) {
            int lineNo = (int) Math.round(num(field));
            if (lineNo >= 1 && lineNo <= items.size()) {
                Map<String, Object> row = items.get(lineNo - 1);
                if (row != null && code.equals(text(row.get("存货编码")))) return lineNo - 1;
            }
        }

        for (int i = 0; i < items.size(); i++) {
            Map<String, Object> row = items.get(i);
            if (row == null || !code.equals(text(row.get("存货编码")))) continue;
            double order = num(row.get("数量"));
            double used = occupied != null && i < occupied.length ? occupied[i] : 0;
            // 已排满的行跳过，否则同一产品多行时永远只往第一行堆
            if (used + 1e-6 >= order) continue;
            return i;
        }
        return -1;
    }

    private static String text(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }

    private static double num(Object value) {
        if (value instanceof Number number) return number.doubleValue();
        if (value == null) return 0;
        try {
            return Double.parseDouble(String.valueOf(value).trim());
        } catch (NumberFormatException ignored) {
            return 0;
        }
    }

    private static String trim(double value) {
        return value == Math.rint(value) ? String.valueOf((long) value) : String.valueOf(value);
    }
}
