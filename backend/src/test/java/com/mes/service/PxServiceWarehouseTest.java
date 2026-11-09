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
 * 仓库选择的落地（2026-11-05）。
 *
 * <p>要解决的两处写死：
 * <ul>
 *   <li>销售出库固定从「成品仓」发货，不能选仓，也不按所选仓库校验库存；</li>
 *   <li>加工单材料明细的「预出仓库」固定写「原料仓」，辅料仓的料被从原料仓出，
 *       直接出成负库存（实测 切削液 原料仓 -2.04）。</li>
 * </ul>
 */
@ExtendWith(MockitoExtension.class)
class PxServiceWarehouseTest {

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

    private FormData saleOrder() throws Exception {
        return document("SO_ORDER", "SO-1", "已审核",
                Map.of("单据编号", "SO-1", "客户", "华东铝业", "客户编码", "KH001"),
                Map.of("items", List.of(Map.of(
                        "存货编码", "CP001", "存货名称", "铝棒 Φ80", "规格型号", "Φ80×3000",
                        "数量", 10, "销售单位", "件", "含税单价", 20, "税率%", 13))));
    }

    // ---------- 销售出库：按所选仓库校验与落库 ----------

    @Test
    void saleOutUsesSelectedWarehouseForStockCheckAndRows() throws Exception {
        PxService spied = spy(service);
        doReturn(null).when(spied).findOpenSaleOut(anyString());
        doReturn(Map.of()).when(spied).shippedQtyByItem(anyString());
        when(panelMapper.selectOne(any())).thenReturn(config("SALE_OUT"));
        when(formMapper.selectOne(any())).thenReturn(saleOrder());
        when(stockTxnService.quantityOf(eq("CP001"), anyString(), eq("半成品仓")))
                .thenReturn(new BigDecimal("50"));
        when(formMapper.selectCount(any())).thenReturn(0L);

        spied.callButton("SO_ORDER", "生成销售出库单",
                Map.of("编号", "SO-1"),
                Map.of("warehouse", "半成品仓", "lines", List.of(Map.of("存货编码", "CP001", "数量", 10))));

        FormData target = captureInserted();
        Map<?, ?> detail = json.readValue(target.getDetailData(), Map.class);
        Map<?, ?> row = (Map<?, ?>) ((List<?>) detail.get("items")).get(0);
        assertEquals("半成品仓", row.get("仓库"), "出库单明细要落到所选仓库");
    }

    @Test
    void saleOutRejectsWhenSelectedWarehouseHasNoStock() throws Exception {
        PxService spied = spy(service);
        doReturn(null).when(spied).findOpenSaleOut(anyString());
        doReturn(Map.of()).when(spied).shippedQtyByItem(anyString());
        when(formMapper.selectOne(any())).thenReturn(saleOrder());
        when(stockTxnService.quantityOf(eq("CP001"), anyString(), eq("半成品仓")))
                .thenReturn(BigDecimal.ZERO);

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                spied.callButton("SO_ORDER", "生成销售出库单",
                        Map.of("编号", "SO-1"),
                        Map.of("warehouse", "半成品仓", "lines", List.of(Map.of("存货编码", "CP001", "数量", 10)))));

