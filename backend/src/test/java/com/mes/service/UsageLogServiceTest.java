package com.mes.service;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

/** 埋点字段截断：超长值不能撑爆列宽 */
class UsageLogServiceTest {

    @Test
    void keepsShortValues() {
        assertEquals("admin", UsageLogService.cut("admin", 50));
        assertNull(UsageLogService.cut(null, 50));
    }

    @Test
    void truncatesLongValues() {
        String longName = "x".repeat(80);
        assertEquals(50, UsageLogService.cut(longName, 50).length());
    }
}
