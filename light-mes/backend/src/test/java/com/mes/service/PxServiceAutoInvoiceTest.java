package com.mes.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.entity.PanelConfig;
import com.mes.mapper.FormApprovalMapper;
import com.mes.mapper.FormDataMapper;
import com.mes.mapper.PanelConfigMapper;
import com.mes.panel.PanelActionRegistry;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.doReturn;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.spy;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 入库 → 销货 → 发票 一路自动：销货单审批通过后系统自动开票，不需要人工再点一次生单。
 *
 * <p>要求：幂等（重复审批不重复开票），且自动开票失败不得阻断销货单审批。
 */
@ExtendWith(MockitoExtension.class)
class PxServiceAutoInvoiceTest {

    @Mock private PanelConfigMapper panelMapper;
    @Mock private FormDataMapper formMapper;
    @Mock private FormApprovalMapper approvalMapper;
    @Mock private ReportQueryService reportQueryService;
    @Mock private StockTxnService stockTxnService;

    private final ObjectMapper json = new ObjectMapper();
    private PxService service;

    @BeforeEach
    void setUp() {
        service = new PxService(panelMapper, formMapper, approvalMapper, reportQueryService,
                new PanelActionRegistry(List.of()), null, null, stockTxnService);
    }

    private FormData document(String panel, String no, String status,
                              Map<String, Object> head, Map<String, Object> detail) throws Exception {
        FormData row = new FormData();
        row.setId(1L);
        row.setPanelCode(panel);
        row.setFormNo(no);
        row.setStatus(status);
        row.setData(json.writeValueAsString(head));
        row.setDetailData(json.writeValueAsString(detail));
        row.setCreateTime(LocalDateTime.now());
        return row;
    }

    private PanelConfig config(String code) {
        PanelConfig cfg = new PanelConfig();
        cfg.setPanelCode(code);
        cfg.setPanelName(code);
        cfg.setConfig("{\"metadata\":{\"panelCode\":\"" + code
                + "\",\"autoCodeField\":\"单据编号\",\"panelButtons\":[],\"buttonGroups\":[],\"panelPageDto\":{}},"
                + "\"dataSchema\":{\"fields\":[]},\"detail\":{\"tabs\":[]}}");
        return cfg;
    }

    private FormData saleInv() throws Exception {
        return document("SALE_INV", "XS-1", "已审核",
                Map.of("单据编号", "XS-1", "客户", "华东铝业", "客户编码", "KH001"),
                Map.of("items", List.of(Map.of(
                        "存货编码", "CP001", "存货名称", "铝棒 Φ80", "规格型号", "Φ80×3000",
                        "数量", 10, "销售单位", "件", "含税单价", 33.9, "含税金额", 339))));
    }

    @Test
    void autoCreatesInvoiceFromApprovedSaleInv() throws Exception {
        PxService spied = spy(service);
        doReturn(null).when(spied).findInvoiceOfSaleInv(anyString());
        when(panelMapper.selectOne(any())).thenReturn(config("SALE_INVOICE"));
        when(formMapper.selectOne(any())).thenReturn(saleInv());
        when(formMapper.selectCount(any())).thenReturn(0L);

        spied.autoCreateInvoice("XS-1");

        ArgumentCaptor<FormData> captor = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).insert(captor.capture());
        assertEquals("SALE_INVOICE", captor.getValue().getPanelCode());
        Map<?, ?> detail = json.readValue(captor.getValue().getDetailData(), Map.class);
        Map<?, ?> row = (Map<?, ?>) ((List<?>) detail.get("items")).get(0);
        assertEquals(339.0, ((Number) row.get("价税合计")).doubleValue(), 0.01);
    }

    @Test
    void doesNotDuplicateInvoice() throws Exception {
        PxService spied = spy(service);
        doReturn("草稿").when(spied).findInvoiceOfSaleInv("XS-1");

        spied.autoCreateInvoice("XS-1");

        verify(formMapper, never()).insert(any(FormData.class));
    }

    /** 已开票的销货单再点「生成销售发票」应被拒绝，避免一单多票。 */
    @Test
    void manualInvoiceGenerationRejectsWhenAlreadyInvoiced() throws Exception {
        PxService spied = spy(service);
        doReturn("已审核").when(spied).findInvoiceOfSaleInv(anyString());
        when(formMapper.selectOne(any())).thenReturn(saleInv());

        IllegalStateException e = org.junit.jupiter.api.Assertions.assertThrows(
                IllegalStateException.class,
                () -> spied.callButton("SALE_INV", "生成销售发票", Map.of("编号", "XS-1"), Map.of()));
        assertTrue(e.getMessage().contains("已开票"), e.getMessage());
    }

    /** 自动开票失败不能把销货单审批带崩。 */
    @Test
    void swallowsErrorsSoApprovalIsNotBlocked() {
        PxService spied = spy(service);
        doReturn(null).when(spied).findInvoiceOfSaleInv(anyString());
        when(formMapper.selectOne(any())).thenThrow(new RuntimeException("数据异常"));

        spied.autoCreateInvoice("XS-1"); // 不抛异常即通过
        assertTrue(true);
    }
}
