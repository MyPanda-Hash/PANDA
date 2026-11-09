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
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 检验退料的退货金额按本次退回数量折算：检验 10 件退回 2 件，
 * 退货单金额/税额/价税合计只能是 2 件的量，不能整行复制到货金额。
 */
@ExtendWith(MockitoExtension.class)
class PxServicePurchaseReturnAmountTest {

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
    void proratesInspectionReturnAmountsByReturnedQuantity() throws Exception {
        Map<String, Object> inspectionItem = fields(
                "存货编码", "CL001", "存货名称", "铝棒", "计量单位", "件",
                "检验数量", 10, "合格数量", 8, "不合格数量", 2,
                "检验结果判定", "部分报废", "处置方式", "部分退回", "处置状态", "已确认",
                "判定退货数量", 2, "让步接收数量", 0, "报废数量", 0,
                "来源面板", "ARRIVAL_IN", "来源单号", "DH-001", "来源行号", 1);
        FormData inspection = document("INSPECTION", "JY-001", "已审核",
                Map.of("单据编号", "JY-001", "供应商", "华东铝业", "供应商编码", "KH001",
                        "仓库", "原料仓", "业务类型", "来料检验", "来源单号", "DH-001"),
                Map.of("items", List.of(inspectionItem),
                        "unqualified", List.of(Map.of(
                                "关联明细行号", 1, "不合格原因", "尺寸不良",
                                "处理方式", "退回", "处理数量", 2))));
        FormData arrival = document("ARRIVAL_IN", "DH-001", "已审核",
                Map.of("单据编号", "DH-001", "供应商", "华东铝业", "供应商编码", "KH001",
                        "仓库", "原料仓", "采购订单号", "PO-001"),
                Map.of("items", List.of(fields(
                        "存货编码", "CL001", "存货名称", "铝棒", "计量单位", "件",
                        "到货数量", 10, "实收数量", 10,
                        "单价", 10, "含税单价", 11.3, "税率%", 13,
                        "金额", 100, "税额", 13, "含税金额", 113))));
        // 1) 取检验单 2) 取到货单价格 3) validateSourceLinks 再取检验单
        when(formMapper.selectOne(any())).thenReturn(inspection, arrival, inspection);
        when(formMapper.selectList(any())).thenReturn(List.of());
        when(formMapper.selectCount(any())).thenReturn(0L);
        when(panelMapper.selectOne(any())).thenReturn(returnConfig());

        service.callButton("INSPECTION", "生成采购退货单", Map.of("编号", "JY-001"), Map.of());

        ArgumentCaptor<FormData> inserted = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).insert(inserted.capture());
        Map<?, ?> detail = json.readValue(inserted.getValue().getDetailData(), Map.class);
        Map<?, ?> row = (Map<?, ?>) ((List<?>) detail.get("items")).get(0);
        assertEquals(2.0, ((Number) row.get("数量")).doubleValue(), 0.0001);
        assertEquals(10.0, ((Number) row.get("单价")).doubleValue(), 0.0001);
        assertEquals(20.0, ((Number) row.get("金额")).doubleValue(), 0.0001);
        assertEquals(22.6, ((Number) row.get("含税金额")).doubleValue(), 0.0001);
        assertEquals(2.6, ((Number) row.get("税额")).doubleValue(), 0.0001);
        Map<?, ?> head = json.readValue(inserted.getValue().getData(), Map.class);
        assertEquals("PO-001", head.get("采购订单号"), "退货单应保留采购订单号追溯");
        assertEquals("JY-001", head.get("检验单号"));
    }

    @Test
    void proratesAmountWhenTaxPriceIsMissing() {
        PxService.ReturnAmounts amounts = PxService.returnAmounts(3, 10, 0, 13);

        assertEquals(30.0, amounts.amount(), 0.0001);
        assertEquals(33.9, amounts.gross(), 0.0001);
        assertEquals(3.9, amounts.tax(), 0.0001);
        assertEquals(11.3, amounts.taxPrice(), 0.0001);
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

    private PanelConfig returnConfig() throws Exception {
        PanelConfig config = new PanelConfig();
        config.setPanelCode("PURCHASE_RETURN");
        config.setPanelName("采购退货单");
        config.setCategory("单据");
        config.setConfig(json.writeValueAsString(Map.of(
                "metadata", Map.of(
                        "panelCode", "PURCHASE_RETURN", "panelName", "采购退货单", "panelCategory", "单据",
                        "autoCodeField", "单据编号",
                        "panelState", Map.of("dataName", "单据状态", "defaultOptions", List.of("草稿", "已审核")),
                        "buttonGroups", List.of(), "panelButtons", List.of(),
                        "panelPageDto", Map.of(
                                "tablePages", List.of(),
                                "formPages", List.of())),
                "dataSchema", Map.of("type", "object", "fields", List.of(
                        Map.of("dataName", "单据日期", "dataType", "日期"),
                        Map.of("dataName", "单据编号", "dataType", "文本", "autoCode", true),
                        Map.of("dataName", "供应商", "dataType", "文本"),
                        Map.of("dataName", "供应商编码", "dataType", "文本"),
                        Map.of("dataName", "采购订单号", "dataType", "文本"),
                        Map.of("dataName", "检验单号", "dataType", "文本"))),
                "detail", Map.of("tabs", List.of(Map.of(
                        "key", "items", "label", "明细", "fields", List.of()))))));
        return config;
    }
}
