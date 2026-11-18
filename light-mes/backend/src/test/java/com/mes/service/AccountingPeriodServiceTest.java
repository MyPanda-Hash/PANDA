package com.mes.service;

import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;

import java.time.LocalDate;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/** 会计期间：取期口径、封账校验、格式校验。 */
class AccountingPeriodServiceTest {

    private final JdbcTemplate jdbc = mock(JdbcTemplate.class);
    private final AccountingPeriodService service = new AccountingPeriodService(jdbc);

    @Test
    void periodDerivedFromBizDate() {
        assertEquals("2026-11", service.resolvePeriod(LocalDate.of(2026, 11, 12)));
        assertEquals("2026-01", service.resolvePeriod(LocalDate.of(2026, 1, 31)));
    }

    @Test
    void missingBizDateFallsBackToCurrentMonth() {
        // 宁可记进当月，也不让凭证因为缺日期丢失
        assertEquals(LocalDate.now().toString().substring(0, 7), service.resolvePeriod(null));
    }

    @Test
    void closedPeriodIsDetected() {
        when(jdbc.queryForList(anyString(), eq(String.class), eq("2026-11"), eq("F01")))
                .thenReturn(List.of("closed"));
        assertTrue(service.isClosed("2026-11"));

        when(jdbc.queryForList(anyString(), eq(String.class), eq("2026-10"), eq("F01")))
                .thenReturn(List.of("open"));
        assertFalse(service.isClosed("2026-10"));
    }

    @Test
    void unknownPeriodIsTreatedAsOpen() {
        when(jdbc.queryForList(anyString(), eq(String.class), anyString(), anyString()))
                .thenReturn(List.of());
        assertFalse(service.isClosed("2027-01"), "没登记过的期间视为可记账");
        assertFalse(service.isClosed(null));
        assertFalse(service.isClosed("  "));
    }

    @Test
    void requireOpenThrowsOnClosedPeriodWithActionInMessage() {
        when(jdbc.queryForList(anyString(), eq(String.class), anyString(), anyString()))
                .thenReturn(List.of("closed"));

        IllegalStateException e = assertThrows(IllegalStateException.class,
                () -> service.requireOpen("2026-11", "冲销凭证"));
        assertTrue(e.getMessage().contains("2026-11"));
        assertTrue(e.getMessage().contains("冲销凭证"));
    }

    @Test
    void closeRejectsMalformedPeriod() {
        assertThrows(IllegalArgumentException.class, () -> service.close("2026/11", "admin"));
        assertThrows(IllegalArgumentException.class, () -> service.close("202611", "admin"));
        assertThrows(IllegalArgumentException.class, () -> service.close(null, "admin"));
    }
}
