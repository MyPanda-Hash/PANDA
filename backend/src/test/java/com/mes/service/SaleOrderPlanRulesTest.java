package com.mes.service;

import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 销售订单排产规则：一张加工单只装一个订单行，单号带行号，可排数量 = 订单行数量 − 已占用。
 */
class SaleOrderPlanRulesTest {

    @Test
    void 加工单号等于销售订单号加行号() {
        assertEquals("SO-20261110-001-1", SaleOrderPlanRules.manuOrderNo("SO-20261110-001", 1));
        assertEquals("SO-20261110-001-12", SaleOrderPlanRules.manuOrderNo("SO-20261110-001", 12));
    }

    @Test
    void 行号从1开始按明细次序编号() {
        assertEquals(1, SaleOrderPlanRules.lineNoOf(0));
        assertEquals(3, SaleOrderPlanRules.lineNoOf(2));
    }

    @Test
    void 可排数量是订单行数量减去已占用() {
        assertEquals(10.0, SaleOrderPlanRules.plannable(10, 0));
        assertEquals(6.0, SaleOrderPlanRules.plannable(10, 4));
        assertEquals(0.0, SaleOrderPlanRules.plannable(10, 10));
    }

    @Test
    void 已占用超过订单数量时可排为0而不是负数() {
        assertEquals(0.0, SaleOrderPlanRules.plannable(10, 12));
    }

    @Test
    void 超订单行数量投产要报错并说清上限() {
        IllegalStateException e = assertThrows(IllegalStateException.class,
                () -> SaleOrderPlanRules.requireWithin(11, 10, "CP005 轴套 C", 10));
        assertTrue(e.getMessage().contains("CP005 轴套 C"));
        assertTrue(e.getMessage().contains("10"));
    }

    @Test
    void 数量为零或负数直接被拒() {
        assertThrows(IllegalArgumentException.class,
                () -> SaleOrderPlanRules.requireWithin(0, 10, "CP005", 10));
    }

    private static List<Map<String, Object>> orderItems() {
        return List.of(
                Map.of("存货编码", "CP005", "数量", 10),
                Map.of("存货编码", "CP005", "数量", 20),
                Map.of("存货编码", "CP003", "数量", 5));
    }

    @Test
    void 有行号且产品对得上就占该行() {
        assertEquals(1, SaleOrderPlanRules.targetLine("2", "CP005", orderItems(), new double[3]));
        assertEquals(2, SaleOrderPlanRules.targetLine("3", "CP003", orderItems(), new double[3]));
    }

    @Test
    void 行号产品对不上时退回按编码找() {
        // 表头写行 3，但产品是 CP005，与第 3 行的 CP003 不符 → 退回按编码匹配
        assertEquals(0, SaleOrderPlanRules.targetLine("3", "CP005", orderItems(), new double[3]));
    }

    @Test
    void 老加工单没有行号时按编码占未排满的行() {
        double[] occupied = new double[3];
        assertEquals(0, SaleOrderPlanRules.targetLine("", "CP005", orderItems(), occupied), "第一行还有余量就占第一行");
        occupied[0] = 10;
        assertEquals(1, SaleOrderPlanRules.targetLine(null, "CP005", orderItems(), occupied), "第一行排满后轮到第二行");
        occupied[1] = 20;
        assertEquals(-1, SaleOrderPlanRules.targetLine("", "CP005", orderItems(), occupied), "两行都排满就无处可占");
    }

    @Test
    void 产品不在订单里或明细为空时不占行() {
        assertEquals(-1, SaleOrderPlanRules.targetLine("", "CP999", orderItems(), new double[3]));
        assertEquals(-1, SaleOrderPlanRules.targetLine("1", "CP005", List.of(), new double[0]));
        assertEquals(-1, SaleOrderPlanRules.targetLine("1", "", orderItems(), new double[3]));
    }
}
