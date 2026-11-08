package com.mes.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormApproval;
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
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 成品报检单/首件报检单的累计质量结果：检验单审批通过后，
 * 沿来源链把 累计检验/合格/验收退回/让步/报废 回写到报检行。
 */
@ExtendWith(MockitoExtension.class)
class PxServiceReportRollupTest {

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
    void approvingInspectionRollsUpFinishReportQuantities() throws Exception {
        Map<String, Object> reportItem = fields(
                "产品名称", "铝棒", "存货编码", "CP001", "报检数量", 10,
                "合格数量", 0, "不合格数量", 0, "检验结果", "待检");
        FormData report = document("FINISH_INSPECT", "BJ-001", "已审核",
                Map.of("单据编号", "BJ-001", "业务类型", "完工报检", "加工单号", "MO-001"),
                Map.of("items", List.of(reportItem)));

        Map<String, Object> inspectionItem = fields(
                "存货编码", "CP001", "存货名称", "铝棒", "报检数量", 10, "应检数量", 10,
                "检验数量", 10, "合格数量", 8, "不合格数量", 2,
                "让步接收数量", 0, "报废数量", 0, "验收退回数量", 2, "判定退货数量", 2,
                "返工数量", 0, "检验结果判定", "部分报废",
                "处置方式", "部分退回", "处置状态", "已确认",
                "来源面板", "FINISH_INSPECT", "来源单号", "BJ-001", "来源行号", 1);
        FormData inspection = document("INSPECTION", "JY-001", "审批中",
                Map.of("单据编号", "JY-001", "业务类型", "成品检验", "来源单号", "BJ-001"),
                Map.of("items", List.of(inspectionItem), "unqualified", List.of(Map.of(
                        "关联明细行号", 1, "不合格原因", "尺寸不良",
                        "处理方式", "退回", "处理数量", 2))));

        when(formMapper.selectOne(any())).thenReturn(inspection, report, inspection);
        when(formMapper.selectList(any())).thenReturn(List.of(report, inspection));
        when(panelMapper.selectOne(any())).thenReturn(panelConfig());
        when(approvalMapper.selectOne(any())).thenReturn(submission("JY-001"));

        service.callButton("INSPECTION", "审批通过",
                Map.of("编号", "JY-001", "审批意见", "同意"), Map.of());

        ArgumentCaptor<FormData> updated = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper, org.mockito.Mockito.atLeastOnce()).updateById(updated.capture());
        FormData rolled = updated.getAllValues().stream()
                .filter(row -> "FINISH_INSPECT".equals(row.getPanelCode())).findFirst().orElseThrow();
        Map<?, ?> detail = json.readValue(rolled.getDetailData(), Map.class);
        Map<?, ?> row = (Map<?, ?>) ((List<?>) detail.get("items")).get(0);
        assertEquals(10.0, ((Number) row.get("累计检验数量")).doubleValue(), 0.0001);
        assertEquals(8.0, ((Number) row.get("累计合格数量")).doubleValue(), 0.0001);
        assertEquals(2.0, ((Number) row.get("累计验收退回数量")).doubleValue(), 0.0001);
        assertEquals(0.0, ((Number) row.get("累计让步接收数量")).doubleValue(), 0.0001);
        assertEquals(0.0, ((Number) row.get("累计报废数量")).doubleValue(), 0.0001);
    }

    private static Map<String, Object> fields(Object... values) {
        Map<String, Object> map = new HashMap<>();
        for (int index = 0; index + 1 < values.length; index += 2) {
            map.put(String.valueOf(values[index]), values[index + 1]);
        }
        return map;
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

    private FormApproval submission(String no) {
        FormApproval record = new FormApproval();
        record.setPanelCode("INSPECTION");
        record.setFormNo(no);
        record.setAction("SUBMIT");
        record.setResult("PENDING");
        return record;
    }

    private PanelConfig panelConfig() throws Exception {
        PanelConfig config = new PanelConfig();
        config.setPanelCode("INSPECTION");
        config.setPanelName("来料/成品检验单");
        config.setCategory("单据");
        config.setConfig(json.writeValueAsString(Map.of(
                "metadata", Map.of(
                        "panelCode", "INSPECTION", "panelName", "来料/成品检验单", "panelCategory", "单据",
                        "autoCodeField", "单据编号",
                        "panelState", Map.of("dataName", "单据状态"),
                        "buttonGroups", List.of(), "panelButtons", List.of(),
                        "panelPageDto", Map.of("tablePages", List.of(), "formPages", List.of())),
                "dataSchema", Map.of("type", "object", "fields", List.of(
                        Map.of("dataName", "单据日期", "dataType", "日期"),
                        Map.of("dataName", "单据编号", "dataType", "文本", "autoCode", true))),
                "detail", Map.of("tabs", List.of(Map.of("key", "items", "fields", List.of()))))));
        return config;
    }
}