        assertTrue(e.getMessage().contains("半成品仓"), "提示要带出所选仓库：" + e.getMessage());
        verify(formMapper, never()).insert(any(FormData.class));
    }

    @Test
    void saleOutPreviewHonoursWarehouseParameter() throws Exception {
        PxService spied = spy(service);
        doReturn(Map.of()).when(spied).shippedQtyByItem(anyString());
        when(formMapper.selectOne(any())).thenReturn(saleOrder());
        when(stockTxnService.quantityOf(eq("CP001"), anyString(), eq("半成品仓")))
                .thenReturn(new BigDecimal("4"));

        List<Map<String, Object>> rows = spied.saleOutPreview("SO-1", "半成品仓");

        assertEquals(1, rows.size());
        assertEquals("半成品仓", rows.get(0).get("仓库"));
        assertEquals(4.0, ((Number) rows.get(0).get("现存量")).doubleValue(), 0.001);
        assertEquals(4.0, ((Number) rows.get(0).get("建议出库数量")).doubleValue(), 0.001);
    }

    /** 不传仓库时退回默认成品仓，保持旧接口兼容。 */
    @Test
    void saleOutDefaultsToFinishedGoodsWarehouse() throws Exception {
        PxService spied = spy(service);
        doReturn(Map.of()).when(spied).shippedQtyByItem(anyString());
        when(formMapper.selectOne(any())).thenReturn(saleOrder());
        when(stockTxnService.quantityOf(eq("CP001"), anyString(), eq("成品仓")))
                .thenReturn(new BigDecimal("9"));

        List<Map<String, Object>> rows = spied.saleOutPreview("SO-1", null);

        assertEquals("成品仓", rows.get(0).get("仓库"));
        assertEquals(9.0, ((Number) rows.get(0).get("现存量")).doubleValue(), 0.001);
    }

    // ---------- 加工单：材料预出仓库按实际库存仓库带出 ----------

    @Test
    void moMaterialsUseWarehouseThatActuallyHoldsStock() throws Exception {
        PxService spied = spy(service);
        doReturn(null).when(spied).findBlockingSaleOut(anyString());
        when(panelMapper.selectOne(any())).thenReturn(config("MANU_ORDER"));
        when(formMapper.selectOne(any())).thenReturn(saleOrder());
        when(formMapper.selectCount(any())).thenReturn(0L);
        FormData bom = document("BOM", "BOM-001", "启用", Map.of(),
                Map.of("children", List.of(
                        Map.of("父件编码", "CP001", "子件编码", "CL002", "子件名称", "6061铝锭",
                                "定额数量", 1.0, "子件计量单位", "kg"),
                        Map.of("父件编码", "CP001", "子件编码", "CL004", "子件名称", "切削液",
                                "定额数量", 0.02, "子件计量单位", "升"))));
        when(formMapper.selectList(any())).thenReturn(List.of(bom));
        // 铝锭在原料仓，切削液在辅料仓
        when(stockTxnService.warehouseOfItem(eq("CL002"), anyString())).thenReturn("原料仓");
        when(stockTxnService.warehouseOfItem(eq("CL004"), anyString())).thenReturn("辅料仓");

        spied.callButton("SO_ORDER", "生成生产加工单", Map.of("编号", "SO-1"),
                Map.of("lines", List.of(Map.of("存货编码", "CP001", "数量", 10))));

        FormData target = captureInserted();
        Map<?, ?> detail = json.readValue(target.getDetailData(), Map.class);
        List<?> materials = (List<?>) detail.get("materials");
        Map<String, String> wh = new java.util.HashMap<>();
        for (Object o : materials) {
            Map<?, ?> m = (Map<?, ?>) o;
            wh.put(String.valueOf(m.get("材料编码")), String.valueOf(m.get("预出仓库")));
        }
        assertEquals("原料仓", wh.get("CL002"));
        assertEquals("辅料仓", wh.get("CL004"), "切削液应落在辅料仓，不能默认原料仓");
    }

    /** 没有库存记录时退回原料仓，不产生空仓库。 */
    @Test
    void moMaterialsFallBackToRawWarehouse() throws Exception {
        PxService spied = spy(service);
        doReturn(null).when(spied).findBlockingSaleOut(anyString());
        when(panelMapper.selectOne(any())).thenReturn(config("MANU_ORDER"));
        when(formMapper.selectOne(any())).thenReturn(saleOrder());
        when(formMapper.selectCount(any())).thenReturn(0L);
        FormData bom = document("BOM", "BOM-001", "启用", Map.of(),
                Map.of("children", List.of(Map.of("父件编码", "CP001", "子件编码", "CL999",
                        "子件名称", "无库存料", "定额数量", 1.0, "子件计量单位", "kg"))));
        when(formMapper.selectList(any())).thenReturn(List.of(bom));
        when(stockTxnService.warehouseOfItem(eq("CL999"), anyString())).thenReturn(null);

        spied.callButton("SO_ORDER", "生成生产加工单", Map.of("编号", "SO-1"),
                Map.of("lines", List.of(Map.of("存货编码", "CP001", "数量", 10))));

        FormData target = captureInserted();
        Map<?, ?> detail = json.readValue(target.getDetailData(), Map.class);
        Map<?, ?> m = (Map<?, ?>) ((List<?>) detail.get("materials")).get(0);
        assertEquals("原料仓", m.get("预出仓库"));
    }

    private FormData captureInserted() {
        ArgumentCaptor<FormData> captor = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).insert(captor.capture());
        return captor.getValue();
    }
}