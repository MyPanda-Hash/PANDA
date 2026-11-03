package com.mes.service;

import com.mes.entity.FinanceVoucher;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 对账/账龄查询服务：把 finance_voucher 记录转成 OpenItem 再交给 FinanceStatementService。
 *
 * mapper 为 null 时不能抛异常，报表应返回空列表（与其它报表服务一致）。
 */
class FinanceStatementQueryServiceTest {

    @Test
    void mapperNullReturnsEmptyNotNull() {
        FinanceStatementQueryService service =
                new FinanceStatementQueryService(null, new FinanceStatementService());

        assertTrue(service.customerStatement().isEmpty());
        assertTrue(service.supplierStatement().isEmpty());
        assertTrue(service.aging().isEmpty());
    }

    @Test
    void voucherToOpenItemKeepsTypePartnerAmountsDateAndSource() {
        FinanceStatementQueryService service =
                new FinanceStatementQueryService(null, new FinanceStatementService());

        FinanceVoucher v = new FinanceVoucher();
        v.setVoucherType("AR");
        v.setPartner("中天精工");
        v.setAmount(new BigDecimal("250"));
        v.setSettledAmount(new BigDecimal("50"));
        v.setBizDate(LocalDate.parse("2026-11-02"));
        v.setSourcePanel("SALE_OUT");
        v.setSourceNo("CK-2026-11-0203");

        FinanceStatementService.OpenItem item = service.toOpenItem(v);

        assertEquals("AR", item.type());
        assertEquals("中天精工", item.partner());
        assertEquals(0, new BigDecimal("250").compareTo(item.amount()));
        assertEquals(0, new BigDecimal("50").compareTo(item.settled()));
        assertEquals("2026-11-02", item.bizDate());
        assertEquals("SALE_OUT", item.sourcePanel());
        assertEquals("CK-2026-11-0203", item.sourceNo());
    }

    @Test
    void voucherWithNullFieldsIsTolerated() {
        FinanceStatementQueryService service =
                new FinanceStatementQueryService(null, new FinanceStatementService());

        FinanceVoucher v = new FinanceVoucher();
        v.setVoucherType("AP");
        v.setPartner(null);
        v.setAmount(null);
        v.setSettledAmount(null);
        v.setBizDate(null);
        v.setSourcePanel(null);
        v.setSourceNo(null);

        FinanceStatementService.OpenItem item = service.toOpenItem(v);

        assertEquals(0, BigDecimal.ZERO.compareTo(item.amount()));
        assertEquals(0, BigDecimal.ZERO.compareTo(item.settled()));
        assertEquals("", item.bizDate());
        assertEquals("", item.partner());
        assertEquals("", item.sourcePanel());
        assertEquals("", item.sourceNo());
    }

    @Test
    void emptyMapperStillReturnsListType() {
        FinanceStatementQueryService service =
                new FinanceStatementQueryService(null, new FinanceStatementService());

        List<Map<String, Object>> rows = service.customerStatement();
        assertTrue(rows.isEmpty());
    }

    @Test
    void agingAcceptsExplicitBaseDateFromPanelCondition() {
        FinanceStatementQueryService service =
                new FinanceStatementQueryService(null, new FinanceStatementService());

        // 面板「截止日期」查询条件必须能传进来；mapper 为空时仍返回空列表
        assertTrue(service.aging("2026-11-30").isEmpty());
        assertTrue(service.aging(null).isEmpty());
        assertTrue(service.aging("").isEmpty());
    }

    @Test
    void agingBaseDateFallsBackToTodayWhenBlank() {
        FinanceStatementQueryService service =
                new FinanceStatementQueryService(null, new FinanceStatementService());

        // 空串与 null 都应回退到今天，而不是抛异常
        assertEquals(service.aging(null), service.aging(""));
    }
}
