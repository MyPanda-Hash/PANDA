package com.mes.service;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;

/**
 * 委外入库成本 = 材料结存均价 × 计划用量 + 加工费（委外单价 × 数量）。
 * 只按委外单价计价会把成品价值算低：材料已经发出去，价值必须跟着成品回到库存。
 */
class PxServiceOutsourceCostTest {

    @Test
    void singleProductAddsMaterialCostToProcessingFee() {
        // 材料 6kg × 4.2 = 25.2（取整到 26 举例），加工费 10 件 × 6.5 = 65
        assertEquals(91.0, PxService.outsourceInboundAmount(26.0, 10, 10, 6.5), 0.001);
    }

    @Test
    void materialCostIsApportionedByQuantity() {
        // 两个产成品按数量 3:7 分摊 100 元材料成本
        assertEquals(30.0, PxService.apportionOutsourceMaterialCost(100.0, 3, 10), 0.001);
        assertEquals(70.0, PxService.apportionOutsourceMaterialCost(100.0, 7, 10), 0.001);
        // 分摊后合计仍等于材料成本总额
        assertEquals(100.0,
                PxService.apportionOutsourceMaterialCost(100.0, 3, 10)
                        + PxService.apportionOutsourceMaterialCost(100.0, 7, 10), 0.001);
    }

    @Test
    void noMaterialCostKeepsProcessingFee() {
        assertEquals(65.0, PxService.outsourceInboundAmount(0, 10, 10, 6.5), 0.001);
    }

    @Test
    void zeroQuantityDoesNotExplode() {
        assertEquals(0.0, PxService.apportionOutsourceMaterialCost(50, 0, 0), 0.001);
        assertEquals(0.0, PxService.outsourceInboundAmount(50, 0, 0, 6.5), 0.001);
    }

    @Test
    void apportionRoundsToCent() {
        assertEquals(3.33, PxService.apportionOutsourceMaterialCost(10, 1, 3), 0.001);
    }
}
