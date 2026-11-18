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
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.spy;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 剩余硬编码仓库的处理（采购入库 / 委外入库 / 委外发料 / 材料出库 / 调拨）。
 *
 * <p>原则：
 * <ol>
 *   <li>出库类逐行按「该物料实际有库存的仓库」带出，避免从错误仓库出成负库存；</li>
 *   <li>入库/调拨类接受 buttonParam 指定仓库，未指定时用各自的业务默认值；</li>
 *   <li>落库后这些仓库字段仍可在单据表单里改，审核前都能调整。</li>
 * </ol>
 */
@ExtendWith(MockitoExtension.class)
class PxServiceWarehouseFallbackTest {

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

    private List<Map<String, Object>> firstDetailRows(FormData target) throws Exception {
        Map<?, ?> detail = json.readValue(target.getDetailData(), Map.class);
        if (detail.get("items") instanceof List<?> items) {
            return items.stream().map(o -> (Map<String, Object>) o).toList();
        }
        return List.of();
    }

    // ---------- 材料出库：无预出仓库时按实际库存仓库 ----------

    @Test
    void materialOutFallsBackToStockWarehouse() throws Exception {
        when(panelMapper.selectOne(any())).thenReturn(config("MATERIAL_OUT"));
        FormData req = document("MATERIAL_REQ", "LL-1", "已审核",
                Map.of("单据编号", "LL-1"),
                Map.of("items", List.of(Map.of(
                        "材料编码", "CL004", "材料名称", "切削液", "数量", 5, "计量单位", "升"))));
        when(formMapper.selectOne(any())).thenReturn(req);
        when(formMapper.selectCount(any())).thenReturn(0L);
        when(stockTxnService.warehouseOfItem(eq("CL004"), anyString())).thenReturn("辅料仓");

        service.callButton("MATERIAL_REQ", "生成材料出库单", Map.of("编号", "LL-1"), Map.of());

        FormData target = captureInserted();
        Map<String, Object> row = firstDetailRows(target).get(0);
        assertEquals("辅料仓", row.get("仓库"), "应按切削液实际库存仓库出库，而不是原料仓");
    }

    // ---------- 采购入库：接受 warehouse 参数 ----------

    @Test
    void purchaseInAcceptsWarehouseParameter() throws Exception {
        when(panelMapper.selectOne(any())).thenReturn(config("PURCHASE_IN"));
        FormData po = document("PU_ORDER", "PO-1", "已审核",
                Map.of("单据编号", "PO-1"),
                Map.of("items", List.of(Map.of(
                        "存货编码", "CL002", "存货名称", "6061铝锭", "数量", 100, "单价", 18))));
        when(formMapper.selectOne(any())).thenReturn(po);
        when(formMapper.selectCount(any())).thenReturn(0L);

        service.callButton("PU_ORDER", "生成采购入库单", Map.of("编号", "PO-1"),
                Map.of("warehouse", "不良品仓"));

        FormData target = captureInserted();
        Map<String, Object> row = firstDetailRows(target).get(0);
        assertEquals("不良品仓", row.get("仓库"));
    }

    @Test
    void purchaseInDefaultsToRawWarehouse() throws Exception {
        when(panelMapper.selectOne(any())).thenReturn(config("PURCHASE_IN"));
        FormData po = document("PU_ORDER", "PO-1", "已审核",
                Map.of("单据编号", "PO-1"),
                Map.of("items", List.of(Map.of(
                        "存货编码", "CL002", "存货名称", "6061铝锭", "数量", 100, "单价", 18))));
        when(formMapper.selectOne(any())).thenReturn(po);
        when(formMapper.selectCount(any())).thenReturn(0L);

        service.callButton("PU_ORDER", "生成采购入库单", Map.of("编号", "PO-1"), Map.of());

        FormData target = captureInserted();
        assertEquals("原料仓", firstDetailRows(target).get(0).get("仓库"));
    }

    // ---------- 调拨：调出/调入双仓 ----------

    @Test
    void transferAcceptsFromAndToWarehouse() throws Exception {
        when(panelMapper.selectOne(any())).thenReturn(config("TRANSFER"));
        FormData req = document("MATERIAL_REQ", "LL-2", "已审核",
                Map.of("单据编号", "LL-2", "仓库", "原料仓"),
                Map.of("items", List.of(Map.of(
                        "材料编码", "CL002", "材料名称", "6061铝锭", "数量", 10))));
        when(formMapper.selectOne(any())).thenReturn(req);
        when(formMapper.selectCount(any())).thenReturn(0L);

        service.callButton("MATERIAL_REQ", "生成调拨单", Map.of("编号", "LL-2"),
                Map.of("fromWarehouse", "原料仓", "toWarehouse", "半成品仓"));

        FormData target = captureInserted();
        Map<?, ?> head = json.readValue(target.getData(), Map.class);
        assertEquals("原料仓", head.get("调出仓库"));
        assertEquals("半成品仓", head.get("调入仓库"));
    }

    private FormData captureInserted() {
        ArgumentCaptor<FormData> captor = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).insert(captor.capture());
        return captor.getValue();
    }
}