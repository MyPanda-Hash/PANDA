package com.mes.service;

import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;

import java.math.BigDecimal;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/**
 * 按期率：没样本时必须返回 null 而不是 0%。
 *
 * <p>这是口径问题不是算法问题：0% 会被读成「全都不按期」，
 * 而真实情况往往是「还没有已交付/已完工的单据」。
 */
class OnTimeServiceTest {

    private final JdbcTemplate jdbc = mock(JdbcTemplate.class);
    private final OnTimeService service = new OnTimeService(jdbc);

    private Map<String, Object> row(long total, long onTime) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("total", total);
        m.put("on_time", onTime);
        return m;
    }

    @Test
    void rateComputedFromSamples() {
        when(jdbc.queryForList(anyString(), any(Object[].class)))
                .thenReturn(List.of(row(10, 9)), List.of(row(4, 1)));

        Map<String, Object> out = service.onTime("2026-11");

        @SuppressWarnings("unchecked")
        Map<String, Object> order = (Map<String, Object>) out.get("order");
        @SuppressWarnings("unchecked")
        Map<String, Object> mo = (Map<String, Object>) out.get("manuOrder");
        assertEquals(0, new BigDecimal("90.0").compareTo((BigDecimal) order.get("rate")));
        assertEquals(0, new BigDecimal("25.0").compareTo((BigDecimal) mo.get("rate")));
        assertTrue((Boolean) order.get("hasData"));
    }

    @Test
    void noSamplesYieldNullRateInsteadOfZero() {
        when(jdbc.queryForList(anyString(), any(Object[].class)))
                .thenReturn(List.of(row(0, 0)), List.of());

        Map<String, Object> out = service.onTime(null);

        @SuppressWarnings("unchecked")
        Map<String, Object> order = (Map<String, Object>) out.get("order");
        @SuppressWarnings("unchecked")
        Map<String, Object> mo = (Map<String, Object>) out.get("manuOrder");
        assertNull(order.get("rate"), "没有样本时不能报 0%");
        assertFalse((Boolean) order.get("hasData"));
        assertNull(mo.get("rate"));
        assertEquals("全部", out.get("period"));
    }

    @Test
    void periodIsEchoedAndDefaultsToAll() {
        when(jdbc.queryForList(anyString(), any(Object[].class))).thenReturn(List.of(), List.of());
        assertEquals("2026-11", service.onTime("2026-11").get("period"));
        assertEquals("全部", service.onTime("").get("period"));
    }
}
