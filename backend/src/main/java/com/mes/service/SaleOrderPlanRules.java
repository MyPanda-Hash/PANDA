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
     * 这张加工单是否占用「销售订单某行」的额度。
     *
     * @param moHead     加工单表头
     * @param moProducts 加工单产品明细行
     * @param saleOrderNo 销售订单号
     * @param lineNo      订单行号
     * @param productCode 产品编码
     */
    public static boolean occupies(Map<String, Object> moHead, List<Map<String, Object>> moProducts,
                                  String saleOrderNo, int lineNo, String productCode) {
        if (moHead == null) return false;
        String so = String.valueOf(moHead.getOrDefault("销售订单号", "")).trim();
        if (so.isEmpty() || !so.equals(String.valueOf(saleOrderNo).trim())) return false;
        String moLine = String.valueOf(moHead.getOrDefault(LINE_FIELD, "")).trim();
        if (!moLine.isEmpty()) {
            return moLine.equals(String.valueOf(lineNo));
        }
        // 老加工单（无行号）按产品编码认占用
        if (moProducts == null) return false;
        for (Map<String, Object> p : moProducts) {
            if (p == null) continue;
            if (String.valueOf(p.getOrDefault("产品编码", "")).trim().equals(productCode)) return true;
        }
        return false;
    }

    private static String trim(double value) {
        return value == Math.rint(value) ? String.valueOf((long) value) : String.valueOf(value);
    }
}
