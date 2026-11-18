package com.mes.service;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.doReturn;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.spy;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.entity.PanelConfig;
import com.mes.mapper.FormApprovalMapper;
import com.mes.mapper.FormDataMapper;
import com.mes.mapper.PanelConfigMapper;
import com.mes.panel.PanelActionRegistry;

/**
 * 委外回厂检验走「委外检验单」（2026-11-13 用户口径），不再用首件报检：
 *
 * <ol>
 *   <li>委外加工单 → 生成委外检验单（草稿）；</li>
 *   <li>委外检验单审核且判定合格/让步接收后，才能生成委外入库单；</li>
 *   <li>没有检验单、检验中、判定不合格，都不许入库，且提示要指向那张单。</li>
 * </ol>
 */
@ExtendWith(MockitoExtension.class)
class PxServiceOutsourceInspectionTest {

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

    private FormData outsourceOrder() throws Exception {
        FormData row = new FormData();
        row.setId(1L);
        row.setPanelCode("OUTSOURCE_ORDER");
        row.setFormNo("WW-1");
        row.setStatus("已审核");
        row.setData(json.writeValueAsString(Map.of(
                "单据编号", "WW-1", "委外供应商", "华东热处理厂", "单据日期", "2026-11-13")));
        row.setDetailData(json.writeValueAsString(Map.of(
                "products", List.of(Map.of(
                        "产品编码", "CP001", "产品名称", "铝棒 Φ80", "规格型号", "Φ80×3000",
                        "计量单位", "件", "数量", 10, "委外单价", 6.5)),
                "materials", List.of())));
        row.setCreateTime(LocalDateTime.now());
        return row;
    }

    private FormData inspection(String status, String verdict) throws Exception {
        FormData row = new FormData();
        row.setId(2L);
        row.setPanelCode("OUTSOURCE_INSPECTION");
        row.setFormNo("WJ-1");
        row.setStatus(status);
        row.setData(json.writeValueAsString(Map.of(
                "单据编号", "WJ-1", "业务类型", "委外检验", "委外加工单号", "WW-1",
                "委外供应商", "华东热处理厂")));
        Map<String, Object> item = new java.util.LinkedHashMap<>();
        item.put("产品编码", "CP001");
        item.put("产品名称", "铝棒 Φ80");
        item.put("规格型号", "Φ80×3000");
        item.put("计量单位", "件");
        item.put("报检数量", 10);
        item.put("检验数量", 10);
        item.put("合格数量", "合格".equals(verdict) ? 10 : 0);
        item.put("不合格数量", "合格".equals(verdict) ? 0 : 10);
        item.put("检验结果判定", verdict);
        item.put("处置方式", "让步接收".equals(verdict) ? "让步接收" : "原价");
        item.put("委外单价", 6.5);
        row.setDetailData(json.writeValueAsString(Map.of("items", List.of(item))));
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

    // ---------- ① 委外加工单 → 委外检验单 ----------

    @Test
    @DisplayName("生成委外检验单：按产成品明细带出报检行，来源指向委外加工单")
    void createsInspectionFromOutsourceOrder() throws Exception {
        PxService spied = spy(service);
        doReturn(null).when(spied).findOutsourceInspection(anyString());
        when(panelMapper.selectOne(any())).thenReturn(config("OUTSOURCE_INSPECTION"));
        when(formMapper.selectOne(any())).thenReturn(outsourceOrder());
        when(formMapper.selectCount(any())).thenReturn(0L);

        Map<String, Object> out = spied.callButton("OUTSOURCE_ORDER", "生成委外检验单",
                Map.of("编号", "WW-1"), Map.of());

        ArgumentCaptor<FormData> captor = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).insert(captor.capture());
        FormData created = captor.getValue();
        assertEquals("OUTSOURCE_INSPECTION", created.getPanelCode());
        assertEquals("草稿", created.getStatus());

        Map<?, ?> head = json.readValue(created.getData(), Map.class);
        assertEquals("WW-1", head.get("来源单号"));
        assertEquals("委外检验", head.get("业务类型"));
        assertEquals("WW-1", head.get("委外加工单号"));

        Map<?, ?> detail = json.readValue(created.getDetailData(), Map.class);
        List<?> items = (List<?>) detail.get("items");
        assertEquals(1, items.size(), "一张产成品一行报检");
        Map<?, ?> row = (Map<?, ?>) items.get(0);
        assertEquals("CP001", row.get("产品编码"));
        assertEquals(10.0, ((Number) row.get("报检数量")).doubleValue(), 0.001);
        assertEquals(10.0, ((Number) row.get("检验数量")).doubleValue(), 0.001, "默认按报检数量全检");
        assertEquals("OUTSOURCE_INSPECTION", out.get("gotoPanel"));
    }

