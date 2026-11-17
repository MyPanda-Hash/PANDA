package com.mes.service;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;

/**
 * 加工单「选销售订单」弹窗上的排产进度标记。
 *
 * <p>弹窗要回答一句话：这张订单**还排不排得动**。所以状态只看两个数——
 * 已排数量与还可排数量，不看单据状态（已审核是候选项的入场券）。
 */
class SaleOrderPlanRulesStatusTest {

    @Test
    void neverPlannedIs未排产() {
        assertEquals("未排产", SaleOrderPlanRules.planStatus(0, 10));
    }

    @Test
    void partlyPlannedIs部分排产() {
        assertEquals("部分排产", SaleOrderPlanRules.planStatus(4, 6));
    }

    @Test
    void fullyPlannedIs已排产() {
        assertEquals("已排产", SaleOrderPlanRules.planStatus(10, 0));
        // 超排（历史数据或手工单）也算排完，不能显示成「还能排」
        assertEquals("已排产", SaleOrderPlanRules.planStatus(12, 0));
    }

    @Test
    void zeroQuantityLineIsNotMarkedAsPlanned() {
        // 订单行数量为 0 时既没排也没得排，不能显示成「已排产」误导计划员
        assertEquals("未排产", SaleOrderPlanRules.planStatus(0, 0));
    }
}
