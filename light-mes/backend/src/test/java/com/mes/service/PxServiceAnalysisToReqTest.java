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

/** 采购需求分析 → 请购单：建议请购数量带入草稿，必填字段齐全。 */
@ExtendWith(MockitoExtension.class)
class PxServiceAnalysisToReqTest {

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
    void generatesPurchaseReqFromAnalysisSuggestions() throws Exception {
        FormData mo = document("MANU_ORDER", "MO-001", "已审核",
                Map.of("单据编号", "MO-001"),
                Map.of("materials", List.of(Map.of(
                        "材料编码", "CL001", "材料名称", "铝棒", "规格型号", "80mm",
                        "计量单位", "kg", "计划数量", 100, "现存量", 0))));
        when(formMapper.selectList(any())).thenReturn(List.of(mo));
        when(formMapper.selectCount(any())).thenReturn(0L);
        when(panelMapper.selectOne(any())).thenReturn(puReqConfig());

        Map<String, Object> result = service.callButton("PU_REQ_ANALYSIS", "生成请购单", Map.of(), Map.of());

        assertEquals("PU_REQ", result.get("gotoPanel"));
        ArgumentCaptor<FormData> inserted = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).insert(inserted.capture());
        FormData target = inserted.getValue();
        assertEquals("PU_REQ", target.getPanelCode());
        assertEquals("草稿", target.getStatus());

        Map<?, ?> head = json.readValue(target.getData(), Map.class);
        assertEquals("PU_REQ_ANALYSIS", head.get("来源单据"));
        assertTrue(String.valueOf(head.get("来源单号")).startsWith("分析-"));
        assertTrue(String.valueOf(head.get("请购人")).length() > 0, "请购人必填");

        Map<?, ?> detail = json.readValue(target.getDetailData(), Map.class);
        Map<?, ?> row = (Map<?, ?>) ((List<?>) detail.get("items")).get(0);
        assertEquals("CL001", row.get("存货编码"));
        assertEquals("铝棒", row.get("存货名称"));
        assertEquals("kg", row.get("采购单位"));
        assertEquals(100.0, ((Number) row.get("数量")).doubleValue());
        assertTrue(String.valueOf(row.get("备注")).contains("MO-001"), "需求来源应可追溯");
    }

    @Test
    void rejectsWhenNothingToPurchase() throws Exception {
        when(formMapper.selectList(any())).thenReturn(List.of());

        IllegalStateException error = assertThrows(IllegalStateException.class,
                () -> service.callButton("PU_REQ_ANALYSIS", "生成请购单", Map.of(), Map.of()));

        assertTrue(error.getMessage().contains("没有需要请购的物料"));
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

    private PanelConfig puReqConfig() throws Exception {
        PanelConfig config = new PanelConfig();
        config.setPanelCode("PU_REQ");
        config.setPanelName("请购单");
        config.setCategory("单据");
        Map<String, Object> head = new java.util.HashMap<>();
        head.put("单据日期", Map.of("dataName", "单据日期", "dataType", "日期"));
        head.put("单据编号", Map.of("dataName", "单据编号", "dataType", "文本", "autoCode", true));
        head.put("请购人", Map.of("dataName", "请购人", "dataType", "文本", "isRequired", true));
        head.put("需求日期", Map.of("dataName", "需求日期", "dataType", "日期"));
        head.put("来源单据", Map.of("dataName", "来源单据", "dataType", "文本"));
        head.put("来源单号", Map.of("dataName", "来源单号", "dataType", "文本"));
        Map<String, Object> detailField = new java.util.HashMap<>();
        detailField.put("dataName", "存货编码");
        detailField.put("dataType", "文本");
        config.setConfig(json.writeValueAsString(Map.of(
                "metadata", Map.of(
                        "panelCode", "PU_REQ", "panelName", "请购单", "panelCategory", "单据",
                        "autoCodeField", "单据编号",
                        "panelState", Map.of("dataName", "单据状态", "defaultOptions", List.of("草稿", "已审核")),
                        "buttonGroups", List.of(), "panelButtons", List.of(),
                        "panelPageDto", Map.of(
                                "tablePages", List.of(Map.of(
                                        "tableName", "请购单列表",
                                        "queryFields", List.of(),
                                        "gridTabs", List.of(Map.of("label", "明细", "columns", List.of())),
                                        "formPages", List.of())),
                                "formPages", List.of())),
                "dataSchema", Map.of("type", "object", "fields", List.copyOf(head.values())),
                "detail", Map.of("tabs", List.of(Map.of(
                        "key", "items", "label", "明细",
                        "fields", List.of(detailField)))))));
        return config;
    }
}
