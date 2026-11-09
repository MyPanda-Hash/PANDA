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
import static org.mockito.Mockito.spy;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 销货价补价：产成品入库单没有销售价格，直接照抄会把销货单/发票做成 0 金额，
 * 导致应收凭证不生成、收款无从核销。
 */
@ExtendWith(MockitoExtension.class)
class PxServiceSalePriceTest {

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

    @Test
    void saleInvTakesPriceFromRelatedSalesOrderWhenSourceHasNone() throws Exception {
        PxService spied = spy(service);
        doReturn(Map.of("CP003", 33.9)).when(spied).salePriceByItem(anyString());
        when(panelMapper.selectOne(any())).thenReturn(config("SALE_INV"));
        FormData fi = document("FINISH_IN", "FI-1", "已审核",
                Map.of("单据编号", "FI-1", "加工单号", "MO-1", "客户", "华东铝业", "客户编码", "KH001", "仓库", "成品仓"),
                Map.of("items", List.of(Map.of(
                        "存货编码", "CP003", "存货名称", "铝型材-散热片", "规格型号", "XD-6063-T5",
                        "计量单位", "件", "实收数量", 20, "单价", 0, "金额", 0))));
        when(formMapper.selectOne(any())).thenReturn(fi);
        when(formMapper.selectCount(any())).thenReturn(0L);

        spied.callButton("FINISH_IN", "生成销货单", Map.of("编号", "FI-1"), Map.of());

        ArgumentCaptor<FormData> captor = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).insert(captor.capture());
        Map<?, ?> detail = json.readValue(captor.getValue().getDetailData(), Map.class);
        Map<?, ?> row = (Map<?, ?>) ((List<?>) detail.get("items")).get(0);
        assertEquals(33.9, ((Number) row.get("含税单价")).doubleValue(), 0.001, "销货价应取关联销售订单");
        assertEquals(678.0, ((Number) row.get("含税金额")).doubleValue(), 0.01, "20 × 33.9 = 678");
    }

    @Test
    void fallsBackToZeroWhenNoSalesOrderPrice() throws Exception {
        PxService spied = spy(service);
        doReturn(Map.of()).when(spied).salePriceByItem(anyString());
        when(panelMapper.selectOne(any())).thenReturn(config("SALE_INV"));
        FormData fi = document("FINISH_IN", "FI-2", "已审核",
                Map.of("单据编号", "FI-2", "加工单号", "MO-2", "客户", "华东铝业", "仓库", "成品仓"),
                Map.of("items", List.of(Map.of(
                        "存货编码", "CP999", "存货名称", "未知料", "计量单位", "件",
                        "实收数量", 5, "单价", 0, "金额", 0))));
        when(formMapper.selectOne(any())).thenReturn(fi);
        when(formMapper.selectCount(any())).thenReturn(0L);

        spied.callButton("FINISH_IN", "生成销货单", Map.of("编号", "FI-2"), Map.of());

        ArgumentCaptor<FormData> captor = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).insert(captor.capture());
        Map<?, ?> detail = json.readValue(captor.getValue().getDetailData(), Map.class);
        Map<?, ?> row = (Map<?, ?>) ((List<?>) detail.get("items")).get(0);
        assertEquals(0.0, ((Number) row.get("含税单价")).doubleValue(), 0.001, "无价可补时保持 0，不臆造");
        assertTrue(true);
    }
}
