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
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 混装到货单（免检 + 需检）：
 * 生成检验单只送需检行；免检行仍可从到货单直接入库，已由检验单接手的行不再重复入库。
 */
@ExtendWith(MockitoExtension.class)
class PxServiceArrivalMixedInspectionTest {

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
    void generatesInspectionOnlyForNonExemptLines() throws Exception {
        FormData arrival = arrival(Map.of("items", List.of(
                row("CL001", "铝棒", 10, "免检", 100),
                row("CL002", "铝板", 10, "抽检", 50))));
        when(formMapper.selectOne(any())).thenReturn(arrival);
        when(formMapper.selectList(any())).thenReturn(List.of());
        when(formMapper.selectCount(any())).thenReturn(0L);
        when(panelMapper.selectOne(any())).thenReturn(targetConfig("INSPECTION", "来料/成品检验单"));

        service.callButton("ARRIVAL_IN", "生成检验单", Map.of("编号", "DH-001"), Map.of());

        ArgumentCaptor<FormData> inserted = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).insert(inserted.capture());
        Map<?, ?> detail = json.readValue(inserted.getValue().getDetailData(), Map.class);
        List<?> items = (List<?>) detail.get("items");
        assertEquals(1, items.size(), "免检行不应生成检验明细");
        Map<?, ?> line = (Map<?, ?>) items.get(0);
        assertEquals("CL002", line.get("存货编码"));
        assertEquals(2, line.get("来源行号"), "来源行号仍指向到货单原始行");
        assertEquals("抽检", line.get("检验方式"));
        assertEquals(50.0, ((Number) line.get("抽检比例%")).doubleValue(), 0.0001);
        assertEquals(10.0, ((Number) line.get("检验数量")).doubleValue(), 0.0001);
    }

    @Test
    void rejectsInspectionWhenAllLinesExempt() throws Exception {
        FormData arrival = arrival(Map.of("items", List.of(
                row("CL001", "铝棒", 10, "免检", 100))));
        when(formMapper.selectOne(any())).thenReturn(arrival);

        IllegalStateException error = assertThrows(IllegalStateException.class,
                () -> service.callButton("ARRIVAL_IN", "生成检验单",
                        Map.of("编号", "DH-001"), Map.of()));

        assertTrue(error.getMessage().contains("均为免检物料"));
    }

    @Test
    void directInboundSkipsLinesAlreadyUnderInspection() throws Exception {
        FormData arrival = arrival(Map.of("items", List.of(
                row("CL001", "铝棒", 10, "免检", 100),
                row("CL002", "铝板", 20, "全检", 100))));
        FormData inspection = document("INSPECTION", "JY-001", "已审核",
                Map.of("单据编号", "JY-001"),
                Map.of("items", List.of(Map.of(
                        "存货编码", "CL002", "检验数量", 20,
                        "来源面板", "ARRIVAL_IN", "来源单号", "DH-001", "来源行号", 2))));
        when(formMapper.selectOne(any())).thenReturn(arrival);
        when(formMapper.selectList(any())).thenReturn(List.of(inspection));
        when(formMapper.selectCount(any())).thenReturn(0L);
        when(panelMapper.selectOne(any())).thenReturn(targetConfig("PURCHASE_IN", "采购入库单"));

        service.callButton("ARRIVAL_IN", "生成采购入库单", Map.of("编号", "DH-001"), Map.of());

        ArgumentCaptor<FormData> inserted = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).insert(inserted.capture());
        Map<?, ?> detail = json.readValue(inserted.getValue().getDetailData(), Map.class);
        List<?> items = (List<?>) detail.get("items");
        assertEquals(1, items.size(), "已检验行不应重复入库");
        assertEquals("CL001", ((Map<?, ?>) items.get(0)).get("存货编码"));
        Map<?, ?> head = json.readValue(inserted.getValue().getData(), Map.class);
        assertEquals("DH-001", head.get("到货单号"));
        assertEquals("PO-001", head.get("采购订单号"));
    }

    private FormData arrival(Map<String, Object> detail) throws Exception {
        return document("ARRIVAL_IN", "DH-001", "已审核",
                Map.of("单据编号", "DH-001", "供应商", "华东铝业", "供应商编码", "KH001",
                        "仓库", "原料仓", "采购订单号", "PO-001"),
                detail);
    }

    private Map<String, Object> row(String code, String name, double quantity,
                                    String method, double ratio) {
        Map<String, Object> row = new java.util.HashMap<>();
        row.put("存货编码", code);
        row.put("存货名称", name);
        row.put("仓库", "原料仓");
        row.put("采购单位", "件");
        row.put("到货数量", quantity);
        row.put("实收数量", quantity);
        row.put("检验方式", method);
        row.put("抽检比例%", ratio);
        row.put("单价", 10);
        row.put("含税单价", 11.3);
        row.put("税率%", 13);
        row.put("金额", quantity * 10);
        row.put("税额", quantity * 1.3);
        row.put("含税金额", quantity * 11.3);
        return row;
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

    private PanelConfig targetConfig(String code, String name) throws Exception {
        PanelConfig config = new PanelConfig();
        config.setPanelCode(code);
        config.setPanelName(name);
        config.setCategory("单据");
        config.setConfig(json.writeValueAsString(Map.of(
                "metadata", Map.of(
                        "panelCode", code, "panelName", name, "panelCategory", "单据",
                        "autoCodeField", "单据编号",
                        "panelState", Map.of("dataName", "单据状态"),
                        "buttonGroups", List.of(), "panelButtons", List.of(),
                        "panelPageDto", Map.of("tablePages", List.of(), "formPages", List.of())),
                "dataSchema", Map.of("type", "object", "fields", List.of(
                        Map.of("dataName", "单据日期", "dataType", "日期"),
                        Map.of("dataName", "单据编号", "dataType", "文本", "autoCode", true),
                        Map.of("dataName", "供应商", "dataType", "文本"),
                        Map.of("dataName", "供应商编码", "dataType", "文本"),
                        Map.of("dataName", "仓库", "dataType", "文本"),
                        Map.of("dataName", "采购订单号", "dataType", "文本"),
                        Map.of("dataName", "到货单号", "dataType", "文本"))),
                "detail", Map.of("tabs", List.of(Map.of("key", "items", "fields", List.of()))))));
        return config;
    }
}