    @Test
    @DisplayName("已有未废弃的委外检验单时不能重复生成")
    void rejectsDuplicateInspection() throws Exception {
        PxService spied = spy(service);
        doReturn(inspection("草稿", "")).when(spied).findOutsourceInspection("WW-1");
        when(formMapper.selectOne(any())).thenReturn(outsourceOrder());

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                spied.callButton("OUTSOURCE_ORDER", "生成委外检验单", Map.of("编号", "WW-1"), Map.of()));

        assertTrue(e.getMessage().contains("WJ-1"), "要带出已有检验单号：" + e.getMessage());
        verify(formMapper, never()).insert(any(FormData.class));
    }

    // ---------- ② 检验合格才能入库 ----------

    @Test
    @DisplayName("没有委外检验单时不能生成委外入库单")
    void rejectsInboundWithoutInspection() throws Exception {
        PxService spied = spy(service);
        doReturn(null).when(spied).findOutsourceInspection("WW-1");
        when(formMapper.selectOne(any())).thenReturn(outsourceOrder());

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                spied.callButton("OUTSOURCE_ORDER", "生成委外入库单", Map.of("编号", "WW-1"), Map.of()));

        String msg = e.getMessage();
        assertTrue(msg.contains("委外检验单"), "提示要指向委外检验单：" + msg);
        assertTrue(msg.contains("生成委外检验单"), "要告诉用户下一步怎么做：" + msg);
        verify(formMapper, never()).insert(any(FormData.class));
    }

    @Test
    @DisplayName("委外检验单还是草稿/审批中时不能入库")
    void rejectsInboundWhileInspectionPending() throws Exception {
        PxService spied = spy(service);
        doReturn(inspection("审批中", "合格")).when(spied).findOutsourceInspection("WW-1");
        when(formMapper.selectOne(any())).thenReturn(outsourceOrder());

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                spied.callButton("OUTSOURCE_ORDER", "生成委外入库单", Map.of("编号", "WW-1"), Map.of()));

        assertTrue(e.getMessage().contains("审批中"), e.getMessage());
        verify(formMapper, never()).insert(any(FormData.class));
    }

    @Test
    @DisplayName("委外检验判定不合格时不能入库")
    void rejectsInboundWhenInspectionFailed() throws Exception {
        PxService spied = spy(service);
        doReturn(inspection("已审核", "不合格")).when(spied).findOutsourceInspection("WW-1");
        when(formMapper.selectOne(any())).thenReturn(outsourceOrder());

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                spied.callButton("OUTSOURCE_ORDER", "生成委外入库单", Map.of("编号", "WW-1"), Map.of()));

        assertTrue(e.getMessage().contains("不合格"), e.getMessage());
        verify(formMapper, never()).insert(any(FormData.class));
    }

    @Test
    @DisplayName("委外检验已审核且判定合格 → 允许生成委外入库单")
    void allowsInboundWhenInspectionPassed() throws Exception {
        PxService spied = spy(service);
        doReturn(inspection("已审核", "合格")).when(spied).findOutsourceInspection("WW-1");
        when(panelMapper.selectOne(any())).thenReturn(config("OUTSOURCE_IN"));
        when(formMapper.selectOne(any())).thenReturn(outsourceOrder());
        when(formMapper.selectCount(any())).thenReturn(0L);

        spied.callButton("OUTSOURCE_ORDER", "生成委外入库单", Map.of("编号", "WW-1"), Map.of());

        ArgumentCaptor<FormData> captor = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).insert(captor.capture());
        assertEquals("OUTSOURCE_IN", captor.getValue().getPanelCode());
        Map<?, ?> head = json.readValue(captor.getValue().getData(), Map.class);
        assertEquals("WJ-1", head.get("检验单号"), "入库单要记住是哪张检验单放行的");
    }

    // ---------- ③ 委外检验单 → 委外入库单 ----------

    @Test
    @DisplayName("从委外检验单生成委外入库单：只入合格数量，单价取委外单价")
    void createsInboundFromInspection() throws Exception {
        PxService spied = spy(service);
        FormData insp = inspection("已审核", "合格");
        when(formMapper.selectOne(any())).thenReturn(insp);
        when(panelMapper.selectOne(any())).thenReturn(config("OUTSOURCE_IN"));
        when(formMapper.selectCount(any())).thenReturn(0L);

        spied.callButton("OUTSOURCE_INSPECTION", "生成委外入库单", Map.of("编号", "WJ-1"), Map.of());

        ArgumentCaptor<FormData> captor = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).insert(captor.capture());
        FormData created = captor.getValue();
        assertEquals("OUTSOURCE_IN", created.getPanelCode());
        Map<?, ?> detail = json.readValue(created.getDetailData(), Map.class);
        List<?> items = (List<?>) detail.get("items");
        assertEquals(1, items.size());
        Map<?, ?> row = (Map<?, ?>) items.get(0);
        assertEquals(10.0, ((Number) row.get("实收数量")).doubleValue(), 0.001);
        assertEquals(6.5, ((Number) row.get("单价")).doubleValue(), 0.001, "价来自检验单的委外单价");
    }

    @Test
    @DisplayName("委外检验单只能转委外入库，不能转采购入库/产成品入库")
    void inspectionOnlyGoesToOutsourceIn() throws Exception {
        for (String target : List.of("生成采购入库单", "生成产成品入库单")) {
            PxService spied = spy(service);
            when(formMapper.selectOne(any())).thenReturn(inspection("已审核", "合格"));
            IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                    spied.callButton("OUTSOURCE_INSPECTION", target, Map.of("编号", "WJ-1"), Map.of()),
                    target + " 必须被拦");
            assertTrue(e.getMessage().contains("委外入库"), target + " 的提示要指向委外入库：" + e.getMessage());
        }
    }

    @Test
    @DisplayName("非委外加工单不能生成委外检验单")
    void onlyOutsourceOrderCanGenerateInspection() {
        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                service.callButton("PU_ORDER", "生成委外检验单", Map.of("编号", "PO-1"), Map.of()));
        assertTrue(e.getMessage().contains("委外加工单"), e.getMessage());
    }

    @Test
    @DisplayName("检验结论没填完不许审批通过（避免空白检验单被审掉后卡住入库）")
    void rejectsApprovalWithoutVerdict() throws Exception {
        PxService spied = spy(service);
        FormData blank = inspection("审批中", "");
        when(formMapper.selectOne(any())).thenReturn(blank);
        com.mes.entity.FormApproval pending = new com.mes.entity.FormApproval();
        pending.setPanelCode("OUTSOURCE_INSPECTION");
        pending.setFormNo("WJ-1");
        pending.setAction("SUBMIT");
        pending.setResult("PENDING");
        when(approvalMapper.selectOne(any())).thenReturn(pending);

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                spied.callButton("OUTSOURCE_INSPECTION", "审批通过", Map.of("编号", "WJ-1"), Map.of()));

        assertTrue(e.getMessage().contains("检验结果"), e.getMessage());
    }

    @Test
    @DisplayName("findOutsourceInspection 对空单号返回 null")
    void nullDocNoReturnsNull() {
        assertNull(service.findOutsourceInspection(""));
        assertNull(service.findOutsourceInspection(null));
    }

    @Test
    @DisplayName("委外检验单是审批面板")
    void inspectionIsApprovalPanel() {
        assertNotNull(PxService.APPROVAL_PANELS_VIEW);
        assertTrue(PxService.APPROVAL_PANELS_VIEW.contains("OUTSOURCE_INSPECTION"),
                "委外检验单要走审批，否则没有「审批通过」可点");
    }
}
