package com.mes.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.entity.PanelConfig;
import com.mes.mapper.FormApprovalMapper;
import com.mes.mapper.FormDataMapper;
import com.mes.mapper.PanelConfigMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.LocalDateTime;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 部分下单/部分到货（2026-11-05）：同一来源行按剩余量分批下推，不超量、不跨下游面板混用。
 */
@ExtendWith(MockitoExtension.class)
class PxServicePartialPurchaseTest {

    @Mock private PanelConfigMapper panelMapper;
    @Mock private FormDataMapper formMapper;
    @Mock private FormApprovalMapper approvalMapper;
    @Mock private ReportQueryService reportQueryService;

    private final ObjectMapper json = new ObjectMapper();
    private PxService service;

    @BeforeEach
    void setUp() {
        service = new PxService(panelMapper, formMapper, approvalMapper, reportQueryService);
    }

    @Test
    void generatesRemainingOrderQuantityFromRequisition() throws Exception {
        Map<String, Object> reqItem = new HashMap<>();
        reqItem.put("存货编码", "CL001");
        reqItem.put("存货名称", "铝棒");
        reqItem.put("采购单位", "件");
        reqItem.put("数量", 10);
        reqItem.put("单价", 10);
        reqItem.put("金额", 100);
        reqItem.put("税率%", 13);
        reqItem.put("含税单价", 11.3);
        reqItem.put("含税金额", 113);
        FormData requisition = document("PU_REQ", "CG-001", "已审核",
                Map.of("单据编号", "CG-001", "建议供应商", "华东铝业"),
                Map.of("items", List.of(reqItem)));
        FormData existingOrder = document("PU_ORDER", "PO-001", "草稿",
                Map.of("单据编号", "PO-001"),
                Map.of("items", List.of(Map.of(
                        "物料编码", "CL001", "数量", 6,
                        "来源面板", "PU_REQ", "来源单号", "CG-001", "来源行号", 1, "来源数量", 6))));
        when(formMapper.selectOne(any())).thenReturn(requisition);
        when(formMapper.selectList(any())).thenReturn(List.of(existingOrder));
        when(formMapper.selectCount(any())).thenReturn(0L);
        when(panelMapper.selectOne(any())).thenReturn(panelConfig("PU_ORDER", "采购订单"));

        service.callButton("PU_REQ", "生成采购订单", Map.of("编号", "CG-001"), Map.of());

        ArgumentCaptor<FormData> inserted = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).insert(inserted.capture());
        Map<?, ?> detail = json.readValue(inserted.getValue().getDetailData(), Map.class);
        Map<?, ?> row = (Map<?, ?>) ((List<?>) detail.get("items")).get(0);
        assertEquals(4.0, ((Number) row.get("数量")).doubleValue());
        assertEquals(40.0, ((Number) row.get("金额")).doubleValue());
        assertEquals(45.2, ((Number) row.get("含税金额")).doubleValue(), 0.0001);
        assertEquals("CG-001", row.get("来源单号"));
    }

    @Test
    void generatesRemainingArrivalQuantityFromOrder() throws Exception {
        Map<String, Object> orderItem = new HashMap<>();
        orderItem.put("物料编码", "CL001");
        orderItem.put("物料名称", "铝棒");
        orderItem.put("单位", "件");
        orderItem.put("数量", 10);
        orderItem.put("单价", 10);
        orderItem.put("金额", 100);
        orderItem.put("税率%", 13);
        orderItem.put("含税单价", 11.3);
        orderItem.put("含税金额", 113);
        FormData order = document("PU_ORDER", "PO-002", "已审核",
                Map.of("单据编号", "PO-002", "供应商", "华东铝业", "供应商编码", "KH001"),
                Map.of("items", List.of(orderItem)));
        FormData existingArrival = document("ARRIVAL_IN", "DH-001", "草稿",
                Map.of("单据编号", "DH-001"),
                Map.of("items", List.of(Map.of(
                        "存货编码", "CL001", "实收数量", 4,
                        "来源面板", "PU_ORDER", "来源单号", "PO-002", "来源行号", 1, "来源数量", 4))));
        when(formMapper.selectOne(any())).thenReturn(order);
        when(formMapper.selectList(any())).thenReturn(List.of(existingArrival));
        when(formMapper.selectCount(any())).thenReturn(0L);
        when(panelMapper.selectOne(any())).thenReturn(panelConfig("ARRIVAL_IN", "到货单"));

        service.callButton("PU_ORDER", "生成到货单", Map.of("编号", "PO-002"), Map.of());

        ArgumentCaptor<FormData> inserted = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).insert(inserted.capture());
        Map<?, ?> detail = json.readValue(inserted.getValue().getDetailData(), Map.class);
        Map<?, ?> row = (Map<?, ?>) ((List<?>) detail.get("items")).get(0);
        assertEquals(6.0, ((Number) row.get("到货数量")).doubleValue());
        assertEquals(6.0, ((Number) row.get("实收数量")).doubleValue());
        assertEquals(60.0, ((Number) row.get("金额")).doubleValue());
        assertEquals(67.8, ((Number) row.get("含税金额")).doubleValue(), 0.0001);
    }

    /**
     * 采购订单直开预开票后仍可入库：发票只占财务额度，不占订单的履约额度。
     * 回归：删除采购入库单后无法再次生单，实为残留草稿发票被当成「其他下游单据」。
     */
    @Test
    void purchaseInFromOrderIsNotBlockedByDraftInvoice() throws Exception {
        Map<String, Object> orderItem = new HashMap<>();
        orderItem.put("物料编码", "CP005");
        orderItem.put("物料名称", "轴套 C");
        orderItem.put("单位", "件");
        orderItem.put("数量", 11);
        orderItem.put("单价", 1);
        orderItem.put("金额", 11);
        orderItem.put("税率%", 13);
        orderItem.put("含税单价", 1.13);
        orderItem.put("含税金额", 12.43);
        FormData order = document("PU_ORDER", "PO-003", "已审核",
                Map.of("单据编号", "PO-003", "供应商", "西部材料", "供应商编码", "KH003"),
                Map.of("items", List.of(orderItem)));
        FormData invoice = document("PU_INVOICE", "PI-001", "草稿",
                Map.of("单据编号", "PI-001"),
                Map.of("items", List.of(Map.of(
                        "存货编码", "CP005", "数量", 11, "来源数量", 11,
                        "来源面板", "PU_ORDER", "来源单号", "PO-003", "来源行号", 1))));
        when(formMapper.selectOne(any())).thenReturn(order);
        when(formMapper.selectList(any())).thenReturn(List.of(invoice));
        when(formMapper.selectCount(any())).thenReturn(0L);
        when(panelMapper.selectOne(any())).thenReturn(panelConfig("PURCHASE_IN", "采购入库单"));

        service.callButton("PU_ORDER", "生成采购入库单", Map.of("编号", "PO-003"), Map.of());

        ArgumentCaptor<FormData> inserted = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).insert(inserted.capture());
        Map<?, ?> detail = json.readValue(inserted.getValue().getDetailData(), Map.class);
        Map<?, ?> row = (Map<?, ?>) ((List<?>) detail.get("items")).get(0);
        assertEquals(11.0, ((Number) row.get("实收数量")).doubleValue());
        assertEquals("PO-003", row.get("来源单号"));
    }

    /** 到货单与采购入库单仍是互斥的履约路径：已有到货单时不能绕过它直接从订单入库。 */
    @Test
    void purchaseInFromOrderIsStillBlockedByArrival() throws Exception {
        Map<String, Object> orderItem = new HashMap<>();
        orderItem.put("物料编码", "CP005");
        orderItem.put("物料名称", "轴套 C");
        orderItem.put("单位", "件");
        orderItem.put("数量", 11);
        FormData order = document("PU_ORDER", "PO-004", "已审核",
                Map.of("单据编号", "PO-004", "供应商", "西部材料", "供应商编码", "KH003"),
                Map.of("items", List.of(orderItem)));
        FormData arrival = document("ARRIVAL_IN", "DH-001", "草稿",
                Map.of("单据编号", "DH-001"),
                Map.of("items", List.of(Map.of(
                        "存货编码", "CP005", "实收数量", 11,
                        "来源面板", "PU_ORDER", "来源单号", "PO-004", "来源行号", 1))));
        when(formMapper.selectOne(any())).thenReturn(order);
        when(formMapper.selectList(any())).thenReturn(List.of(arrival));

        IllegalStateException error = assertThrows(IllegalStateException.class,
                () -> service.callButton("PU_ORDER", "生成采购入库单", Map.of("编号", "PO-004"), Map.of()));
        assertEquals(true, error.getMessage().contains("来源明细已被其他下游单据选入"));
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

    private PanelConfig panelConfig(String code, String name) throws Exception {
        PanelConfig config = new PanelConfig();
        config.setPanelCode(code);
        config.setPanelName(name);
        config.setCategory("单据");
        config.setConfig(json.writeValueAsString(Map.of(
                "metadata", Map.of(
                        "panelCode", code, "panelName", name, "panelCategory", "单据",
                        "autoCodeField", "单据编号",
                        "panelState", Map.of("dataName", "单据状态", "defaultOptions", List.of("草稿", "已审核")),
                        "buttonGroups", List.of(), "panelButtons", List.of(),
                        "panelPageDto", Map.of(
                                "tablePages", List.of(Map.of(
                                        "tableName", name + "列表",
                                        "queryFields", List.of(),
                                        "gridTabs", List.of(Map.of("label", "明细", "columns", List.of())),
                                        "formPages", List.of())),
                                "formPages", List.of())),
                "dataSchema", Map.of("type", "object", "fields", List.of(
                        Map.of("dataName", "单据日期", "dataType", "日期"),
                        Map.of("dataName", "单据编号", "dataType", "文本", "autoCode", true),
                        Map.of("dataName", "供应商", "dataType", "文本"),
                        Map.of("dataName", "供应商编码", "dataType", "文本"))),
                "detail", Map.of("tabs", List.of(Map.of(
                        "key", "items", "label", "明细",
                        "fields", List.of(
                                Map.of("dataName", "存货编码", "dataType", "文本"),
                                Map.of("dataName", "存货名称", "dataType", "文本"),
                                Map.of("dataName", "数量", "dataType", "小数"),
                                Map.of("dataName", "到货数量", "dataType", "小数"),
                                Map.of("dataName", "实收数量", "dataType", "小数"))))))));
        return config;
    }
}
