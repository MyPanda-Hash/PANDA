package com.mes.service;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
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

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.LinkedHashMap;
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
 * 销售订单的三条生单线互斥（2026-11-13 用户口径）。
 *
 * <p>规则：
 * <ol>
 *   <li>销售订单必须能生成<b>委外加工单</b>（走委外线）；</li>
 *   <li>选了委外加工单并进入审批（草稿/审批中/已审核），就不允许再生成本地生产加工单、销售出库单；</li>
 *   <li>反过来：已生成生产加工单或销售出库单（含草稿/审批中），也不允许再生成委外加工单；</li>
 *   <li>对方单据只要处在草稿、审批中、已审核（未中止/未关闭）都算占用，<b>不允许重复生单</b>。</li>
 * </ol>
 */
@ExtendWith(MockitoExtension.class)
class PxServiceSoGenerateConflictTest {

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

    private FormData document(String panel, String no, String status) throws Exception {
        FormData row = new FormData();
        row.setId(1L);
        row.setPanelCode(panel);
        row.setFormNo(no);
        row.setStatus(status);
        row.setData(json.writeValueAsString(Map.of("单据编号", no, "客户", "华东铝业", "客户编码", "KH001")));
        row.setDetailData(json.writeValueAsString(Map.of("items", List.of(Map.of(
                "存货编码", "CP001", "存货名称", "铝棒 Φ80", "数量", 10, "销售单位", "件")))));
        row.setCreateTime(LocalDateTime.now());
        return row;
    }

    private FormData saleOrder() throws Exception {
        return document("SO_ORDER", "SO-1", "已审核");
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

    // ---------- 线 A：销售订单 → 委外加工单 ----------

    @Test
    @DisplayName("销售订单可以生成委外加工单（草稿），来源单号回填销售订单号")
    void createsOutsourceOrderFromSaleOrder() throws Exception {
        PxService spied = spy(service);
        doReturn(null).when(spied).findOutsourceOfSo(anyString());
        doReturn(null).when(spied).findBlockingManuOrder(anyString());
        doReturn(null).when(spied).findBlockingSaleOut(anyString());
        when(panelMapper.selectOne(any())).thenReturn(config("OUTSOURCE_ORDER"));
        when(formMapper.selectOne(any())).thenReturn(saleOrder());
        when(formMapper.selectCount(any())).thenReturn(0L);

        Map<String, Object> out = spied.callButton("SO_ORDER", "生成委外加工单",
                Map.of("编号", "SO-1"), Map.of());

        ArgumentCaptor<FormData> captor = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).insert(captor.capture());
        FormData created = captor.getValue();
        assertEquals("OUTSOURCE_ORDER", created.getPanelCode());
        assertEquals("草稿", created.getStatus());
        Map<?, ?> head = json.readValue(created.getData(), Map.class);
        assertEquals("SO-1", head.get("来源单号"));
        // 来源单据写面板编码（与 insertGenerated 的其它生单一一致）；检测同时认中文「销售订单」
        assertEquals("SO_ORDER", head.get("来源单据"));
        Map<?, ?> detail = json.readValue(created.getDetailData(), Map.class);
        assertNotNull(detail.get("products"), "委外加工单必须带产成品明细");
        assertEquals("OUTSOURCE_ORDER", out.get("gotoPanel"));
    }

    @Test
    @DisplayName("已有委外加工单（草稿/审批中/已审核）时，销售订单不能重复生成委外加工单")
    void rejectsDuplicateOutsourceOrder() throws Exception {
        PxService spied = spy(service);
        doReturn("审批中").when(spied).findOutsourceOfSo("SO-1");
        when(formMapper.selectOne(any())).thenReturn(saleOrder());

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                spied.callButton("SO_ORDER", "生成委外加工单", Map.of("编号", "SO-1"), Map.of()));

