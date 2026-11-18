package com.mes.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
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
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 按批次领料动作：生成一张只含该批次一行的材料出库单，并自动带上领料工序/领料人。
 */
@ExtendWith(MockitoExtension.class)
class PxServiceIssueActionTest {

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

    private FormData doc(String panel, String no, String status, Map<String, Object> head, Map<String, Object> detail) throws Exception {
        FormData d = new FormData();
        d.setId(1L);
        d.setPanelCode(panel);
        d.setFormNo(no);
        d.setStatus(status);
        d.setData(json.writeValueAsString(head));
        d.setDetailData(json.writeValueAsString(detail));
        d.setCreateTime(LocalDateTime.now());
        return d;
    }

    private com.mes.entity.PanelConfig config(String code) {
        com.mes.entity.PanelConfig cfg = new com.mes.entity.PanelConfig();
        cfg.setPanelCode(code);
        cfg.setPanelName(code);
        cfg.setConfig("{\"metadata\":{\"panelCode\":\"" + code
                + "\",\"autoCodeField\":\"单据编号\",\"panelButtons\":[],\"buttonGroups\":[],\"panelPageDto\":{}},"
                + "\"dataSchema\":{\"fields\":[]},\"detail\":{\"tabs\":[]}}");
        return cfg;
    }

    private FormData mo() throws Exception {
        return doc("MANU_ORDER", "MO-1", "已审核",
                Map.of("单据编号", "MO-1", "生产车间", "熔铸车间"),
                Map.of(
                        "products", List.of(Map.of("产品编码", "CP001", "产品名称", "铝棒", "数量", 100)),
                        "processes", List.of(Map.of("工序编码", "PX001", "工序名称", "下料")),
                        "materials", List.of(Map.of(
                                "材料编码", "CL001", "材料名称", "6063铝棒", "计量单位", "kg",
                                "规格型号", "Φ120", "需用数量", 200, "计划数量", 200, "预出仓库", "原料仓"))));
    }

    private FormData batch() throws Exception {
        return doc("DISPATCH", "PG-1", "已审核",
                Map.of("单据编号", "PG-1", "加工单号", "MO-1"),
                Map.of("items", List.of(Map.of(
                        "工序编码", "PX001", "工序名称", "下料", "派工数量", 40))));
    }

    @Test
    void createsIssueBoundToBatchWithProcessAndPerson() throws Exception {
        // 调用顺序：批次 → 加工单（本次缓存）→ 整单上限 → 批次分摊（加工单、批次）
        when(formMapper.selectOne(any())).thenReturn(batch(), mo(), mo(), mo(), batch());
        lenient().when(formMapper.selectList(any())).thenReturn(List.of());
        lenient().when(panelMapper.selectOne(any())).thenReturn(config("MATERIAL_OUT"));

        PxService spied = org.mockito.Mockito.spy(service);
        org.mockito.Mockito.doNothing().when(spied).submitIssueForApproval(anyString(), any());
        spied.issueByBatch("PG-1", "CL001", 80, "王强", "");

        ArgumentCaptor<FormData> captor = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).insert(captor.capture());
        FormData saved = captor.getValue();
        assertEquals("MATERIAL_OUT", saved.getPanelCode());
        Map<?, ?> detail = json.readValue(saved.getDetailData(), Map.class);
        Map<?, ?> row = (Map<?, ?>) ((List<?>) detail.get("items")).get(0);
        assertEquals("PG-1", row.get("排产单号"));
        assertEquals("下料", row.get("领料工序"));
        assertEquals("王强", row.get("领料人"));
        assertEquals("CL001", row.get("材料编码"));
        assertEquals(80.0, ((Number) row.get("数量")).doubleValue(), 0.001);
        assertEquals("MO-1", row.get("加工单号"));
    }

    @Test
    void rejectsOverBatchQuantity() throws Exception {
        when(formMapper.selectOne(any())).thenReturn(batch(), mo(), mo(), mo(), batch());
        lenient().when(formMapper.selectList(any())).thenReturn(List.of());

        IllegalStateException e = assertThrows(IllegalStateException.class,
                () -> service.issueByBatch("PG-1", "CL001", 100, "王强", ""));
        assertTrue(e.getMessage().contains("批次"), e.getMessage());
    }
}