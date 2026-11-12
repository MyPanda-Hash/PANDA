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

    @Test
    void 同一订单行已生成的加工单占用该行额度() {
        Map<String, Object> head = Map.of("销售订单号", "SO-1", "销售订单行号", 2);
        assertTrue(SaleOrderPlanRules.occupies(head, List.of(Map.of("产品编码", "CP005")), "SO-1", 2, "CP005"));
        assertFalse(SaleOrderPlanRules.occupies(head, List.of(Map.of("产品编码", "CP005")), "SO-1", 3, "CP005"));
        assertFalse(SaleOrderPlanRules.occupies(head, List.of(Map.of("产品编码", "CP005")), "SO-2", 2, "CP005"));
    }

    @Test
    void 老加工单没有行号时按产品编码认占用() {
        Map<String, Object> legacy = Map.of("销售订单号", "SO-1");
        assertTrue(SaleOrderPlanRules.occupies(legacy, List.of(Map.of("产品编码", "CP005")), "SO-1", 1, "CP005"));
        assertFalse(SaleOrderPlanRules.occupies(legacy, List.of(Map.of("产品编码", "CP002")), "SO-1", 1, "CP005"));
    }

    @Test
    void 销售订单号为空时不认占用() {
        assertFalse(SaleOrderPlanRules.occupies(Map.of(), List.of(Map.of("产品编码", "CP005")), "SO-1", 1, "CP005"));
    }
}
