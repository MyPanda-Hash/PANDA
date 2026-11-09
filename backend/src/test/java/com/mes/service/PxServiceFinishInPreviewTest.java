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
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.doReturn;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.spy;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 产成品入库的入库预览：可选入库仓库、可按剩余量分次入库。
 *
 * <p>修复前：{@code createFinishInFromMo} 把仓库写死「成品仓」，且整单照抄加工单数量，
 * 做不出「先入一部分」和「入到半成品仓/不良品仓」这两种真实场景。
 */
@ExtendWith(MockitoExtension.class)
class PxServiceFinishInPreviewTest {

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

    /** 产成品：计划 100，已入库 30。 */
    private FormData mo() throws Exception {
        return document("MANU_ORDER", "MO-1", "已审核",
                Map.of("单据编号", "MO-1", "生产车间", "熔铸车间"),
                Map.of("products", List.of(Map.of(
                        "产品编码", "CP001", "产品名称", "铝棒 Φ80", "规格型号", "Φ80×3000",
                        "生产单位", "件", "数量", 100, "累计入库数量", 30))));
    }

    @Test
    void previewShowsRemainingToReceive() throws Exception {
        when(formMapper.selectOne(any())).thenReturn(mo());

        List<Map<String, Object>> rows = service.finishInPreview("MO-1");

        assertEquals(1, rows.size());
        Map<String, Object> r = rows.get(0);
        assertEquals("CP001", r.get("产品编码"));
        assertEquals(100.0, ((Number) r.get("计划数量")).doubleValue(), 0.001);
        assertEquals(30.0, ((Number) r.get("已入库数量")).doubleValue(), 0.001);
        assertEquals(70.0, ((Number) r.get("剩余入库数量")).doubleValue(), 0.001);
        assertEquals(70.0, ((Number) r.get("建议入库数量")).doubleValue(), 0.001);
    }

    /** 已有未完结入库单时拦住重复生成（草稿不写库存流水，多张草稿会超量入库）。 */
    @Test
    void rejectsWhenOpenFinishInExists() throws Exception {
        PxService spied = spy(service);
        doReturn("草稿").when(spied).findOpenFinishIn("MO-1");
        when(formMapper.selectOne(any())).thenReturn(mo());

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                spied.callButton("MANU_ORDER", "生成产成品入库单", Map.of("编号", "MO-1"), Map.of()));

        assertTrue(e.getMessage().contains("已有产成品入库单"), e.getMessage());
        verify(formMapper, never()).insert(any(FormData.class));
    }

    @Test
    void createsFinishInWithSelectedWarehouseAndQuantity() throws Exception {
        PxService spied = spy(service);
        doReturn(null).when(spied).findOpenFinishIn(anyString());
        when(panelMapper.selectOne(any())).thenReturn(config("FINISH_IN"));
        when(formMapper.selectOne(any())).thenReturn(mo());
        when(formMapper.selectCount(any())).thenReturn(0L);

        spied.callButton("MANU_ORDER", "生成产成品入库单",
                Map.of("编号", "MO-1"),
                Map.of("warehouse", "半成品仓", "lines", List.of(Map.of("存货编码", "CP001", "数量", 20))));

        FormData target = captureInserted();
        Map<?, ?> detail = json.readValue(target.getDetailData(), Map.class);
        Map<?, ?> row = (Map<?, ?>) ((List<?>) detail.get("items")).get(0);
        assertEquals("半成品仓", row.get("仓库"), "入库仓库应按所选落库");
        assertEquals(20.0, ((Number) row.get("实收数量")).doubleValue(), 0.001, "数量按用户填的");
    }

    /** 客户与订单上下文必须随产成品入库单带下去，否则销货单客户为空、应收挂不上。 */
    @Test
    void finishInCarriesCustomerAndOrderContext() throws Exception {
        when(panelMapper.selectOne(any())).thenReturn(config("FINISH_IN"));
        FormData withCustomer = document("MANU_ORDER", "MO-3", "已审核",
                Map.of("单据编号", "MO-3", "客户", "华东铝业", "客户编码", "KH001",
                        "结算客户", "华东铝业", "销售订单号", "SO-9", "生产车间", "熔铸车间"),
                Map.of("products", List.of(Map.of(
                        "产品编码", "CP001", "产品名称", "铝棒 Φ80", "数量", 10, "累计入库数量", 0))));
        when(formMapper.selectOne(any())).thenReturn(withCustomer);
        when(formMapper.selectCount(any())).thenReturn(0L);
        PxService spied = spy(service);
        doReturn(null).when(spied).findOpenFinishIn(anyString());

        spied.callButton("MANU_ORDER", "生成产成品入库单", Map.of("编号", "MO-3"), Map.of());

        FormData target = captureInserted();
        Map<?, ?> head = json.readValue(target.getData(), Map.class);
        assertEquals("华东铝业", head.get("客户"));
        assertEquals("KH001", head.get("客户编码"));
        assertEquals("SO-9", head.get("销售订单号"));
    }

    @Test
    void defaultsToFinishedGoodsWarehouseWhenNotSpecified() throws Exception {
        PxService spied = spy(service);
        doReturn(null).when(spied).findOpenFinishIn(anyString());
        when(panelMapper.selectOne(any())).thenReturn(config("FINISH_IN"));
        when(formMapper.selectOne(any())).thenReturn(mo());
        when(formMapper.selectCount(any())).thenReturn(0L);

        spied.callButton("MANU_ORDER", "生成产成品入库单", Map.of("编号", "MO-1"), Map.of());

        FormData target = captureInserted();
        Map<?, ?> detail = json.readValue(target.getDetailData(), Map.class);
        Map<?, ?> row = (Map<?, ?>) ((List<?>) detail.get("items")).get(0);
        assertEquals("成品仓", row.get("仓库"));
        assertEquals(70.0, ((Number) row.get("实收数量")).doubleValue(), 0.001, "默认只入剩余量");
    }

    @Test
    void rejectsWhenAlreadyFullyReceived() throws Exception {
        FormData full = document("MANU_ORDER", "MO-2", "已审核",
                Map.of("单据编号", "MO-2"),
                Map.of("products", List.of(Map.of(
                        "产品编码", "CP001", "产品名称", "铝棒 Φ80", "数量", 100, "累计入库数量", 100))));
        when(formMapper.selectOne(any())).thenReturn(full);

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                service.callButton("MANU_ORDER", "生成产成品入库单", Map.of("编号", "MO-2"), Map.of()));

        assertTrue(e.getMessage().contains("已全部入库"), e.getMessage());
        verify(formMapper, never()).insert(any(FormData.class));
    }


    /** 首件报检单没审核不能入库：首检是质量门，不能只当一张纸。 */
    @Test
    void rejectsReceivingUntilFirstInspectApproved() throws Exception {
        PxService spied = spy(service);
        when(formMapper.selectOne(any())).thenReturn(mo());
        when(formMapper.selectList(any())).thenReturn(List.of(
                document("FIRST_INSPECT", "SJ-1", "草稿", Map.of("加工单号", "MO-1"),
                        Map.of("items", List.of(Map.of("存货编码", "CP001", "报检数量", 70))))));

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                spied.callButton("MANU_ORDER", "生成产成品入库单", Map.of("编号", "MO-1"), Map.of()));

        assertTrue(e.getMessage().contains("首检"), e.getMessage());
        verify(formMapper, never()).insert(any(FormData.class));
    }

    private FormData captureInserted() {
        ArgumentCaptor<FormData> captor = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).insert(captor.capture());
        return captor.getValue();
    }
}