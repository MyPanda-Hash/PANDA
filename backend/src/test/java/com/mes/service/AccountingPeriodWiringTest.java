package com.mes.service;

import com.mes.entity.AccountingEntry;
import com.mes.mapper.AccountingEntryMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 会计期间接线：凭证写入必须带期间，且封账期间拒绝一切写操作。
 *
 * <p>盯的是接线不是算法 —— AccountingPeriodService 的单测只证明取期与校验函数本身对，
 * 但「凭证到底有没有经过它」要靠这里。同一类缺陷此前让销售出库成本按销售额记了 13 倍。
 */
class AccountingPeriodWiringTest {

    private AccountingEntryMapper entryMapper;
    private AccountingPeriodService periodService;
    private AccountingService service;

    @BeforeEach
    void setUp() {
        entryMapper = mock(AccountingEntryMapper.class);
        periodService = mock(AccountingPeriodService.class);
        service = new AccountingService(entryMapper);
        service.setPeriodService(periodService);
    }

    @Test
    void postingStampsPeriodOnEveryEntry() {
        when(periodService.resolvePeriod(any())).thenReturn("2026-11");

        service.post("RECEIPT", "SK-1", new BigDecimal("100"), "客户A", "收款", LocalDate.of(2026, 11, 12));

        org.mockito.ArgumentCaptor<AccountingEntry> captor =
                org.mockito.ArgumentCaptor.forClass(AccountingEntry.class);
        verify(entryMapper, org.mockito.Mockito.atLeastOnce()).insert(captor.capture());
        List<AccountingEntry> inserted = captor.getAllValues();
        assertEquals(2, inserted.size(), "收款生一借一贷两条分录");
        for (AccountingEntry entry : inserted) {
            assertEquals("2026-11", entry.getPeriod(), "每条分录都必须带上会计期间");
        }
    }

    @Test
    void postingIntoClosedPeriodIsRejected() {
        when(periodService.resolvePeriod(any())).thenReturn("2026-11");
        doThrow(new IllegalStateException("会计期间 2026-11 已封账，不能记账"))
                .when(periodService).requireOpen("2026-11", "记账");

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                service.post("RECEIPT", "SK-2", new BigDecimal("100"), "客户A", "收款", LocalDate.of(2026, 11, 12)));

        assertTrue(e.getMessage().contains("已封账"));
        verify(entryMapper, never()).insert(any(AccountingEntry.class));
    }

    @Test
    void reversingClosedPeriodIsRejected() {
        AccountingEntry existing = new AccountingEntry();
        existing.setSourcePanel("RECEIPT");
        existing.setSourceNo("SK-3");
        existing.setPeriod("2026-11");
        when(entryMapper.selectList(any())).thenReturn(List.of(existing));
        doThrow(new IllegalStateException("会计期间 2026-11 已封账，不能冲销凭证"))
                .when(periodService).requireOpen("2026-11", "冲销凭证");

        IllegalStateException e = assertThrows(IllegalStateException.class,
                () -> service.reverse("RECEIPT", "SK-3"));

        assertTrue(e.getMessage().contains("已封账"));
        verify(entryMapper, never()).delete(any());
    }

    @Test
    void saleInvoicePostingAlsoCarriesPeriod() {
        when(periodService.resolvePeriod(any())).thenReturn("2026-11");

        service.postSaleInvoice("XS-1", new BigDecimal("113"), new BigDecimal("13"),
                "客户B", "销货", LocalDate.of(2026, 11, 12));

        org.mockito.ArgumentCaptor<AccountingEntry> captor =
                org.mockito.ArgumentCaptor.forClass(AccountingEntry.class);
        verify(entryMapper, org.mockito.Mockito.atLeastOnce()).insert(captor.capture());
        assertTrue(captor.getAllValues().stream().allMatch(e -> "2026-11".equals(e.getPeriod())),
                "销货单走的是另一条写入口，同样不能漏期间");
    }
}
