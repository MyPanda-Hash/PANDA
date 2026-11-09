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
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 来料批次处置标记（2026-11-05）：
 * 全批退回按检验数量、降价接收折价率、处置确认与生单门禁。
 */
@ExtendWith(MockitoExtension.class)
class PxServiceInspectionDispositionTest {

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
    void confirmDispositionAcceptsFullBatchReturn() throws Exception {
        Map<String, Object> item = new HashMap<>();
        item.put("存货编码", "CL001");
        item.put("存货名称", "铝棒");
        item.put("报检数量", 10);
        item.put("检验数量", 10);
        item.put("合格数量", 2);
        item.put("不合格数量", 8);
        item.put("检验结果判定", "全批退回");
        item.put("处置方式", "全批退回");
        item.put("处置状态", "待确认");
        FormData inspection = document("INSPECTION", "JY-001", "草稿",
                Map.of("单据编号", "JY-001"),
                Map.of("items", List.of(item),
                        "unqualified", List.of(Map.of(
                                "关联明细行号", 1, "不合格原因", "尺寸不良",
                                "处理方式", "退回", "处理数量", 10))));
        when(formMapper.selectOne(any())).thenReturn(inspection);

        Map<String, Object> result = service.callButton("INSPECTION", "确认处置",
                Map.of("编号", "JY-001"), Map.of());

        assertEquals("已确认", result.get("处置状态"));
        ArgumentCaptor<FormData> updated = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).updateById(updated.capture());
        Map<?, ?> detail = json.readValue(updated.getValue().getDetailData(), Map.class);
        Map<?, ?> row = (Map<?, ?>) ((List<?>) detail.get("items")).get(0);
        assertEquals("已确认", row.get("处置状态"));
    }

    @Test
    void rejectsFullBatchReturnQuantityNotEqualToInspected() throws Exception {
        Map<String, Object> item = new HashMap<>();
        item.put("报检数量", 10);
        item.put("检验数量", 10);
        item.put("合格数量", 2);
        item.put("不合格数量", 8);
        item.put("检验结果判定", "全批退回");
        item.put("处置方式", "全批退回");
        item.put("处置状态", "待确认");
        FormData inspection = document("INSPECTION", "JY-002", "草稿",
                Map.of("单据编号", "JY-002"),
                Map.of("items", List.of(item),
                        "unqualified", List.of(Map.of(
                                "关联明细行号", 1, "不合格原因", "尺寸不良",
                                "处理方式", "退回", "处理数量", 8))));
        when(formMapper.selectOne(any())).thenReturn(inspection);

        IllegalStateException error = assertThrows(IllegalStateException.class,
                () -> service.callButton("INSPECTION", "确认处置", Map.of("编号", "JY-002"), Map.of()));

        assertTrue(error.getMessage().contains("全批退回的退回数量必须等于检验数量"));
    }

    @Test
    void rejectsPriceConcessionWithoutDiscountRate() throws Exception {
        Map<String, Object> item = new HashMap<>();
        item.put("报检数量", 10);
        item.put("检验数量", 10);
        item.put("合格数量", 8);
        item.put("不合格数量", 2);
        item.put("检验结果判定", "部分报废");
        item.put("处置方式", "降价接收");
        item.put("处置状态", "待确认");
        FormData inspection = document("INSPECTION", "JY-003", "草稿",
                Map.of("单据编号", "JY-003"),
                Map.of("items", List.of(item),
                        "unqualified", List.of(Map.of(
                                "关联明细行号", 1, "不合格原因", "尺寸不良",
                                "处理方式", "让步接收", "处理数量", 2))));
        when(formMapper.selectOne(any())).thenReturn(inspection);

        IllegalStateException error = assertThrows(IllegalStateException.class,
                () -> service.callButton("INSPECTION", "确认处置", Map.of("编号", "JY-003"), Map.of()));

        assertTrue(error.getMessage().contains("降价接收必须填写折价率"));
    }

    @Test
    void deriveComputesDiscountedPriceForConcession() {
        Map<String, Object> item = new HashMap<>();
        item.put("单价", 10);
        item.put("含税单价", 11.3);
        item.put("金额", 100);
        item.put("处置方式", "降价接收");
        item.put("折价率%", 10);
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", List.of(item));
        detail.put("unqualified", List.of(Map.of(
                "关联明细行号", 1, "处理方式", "让步接收", "处理数量", 2, "不合格原因", "尺寸不良")));

        PxService.deriveInspectionDetail("INSPECTION", detail);

        assertEquals(2.0, ((Number) item.get("处置数量")).doubleValue());
        assertEquals(9.0, ((Number) item.get("调整后单价")).doubleValue(), 0.0001);
        assertEquals(10.17, ((Number) item.get("调整后含税单价")).doubleValue(), 0.0001);
        assertEquals(10.0, ((Number) item.get("折价金额")).doubleValue(), 0.0001);
    }

    @Test
    void blocksInboundWhenDispositionStillPending() throws Exception {
        Map<String, Object> item = new HashMap<>();
        item.put("存货编码", "CL001");
        item.put("存货名称", "铝棒");
        item.put("检验数量", 10);
        item.put("合格数量", 10);
        item.put("处置状态", "待确认");
        FormData inspection = document("INSPECTION", "JY-004", "已审核",
                Map.of("单据编号", "JY-004", "供应商", "华东铝业"),
                Map.of("items", List.of(item)));
        when(formMapper.selectOne(any())).thenReturn(inspection);

        IllegalStateException error = assertThrows(IllegalStateException.class,
                () -> service.callButton("INSPECTION", "生成采购入库单", Map.of("编号", "JY-004"), Map.of()));

        assertTrue(error.getMessage().contains("处置尚未确认"));
    }

    @Test
    void blocksInboundForReworkUntilReinspectionPassed() throws Exception {
        Map<String, Object> item = new HashMap<>();
        item.put("存货编码", "CL001");
        item.put("存货名称", "铝棒");
        item.put("检验数量", 10);
        item.put("合格数量", 10);
        item.put("处置方式", "返工");
        item.put("处置状态", "已确认");
        FormData inspection = document("INSPECTION", "JY-005", "已审核",
                Map.of("单据编号", "JY-005", "供应商", "华东铝业"),
                Map.of("items", List.of(item)));
        when(formMapper.selectOne(any())).thenReturn(inspection);

        IllegalStateException error = assertThrows(IllegalStateException.class,
                () -> service.callButton("INSPECTION", "生成采购入库单", Map.of("编号", "JY-005"), Map.of()));

        assertTrue(error.getMessage().contains("复检合格后才能入库"));
    }

    @Test
    void blocksDirectInboundFromArrivalWhenInspectionRequired() throws Exception {
        FormData arrival = document("ARRIVAL_IN", "DH-005", "已审核",
                Map.of("单据编号", "DH-005", "供应商", "华东铝业", "检验状态", "未检"),
                Map.of("items", List.of(Map.of(
                        "存货编码", "CL001", "存货名称", "铝棒", "实收数量", 10,
                        "到货数量", 10, "检验方式", "全检", "采购单位", "件"))));
        when(formMapper.selectOne(any())).thenReturn(arrival);
        when(formMapper.selectList(any())).thenReturn(List.of());

        IllegalStateException error = assertThrows(IllegalStateException.class,
                () -> service.callButton("ARRIVAL_IN", "生成采购入库单",
                        Map.of("编号", "DH-005"), Map.of()));

        assertTrue(error.getMessage().contains("请先生成检验单并完成检验"));
    }

    @Test
    void rejectsFullBatchVerdictWithoutFullBatchDisposition() throws Exception {
        Map<String, Object> item = new HashMap<>();
        item.put("报检数量", 10);
        item.put("检验数量", 10);
        item.put("合格数量", 2);
        item.put("不合格数量", 8);
        item.put("检验结果判定", "全批退回");
        item.put("处置方式", "部分退回");
        item.put("处置状态", "待确认");
        FormData inspection = document("INSPECTION", "JY-008", "草稿",
                Map.of("单据编号", "JY-008"),
                Map.of("items", List.of(item),
                        "unqualified", List.of(Map.of(
                                "关联明细行号", 1, "不合格原因", "尺寸不良",
                                "处理方式", "退回", "处理数量", 8))));
        when(formMapper.selectOne(any())).thenReturn(inspection);

        IllegalStateException error = assertThrows(IllegalStateException.class,
                () -> service.callButton("INSPECTION", "确认处置",
                        Map.of("编号", "JY-008"), Map.of()));

        assertTrue(error.getMessage().contains("处置方式必须为全批退回"));
    }

    @Test
    void rejectsInspectionQuantityExceedingInspectableQuantity() throws Exception {
        Map<String, Object> item = new HashMap<>();
        item.put("报检数量", 10);
        item.put("应检数量", 8);
        item.put("检验数量", 9);
        item.put("合格数量", 9);
        item.put("不合格数量", 0);
        item.put("检验结果判定", "合格");
        item.put("处置状态", "已确认");
        FormData inspection = document("INSPECTION", "JY-009", "草稿",
                Map.of("单据编号", "JY-009"),
                Map.of("items", List.of(item), "unqualified", List.of()));
        when(formMapper.selectOne(any())).thenReturn(inspection);

        IllegalStateException error = assertThrows(IllegalStateException.class,
                () -> service.callButton("INSPECTION", "确认处置",
                        Map.of("编号", "JY-009"), Map.of()));

        assertTrue(error.getMessage().contains("检验数量不能超过应检数量"));
    }

    @Test
    void blocksFinishedGoodsInspectionFromPurchaseInbound() throws Exception {
        Map<String, Object> item = new HashMap<>();
        item.put("存货编码", "CP001");
        item.put("存货名称", "减速箱体");
        item.put("检验数量", 10);
        item.put("合格数量", 10);
        item.put("处置状态", "已确认");
        item.put("来源面板", "FINISH_INSPECT");
        item.put("来源单号", "BJ-001");
        item.put("来源行号", 1);
        FormData inspection = document("INSPECTION", "JY-F001", "已审核",
                Map.of("单据编号", "JY-F001", "业务类型", "成品检验", "来源单号", "BJ-001"),
                Map.of("items", List.of(item)));
        when(formMapper.selectOne(any())).thenReturn(inspection);

        IllegalStateException error = assertThrows(IllegalStateException.class,
                () -> service.callButton("INSPECTION", "生成采购入库单",
                        Map.of("编号", "JY-F001"), Map.of()));

        assertTrue(error.getMessage().contains("不是来料检验"));
        assertTrue(error.getMessage().contains("生成产成品入库单"));
    }

    @Test
    void resetsConfirmedDispositionWhenQualityDataChanges() throws Exception {
        Map<String, Object> previousItem = new HashMap<>(Map.of(
                "存货编码", "CL001", "检验数量", 10, "合格数量", 8, "不合格数量", 2,
                "检验结果判定", "部分报废", "处置方式", "部分退回", "处置状态", "已确认",
                "让步接收数量", 0, "报废数量", 0, "判定退货数量", 2));
        FormData existing = document("INSPECTION", "JY-006", "草稿",
                Map.of("单据编号", "JY-006"),
                Map.of("items", List.of(previousItem),
                        "unqualified", List.of(Map.of(
                                "关联明细行号", 1, "不合格原因", "尺寸不良",
                                "处理方式", "退回", "处理数量", 2))));
        when(formMapper.selectOne(any())).thenReturn(existing);
        when(panelMapper.selectOne(any())).thenReturn(inspectionConfig());

        Map<String, Object> nextItem = new HashMap<>(previousItem);
        nextItem.put("合格数量", 7);
        nextItem.put("不合格数量", 3);
        nextItem.put("判定退货数量", 3);
        Map<String, Object> nextDetail = new HashMap<>();
        nextDetail.put("items", List.of(nextItem));
        nextDetail.put("unqualified", List.of(Map.of(
                "关联明细行号", 1, "不合格原因", "尺寸不良",
                "处理方式", "退回", "处理数量", 3)));
        Map<String, Object> formData = new HashMap<>();
        formData.put("编号", "JY-006");
        formData.put("detail", nextDetail);

        service.callButton("INSPECTION", "保存", formData, Map.of());

        ArgumentCaptor<FormData> updated = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).updateById(updated.capture());
        Map<?, ?> detail = json.readValue(updated.getValue().getDetailData(), Map.class);
        Map<?, ?> row = (Map<?, ?>) ((List<?>) detail.get("items")).get(0);
        assertEquals("待确认", row.get("处置状态"), "检验数量变化后原确认必须失效");
    }

    @Test
    void keepsConfirmedDispositionWhenOnlyRemarkChanges() throws Exception {
        Map<String, Object> previousItem = fields(
                "存货编码", "CL001", "检验数量", 10, "合格数量", 8, "不合格数量", 2,
                "检验结果判定", "部分报废", "处置方式", "部分退回", "处置状态", "已确认",
                "判定退货数量", 2, "处置数量", 2, "让步接收数量", 0, "报废数量", 0,
                "折价金额", 0, "调整后单价", 0, "调整后含税单价", 0,
                "备注", "旧备注");
        FormData existing = document("INSPECTION", "JY-007", "草稿",
                Map.of("单据编号", "JY-007"),
                Map.of("items", List.of(previousItem),
                        "unqualified", List.of(Map.of(
                                "关联明细行号", 1, "不合格原因", "尺寸不良",
                                "处理方式", "退回", "处理数量", 2))));
        when(formMapper.selectOne(any())).thenReturn(existing);
        when(panelMapper.selectOne(any())).thenReturn(inspectionConfig());

        Map<String, Object> nextItem = new HashMap<>(previousItem);
        nextItem.put("备注", "新备注");
        Map<String, Object> nextDetail = new HashMap<>();
        nextDetail.put("items", List.of(nextItem));
        nextDetail.put("unqualified", List.of(Map.of(
                "关联明细行号", 1, "不合格原因", "尺寸不良",
                "处理方式", "退回", "处理数量", 2)));
        Map<String, Object> formData = new HashMap<>();
        formData.put("编号", "JY-007");
        formData.put("detail", nextDetail);

        service.callButton("INSPECTION", "保存", formData, Map.of());

        ArgumentCaptor<FormData> updated = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).updateById(updated.capture());
        Map<?, ?> detail = json.readValue(updated.getValue().getDetailData(), Map.class);
        Map<?, ?> row = (Map<?, ?>) ((List<?>) detail.get("items")).get(0);
        assertEquals("已确认", row.get("处置状态"), "仅备注变化不应要求重新确认");
    }

    private static Map<String, Object> fields(Object... values) {
        Map<String, Object> map = new HashMap<>();
        for (int index = 0; index + 1 < values.length; index += 2) {
            map.put(String.valueOf(values[index]), values[index + 1]);
        }
        return map;
    }

    private PanelConfig inspectionConfig() throws Exception {
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
                        Map.of("dataName", "单据编号", "dataType", "文本", "autoCode", true),
                        Map.of("dataName", "单据日期", "dataType", "日期"))),
                "detail", Map.of("tabs", List.of(Map.of("key", "items", "fields", List.of()))))));
        return config;
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
}
