package com.mes.service;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;

/**
 * 发料行仓库选取：委外发料单审批要真的出库，仓库选错就是扣错仓。
 * 实测 CL004 库存只在辅料仓，发料行却按表头兜底落成原料仓。
 */
class PxServiceIssueWarehouseTest {

    @Test
    void explicitWarehouseWithStockWins() {
        assertEquals("原料仓", PxService.pickIssueWarehouse("原料仓", true, "辅料仓", "原料仓"));
    }

    @Test
    void explicitWarehouseWithoutStockFallsBackToActualStock() {
        // CL004：预出仓写原料仓，但实际库存在辅料仓 → 按实际仓出库，否则扣成负库存
        assertEquals("辅料仓", PxService.pickIssueWarehouse("原料仓", false, "辅料仓", "原料仓"));
    }

    @Test
    void noStockAnywhereKeepsExplicitThenFallback() {
        assertEquals("半成品仓", PxService.pickIssueWarehouse("半成品仓", false, "", "原料仓"),
                "哪都没有存量时尊重单据上填的仓，由库存校验去拦");
        assertEquals("原料仓", PxService.pickIssueWarehouse("", false, "", "原料仓"));
    }

    @Test
    void blankExplicitUsesActualStockThenFallback() {
        assertEquals("辅料仓", PxService.pickIssueWarehouse("", false, "辅料仓", "原料仓"));
        assertEquals("成品仓", PxService.pickIssueWarehouse(null, false, "成品仓", "原料仓"));
    }
}
