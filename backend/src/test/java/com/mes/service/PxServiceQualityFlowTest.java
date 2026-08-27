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

@ExtendWith(MockitoExtension.class)
class PxServiceQualityFlowTest {

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
    void generatesInspectionWithTraceableSourceRows() throws Exception {
        FormData arrival = document("ARRIVAL_IN", "DH-001", "已审核",
                Map.of("单据编号", "DH-001", "供应商", "华东铝业", "仓库", "原料仓"),
                Map.of("items", List.of(Map.of(
                        "存货编码", "CL001", "存货名称", "铝棒", "规格型号", "80mm",
                        "计量单位", "件", "到货数量", 12))));
        when(formMapper.selectOne(any())).thenReturn(arrival);
        when(formMapper.selectList(any())).thenReturn(List.of());
        when(formMapper.selectCount(any())).thenReturn(0L);
        when(panelMapper.selectOne(any())).thenReturn(inspectionConfig());

        Map<String, Object> result = service.callButton("ARRIVAL_IN", "生成检验单",
                Map.of("编号", "DH-001"), Map.of());

        assertEquals("INSPECTION", result.get("gotoPanel"));
        ArgumentCaptor<FormData> inserted = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).insert(inserted.capture());
        FormData target = inserted.getValue();
        Map<?, ?> detail = json.readValue(target.getDetailData(), Map.class);
        Map<?, ?> row = (Map<?, ?>) ((List<?>) detail.get("items")).get(0);
        assertEquals("ARRIVAL_IN", row.get("来源面板"));
        assertEquals("DH-001", row.get("来源单号"));
        assertEquals(1, row.get("来源行号"));
        assertEquals(12.0, ((Number) row.get("来源数量")).doubleValue());
        assertEquals(12.0, ((Number) row.get("检验数量")).doubleValue());
    }

    @Test
    void rejectsInspectionWhenUnqualifiedQuantityHasNoTreatment() throws Exception {
        FormData inspection = document("INSPECTION", "JY-001", "草稿",
                Map.of("单据编号", "JY-001", "业务类型", "来料检验"),
                Map.of("items", List.of(Map.of(
                        "存货名称", "铝棒", "报检数量", 10, "检验数量", 10,
                        "合格数量", 8, "不合格数量", 2, "检验结果判定", "不合格"))));
        when(formMapper.selectOne(any())).thenReturn(inspection);

        IllegalStateException error = assertThrows(IllegalStateException.class,
                () -> service.callButton("INSPECTION", "审核", Map.of("编号", "JY-001"), Map.of()));

        assertTrue(error.getMessage().contains("不合格处理数量"));
        assertEquals("草稿", inspection.getStatus());
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

    private PanelConfig inspectionConfig() throws Exception {
        PanelConfig config = new PanelConfig();
        config.setPanelCode("INSPECTION");
        config.setPanelName("来料/成品检验单");
        config.setCategory("单据");
        config.setConfig(json.writeValueAsString(Map.of(
                "metadata", Map.of(
                        "panelName", "来料/成品检验单", "panelCategory", "单据", "autoCodeField", "单据编号",
                        "panelState", Map.of("dataName", "单据状态"), "buttonGroups", List.of(), "panelButtons", List.of(),
                        "panelPageDto", Map.of("tablePages", List.of(), "formPages", List.of())),
                "dataSchema", Map.of("fields", List.of(
                        Map.of("dataName", "单据日期", "dataType", "日期"),
                        Map.of("dataName", "单据编号", "dataType", "文本", "autoCode", true))),
                "detail", Map.of("tabs", List.of(Map.of("key", "items", "fields", List.of()))))));
        return config;
    }
}
