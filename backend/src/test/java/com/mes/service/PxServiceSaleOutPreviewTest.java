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

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doReturn;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.spy;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 销售出库的生单预览与按量出库。
 *
 * <p>与生产加工单预览保持同一套体验：出库前先看到每行库存与剩余待出数量，
 * 可以只出其中一部分，不必整单照抄。
 *
 * <p>剩余待出 = 需求 − 已审核出库量；建议出库 = min(剩余待出, 现存量)。
 */
@ExtendWith(MockitoExtension.class)
class PxServiceSaleOutPreviewTest {

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

    private FormData saleOrder(int qty) throws Exception {
        return document("SO_ORDER", "SO-1", "已审核",
                Map.of("单据编号", "SO-1", "客户", "华东铝业", "客户编码", "KH001"),
                Map.of("items", List.of(Map.of(
                        "存货编码", "CP001", "存货名称", "铝棒 Φ80", "规格型号", "Φ80×3000",
                        "数量", qty, "销售单位", "件", "含税单价", 20, "税率%", 13))));
    }

    @Test
    void previewShowsStockAndRemainingToShip() throws Exception {
        PxService spied = spy(service);
        doReturn(Map.of()).when(spied).shippedQtyByItem(anyString());
        when(formMapper.selectOne(any())).thenReturn(saleOrder(100));
        when(stockTxnService.quantityOf(eq("CP001"), anyString(), eq("成品仓")))
                .thenReturn(new BigDecimal("60"));

        List<Map<String, Object>> rows = spied.saleOutPreview("SO-1");

        assertEquals(1, rows.size());
        Map<String, Object> r = rows.get(0);
        assertEquals(100.0, ((Number) r.get("需求数量")).doubleValue(), 0.001);
        assertEquals(0.0, ((Number) r.get("已出库数量")).doubleValue(), 0.001);
        assertEquals(60.0, ((Number) r.get("现存量")).doubleValue(), 0.001);
        assertEquals(60.0, ((Number) r.get("建议出库数量")).doubleValue(), 0.001, "有 60 只能出 60");
        assertEquals(true, r.get("是否不足"));
    }

    /** 已审核出库的部分不再算进剩余待出，否则会重复出库。 */
    @Test
    void previewDeductsAlreadyShippedQuantity() throws Exception {
        PxService spied = spy(service);
        doReturn(Map.of("CP001", 60.0)).when(spied).shippedQtyByItem(anyString());
        when(formMapper.selectOne(any())).thenReturn(saleOrder(100));
        when(stockTxnService.quantityOf(eq("CP001"), anyString(), eq("成品仓")))
                .thenReturn(new BigDecimal("100"));

        List<Map<String, Object>> rows = spied.saleOutPreview("SO-1");

        Map<String, Object> r = rows.get(0);
        assertEquals(60.0, ((Number) r.get("已出库数量")).doubleValue(), 0.001);
        assertEquals(40.0, ((Number) r.get("剩余待出数量")).doubleValue(), 0.001);
        assertEquals(40.0, ((Number) r.get("建议出库数量")).doubleValue(), 0.001);
        assertEquals(false, r.get("是否不足"));
    }

    /** 按用户填的数量出库，而不是整单照抄。 */
    @Test
    void createsSaleOutWithSelectedQuantity() throws Exception {
        PxService spied = spy(service);
        doReturn(null).when(spied).findOpenSaleOut(anyString());
        doReturn(Map.of()).when(spied).shippedQtyByItem(anyString());
        when(panelMapper.selectOne(any())).thenReturn(config("SALE_OUT"));
        when(formMapper.selectOne(any())).thenReturn(saleOrder(100));
        when(stockTxnService.quantityOf(eq("CP001"), anyString(), eq("成品仓")))
                .thenReturn(new BigDecimal("100"));
        when(formMapper.selectCount(any())).thenReturn(0L);

        spied.callButton("SO_ORDER", "生成销售出库单",
                Map.of("编号", "SO-1"),
                Map.of("lines", List.of(Map.of("存货编码", "CP001", "数量", 30))));

        FormData target = captureInserted();
        Map<?, ?> detail = json.readValue(target.getDetailData(), Map.class);
        Map<?, ?> row = (Map<?, ?>) ((List<?>) detail.get("items")).get(0);
        assertEquals(30.0, ((Number) row.get("数量")).doubleValue(), 0.001, "数量以用户填的为准");
    }

    /** 剩余待出为 0（已全部出库）时不允许再生成。 */
    @Test
    void rejectsWhenOrderFullyShipped() throws Exception {
        PxService spied = spy(service);
        doReturn(null).when(spied).findOpenSaleOut(anyString());
        doReturn(Map.of("CP001", 100.0)).when(spied).shippedQtyByItem(anyString());
        when(formMapper.selectOne(any())).thenReturn(saleOrder(100));

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                spied.callButton("SO_ORDER", "生成销售出库单", Map.of("编号", "SO-1"), Map.of()));

        assertTrue(e.getMessage().contains("已全部出库"), e.getMessage());
        verify(formMapper, never()).insert(any(FormData.class));
    }

    /** 已有未完结（草稿/审批中）出库单时，仍要拦住重复生成。 */
    @Test
    void rejectsDuplicateOpenSaleOut() throws Exception {
        PxService spied = spy(service);
        doReturn("草稿").when(spied).findOpenSaleOut("SO-1");
        when(formMapper.selectOne(any())).thenReturn(saleOrder(100));

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                spied.callButton("SO_ORDER", "生成销售出库单", Map.of("编号", "SO-1"), Map.of()));

        assertTrue(e.getMessage().contains("已有销售出库单"), e.getMessage());
        verify(formMapper, never()).insert(any(FormData.class));
    }

    private FormData captureInserted() {
        ArgumentCaptor<FormData> captor = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).insert(captor.capture());
        return captor.getValue();
    }
}