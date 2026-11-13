package com.mes.service;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

/**
 * 单号前缀反查面板：AI 待办只给单号时要靠它拼跳转路径，猜错会把用户带到错误的面板。
 */
class DocNoRulesPanelLookupTest {

    @Test
    @DisplayName("已知前缀能反查出面板")
    void resolvesKnownPrefixes() {
        assertEquals("SO_ORDER", DocNoRules.panelOfFormNo("SO-20261113-001"));
        assertEquals("PU_ORDER", DocNoRules.panelOfFormNo("PO-2026-08-0002"));
        assertEquals("SALE_OUT", DocNoRules.panelOfFormNo("CK-20261113-001"));
        assertEquals("OUTSOURCE_ORDER", DocNoRules.panelOfFormNo("WW-20261113-001"));
        assertEquals("OUTSOURCE_INSPECTION", DocNoRules.panelOfFormNo("WJ-20261113-001"));
        assertEquals("STOCK_CHECK", DocNoRules.panelOfFormNo("PD-20261113-001"));
    }

    @Test
    @DisplayName("每个面板的前缀都能反查回自己（保证前缀唯一）")
    void prefixRoundTrip() {
        for (String panel : java.util.List.of("SO_ORDER", "PU_ORDER", "SALE_OUT", "SALE_INV",
                "OUTSOURCE_INSPECTION", "STOCK_CHECK", "INSPECTION", "MATERIAL_OUT")) {
            String prefix = DocNoRules.prefixOf(panel);
            assertEquals(panel, DocNoRules.panelOfFormNo(prefix + "-20261113-001"),
                    panel + " 的前缀 " + prefix + " 反查不回去");
        }
    }

    @Test
    @DisplayName("认不出或前缀不唯一时返回空串，调用方不要瞎猜")
    void unknownReturnsEmpty() {
        assertTrue(DocNoRules.panelOfFormNo("").isEmpty());
        assertTrue(DocNoRules.panelOfFormNo("ZZZ-123").isEmpty());
        assertTrue(DocNoRules.panelOfFormNo("SO20261113").isEmpty());
        // 没登记前缀的面板会落到 GEN，多处共用，不能反指某一个面板
        assertTrue(DocNoRules.panelOfFormNo("GEN-20261113-001").isEmpty(), "GEN 前缀不该反查出具体面板");
    }
}
