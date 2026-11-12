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
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyDouble;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.doNothing;
import static org.mockito.Mockito.spy;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 按批次领料：一个排产批次的多种材料要落在一张材料出库单里（多行）。
 *
 * <p>修复前每次只领一种材料，20 件的一批料被拆成 3 张出库单，审批和仓库对账都得点三次。
 */
@ExtendWith(MockitoExtension.class)
class PxServiceBatchIssueDocTest {

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

    private FormData doc(String panel, String no, String status, Map<String, Object> head, Map<String, Object> detail)
            throws Exception {
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

    private PanelConfig config(String code) {
        PanelConfig cfg = new PanelConfig();
        cfg.setPanelCode(code);
        cfg.setPanelName(code);
        cfg.setConfig("{\"metadata\":{\"panelCode\":\"" + code
                + "\",\"autoCodeField\":\"单据编号\",\"panelButtons\":[],\"buttonGroups\":[],\"panelPageDto\":{}},"
                + "\"dataSchema\":{\"fields\":[]},\"detail\":{\"tabs\":[]}}");
        return cfg;
    }

    private FormData batch() throws Exception {
        return doc("DISPATCH", "PG-1", "已审核",
                Map.of("单据编号", "PG-1", "加工单号", "MO-1", "生产车间", "熔铸车间"),
                Map.of("items", List.of(Map.of("工序编码", "PX001", "工序名称", "下料", "派工数量", 20))));
    }

    private FormData mo() throws Exception {
        return doc("MANU_ORDER", "MO-1", "已审核",
                Map.of("单据编号", "MO-1"),
                Map.of("products", List.of(Map.of("产品编码", "CP004", "数量", 20)),
                        "processes", List.of(),
                        "materials", List.of(
                                Map.of("材料编码", "CL001", "材料名称", "45#圆钢", "计量单位", "kg",
                                        "计划数量", 882, "预出仓库", "原料仓", "单价", 6.5),
                                Map.of("材料编码", "CL004", "材料名称", "切削液", "计量单位", "升",
                                        "计划数量", 10.2, "预出仓库", "辅料仓", "单价", 12.64),
                                Map.of("材料编码", "CL005", "材料名称", "包装木箱", "计量单位", "件",
                                        "计划数量", 10, "预出仓库", "原料仓", "单价", 36.08))));
    }

    @Test
    void oneBatchProducesOneDocumentWithAllMaterialLines() throws Exception {
        PxService spied = spy(service);
        doNothing().when(spied).validateBatchIssue(anyString(), anyString(), anyString(), anyDouble(), any());
        doNothing().when(spied).validateMaterialIssue(any(), any());
        doNothing().when(spied).submitIssueForApproval(anyString(), any());
        when(panelMapper.selectOne(any())).thenReturn(config("MATERIAL_OUT"));
        when(formMapper.selectOne(any())).thenReturn(batch(), mo());
        when(formMapper.selectCount(any())).thenReturn(0L);

        spied.issueBatch("PG-1",
                List.of(Map.of("材料编码", "CL001", "数量", 882),
                        Map.of("材料编码", "CL004", "数量", 10.2),
                        Map.of("材料编码", "CL005", "数量", 10)),
                "王强", "");

        ArgumentCaptor<FormData> captor = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).insert(captor.capture());
        FormData inserted = captor.getValue();
        assertEquals("MATERIAL_OUT", inserted.getPanelCode());

        Map<?, ?> detail = json.readValue(inserted.getDetailData(), Map.class);
        List<?> rows = (List<?>) detail.get("items");
        assertEquals(3, rows.size(), "三种材料应在一张出库单里");
        Map<?, ?> first = (Map<?, ?>) rows.get(0);
        assertEquals("CL001", first.get("材料编码"));
        assertEquals(882.0, ((Number) first.get("数量")).doubleValue(), 0.001);
        assertEquals("王强", first.get("领料人"));
        Map<?, ?> second = (Map<?, ?>) rows.get(1);
        assertEquals("CL004", second.get("材料编码"));
        assertEquals("辅料仓", second.get("仓库"), "每种材料按自己的预出库仓出库");

        Map<?, ?> head = json.readValue(inserted.getData(), Map.class);
        assertEquals("王强", head.get("领用人"));
        assertEquals("PG-1", first.get("排产单号"), "每行都挂到该排产批次");
    }

    /** 同一批次已经有「审批中」的出库单：不许再领，避免同批重复出库。 */
    @Test
    void issueRejectedWhilePreviousIssueIsPending() throws Exception {
        when(formMapper.selectOne(any())).thenReturn(batch());
        FormData pending = doc("MATERIAL_OUT", "CL-1", "审批中",
                Map.of("单据编号", "CL-1", "来源单号", "PG-1"),
                Map.of("items", List.of(Map.of("材料编码", "CL001", "数量", 1, "排产单号", "PG-1"))));
        when(formMapper.selectList(any())).thenReturn(List.of(pending));

        IllegalStateException e = assertThrows(IllegalStateException.class,
                () -> service.issueBatch("PG-1", List.of(Map.of("材料编码", "CL001", "数量", 1)), "王强", ""));
        assertTrue(e.getMessage().contains("CL-1"), e.getMessage());
        assertTrue(e.getMessage().contains("审批中"), e.getMessage());
    }

    /** 上一张被驳回（退回草稿）：允许重新领料。 */
    @Test
    void issueAllowedAfterPreviousIssueRejected() throws Exception {
        PxService spied = spy(service);
        doNothing().when(spied).validateBatchIssue(anyString(), anyString(), anyString(), anyDouble(), any());
        doNothing().when(spied).validateMaterialIssue(any(), any());
        doNothing().when(spied).submitIssueForApproval(anyString(), any());
        when(panelMapper.selectOne(any())).thenReturn(config("MATERIAL_OUT"));
        FormData rejected = doc("MATERIAL_OUT", "CL-1", "草稿",
                Map.of("单据编号", "CL-1", "来源单号", "PG-1"),
                Map.of("items", List.of(Map.of("材料编码", "CL001", "数量", 1, "排产单号", "PG-1"))));
        when(formMapper.selectOne(any())).thenReturn(batch(), mo());
        when(formMapper.selectCount(any())).thenReturn(0L);
        when(formMapper.selectList(any())).thenReturn(List.of(rejected));

        spied.issueBatch("PG-1", List.of(Map.of("材料编码", "CL001", "数量", 1)), "王强", "");
        verify(formMapper).insert(any(FormData.class));
    }

    /** 领料看板带出批次领料单状态：审批中 → 「待审批」，并锁住该批次的领料按钮。 */
    @Test
    void issueBoardMarksPendingBatchAndBlocksIssue() throws Exception {
        FormData pending = doc("MATERIAL_OUT", "CL-1", "审批中",
                Map.of("单据编号", "CL-1", "来源单号", "PG-1"),
                Map.of("items", List.of(Map.of("加工单号", "MO-1", "材料编码", "CL001",
                        "数量", 0, "排产单号", "PG-1"))));
        when(formMapper.selectOne(any())).thenReturn(mo());
        when(formMapper.selectList(any())).thenReturn(List.of(pending), List.of(batch()));

        List<Map<String, Object>> rows = service.materialIssueBoard("MO-1");
        assertFalse(rows.isEmpty());
        Map<String, Object> first = rows.get(0);
        assertEquals("CL-1", first.get("出库单号"));
        assertEquals("待审批", first.get("出库状态"));
        assertEquals(false, first.get("可否领料"), "审批中的批次不能再领");
    }
}