        assertTrue(e.getMessage().contains("委外加工单"), e.getMessage());
        assertTrue(e.getMessage().contains("审批中"), "要带出已有单据状态：" + e.getMessage());
        verify(formMapper, never()).insert(any(FormData.class));
    }

    // ---------- 线 B：委外占用时禁止自制/出库 ----------

    @Test
    @DisplayName("已有委外加工单时，不允许生成生产加工单")
    void rejectsManuOrderWhenOutsourceExists() throws Exception {
        PxService spied = spy(service);
        doReturn("审批中").when(spied).findOutsourceOfSo("SO-1");
        when(formMapper.selectOne(any())).thenReturn(saleOrder());

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                spied.callButton("SO_ORDER", "生成生产加工单", Map.of("编号", "SO-1"), Map.of()));

        String msg = e.getMessage();
        assertTrue(msg.contains("委外加工单"), "要说明已走委外：" + msg);
        assertTrue(msg.contains("不能再生成生产加工单"), "要给出结论：" + msg);
        verify(formMapper, never()).insert(any(FormData.class));
    }

    @Test
    @DisplayName("已有委外加工单时，不允许生成销售出库单")
    void rejectsSaleOutWhenOutsourceExists() throws Exception {
        PxService spied = spy(service);
        doReturn("已审核").when(spied).findOutsourceOfSo("SO-1");
        when(formMapper.selectOne(any())).thenReturn(saleOrder());

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                spied.callButton("SO_ORDER", "生成销售出库单", Map.of("编号", "SO-1"), Map.of()));

        String msg = e.getMessage();
        assertTrue(msg.contains("委外加工单"), "要说明已走委外：" + msg);
        assertTrue(msg.contains("不能再生成销售出库单"), "要给出结论：" + msg);
        verify(formMapper, never()).insert(any(FormData.class));
    }

    // ---------- 线 C：自制/出库占用时禁止委外 ----------

    @Test
    @DisplayName("已有生产加工单（草稿/审批中/已审核）时，不允许生成委外加工单")
    void rejectsOutsourceWhenManuOrderExists() throws Exception {
        PxService spied = spy(service);
        doReturn(null).when(spied).findOutsourceOfSo(anyString());
        doReturn("草稿").when(spied).findBlockingManuOrder("SO-1");
        when(formMapper.selectOne(any())).thenReturn(saleOrder());

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                spied.callButton("SO_ORDER", "生成委外加工单", Map.of("编号", "SO-1"), Map.of()));

        String msg = e.getMessage();
        assertTrue(msg.contains("生产加工单"), "要说明已有自制加工：" + msg);
        assertTrue(msg.contains("草稿"), "要带出状态：" + msg);
        verify(formMapper, never()).insert(any(FormData.class));
    }

    @Test
    @DisplayName("已有销售出库单（草稿/审批中/已审核）时，不允许生成委外加工单")
    void rejectsOutsourceWhenSaleOutExists() throws Exception {
        PxService spied = spy(service);
        doReturn(null).when(spied).findOutsourceOfSo(anyString());
        doReturn(null).when(spied).findBlockingManuOrder(anyString());
        doReturn("审批中").when(spied).findBlockingSaleOut("SO-1");
        when(formMapper.selectOne(any())).thenReturn(saleOrder());

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                spied.callButton("SO_ORDER", "生成委外加工单", Map.of("编号", "SO-1"), Map.of()));

        String msg = e.getMessage();
        assertTrue(msg.contains("销售出库单"), "要说明已有出库：" + msg);
        verify(formMapper, never()).insert(any(FormData.class));
    }

    // ---------- 门限：只认未完结状态 ----------

    @Test
    @DisplayName("生产加工单/销售出库单只看草稿、审批中、已审核；中止、已关闭不算占用")
    void blockingStatusesExcludeTerminal() {
        assertTrue(PxService.isActiveDownstream("草稿"));
        assertTrue(PxService.isActiveDownstream("审批中"));
        assertTrue(PxService.isActiveDownstream("已审核"));
        assertTrue(PxService.isActiveDownstream("已完工"), "已完工仍然占用了这条线");
        assertTrue(!PxService.isActiveDownstream("已中止"));
        assertTrue(!PxService.isActiveDownstream("中止"));
        assertTrue(!PxService.isActiveDownstream("已关闭"));
        assertTrue(!PxService.isActiveDownstream(""));
        assertTrue(!PxService.isActiveDownstream(null));
    }

    @Test
    @DisplayName("委外加工单被驳回回到草稿后，仍然算占用（要编辑那张单，不另开一张）")
    void rejectedOutsourceStillBlocks() throws Exception {
        PxService spied = spy(service);
        doReturn("草稿").when(spied).findOutsourceOfSo("SO-1");
        when(formMapper.selectOne(any())).thenReturn(saleOrder());

        assertThrows(IllegalStateException.class, () ->
                spied.callButton("SO_ORDER", "生成委外加工单", Map.of("编号", "SO-1"), Map.of()));
    }
}
