package com.mes.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FinanceVoucher;
import com.mes.mapper.FinanceVoucherMapper;
import com.mes.mapper.FormApprovalMapper;
import com.mes.mapper.FormDataMapper;
import com.mes.mapper.PanelConfigMapper;
import com.mes.panel.PanelActionRegistry;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/** 采购退货 AP_RETURN 自动抵扣未结 AP。 */
@ExtendWith(MockitoExtension.class)
class PxServicePayableOffsetTest {

    @Mock private PanelConfigMapper panelMapper;
    @Mock private FormDataMapper formMapper;
    @Mock private FormApprovalMapper approvalMapper;
    @Mock private ReportQueryService reportQueryService;
    @Mock private FinancePaymentService financePaymentService;
    @Mock private FinanceVoucherMapper financeVoucherMapper;

    private final ObjectMapper json = new ObjectMapper();
    private PxService service;

    @BeforeEach
    void setUp() {
        service = new PxService(panelMapper, formMapper, approvalMapper, reportQueryService,
                new PanelActionRegistry(List.of()), null, null, null, null,
                financePaymentService, financeVoucherMapper, null, null);
    }

    @Test
    void offsetsPurchaseReturnCreditAgainstOpenPayable() {
        FinanceVoucher credit = voucher("AP_RETURN", "PURCHASE_RETURN", "CT-001", "华东铝业", 113);
        FinanceVoucher payable = voucher("AP", "PU_INVOICE", "PI-001", "华东铝业", 500);
        when(financeVoucherMapper.selectOne(any())).thenReturn(credit);
        when(financeVoucherMapper.selectList(any())).thenReturn(List.of(payable));
        when(financePaymentService.allocate(any(), any())).thenReturn(List.of(
                new FinancePaymentService.Allocation(payable, BigDecimal.valueOf(113))));
        when(financePaymentService.executePlan(any(), any(), any())).thenReturn(1);

        service.offsetPurchaseReturnPayable("CT-001", Map.of("供应商", "华东铝业"));

        verify(financePaymentService).reverse("CT-001");
        verify(financePaymentService).executePlan(any(), any(), any());
        assertEquals(BigDecimal.valueOf(113), credit.getSettledAmount());
        assertEquals("已结算", credit.getStatus());
    }

    private static FinanceVoucher voucher(String type, String panel, String no, String partner, double amount) {
        FinanceVoucher voucher = new FinanceVoucher();
        voucher.setVoucherType(type);
        voucher.setSourcePanel(panel);
        voucher.setSourceNo(no);
        voucher.setPartner(partner);
        voucher.setAmount(BigDecimal.valueOf(amount));
        voucher.setSettledAmount(BigDecimal.ZERO);
        voucher.setStatus("未结算");
        return voucher;
    }
}
