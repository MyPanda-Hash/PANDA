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
 * 采购订单 → 到货单：已审核订单下推后，表头/明细字段与来源戳必须完整，
 * 否则到货单在列表页与质检链路里会出现空列。
 */
@ExtendWith(MockitoExtension.class)
class PxServiceArrivalFromOrderTest {

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
    void generatesArrivalWithOrderFieldsAndSourceStamps() throws Exception {
        java.util.Map<String, Object> poItem = new java.util.HashMap<>();
        poItem.put("物料编码", "CL001");
        poItem.put("物料名称", "铝棒");
        poItem.put("规格型号", "80mm");
        poItem.put("单位", "件");
        poItem.put("数量", 12);
        poItem.put("单价", 10);
        poItem.put("税率%", 13);
        poItem.put("含税单价", 11.3);
        poItem.put("金额", 120);
        poItem.put("含税金额", 135.6);
        poItem.put("仓库", "原料仓");
        poItem.put("预计到货日期", "2026-11-10");

        FormData order = document("PU_ORDER", "PO-001", "已审核",
                Map.of("单据编号", "PO-001", "供应商", "华东铝业", "供应商编码", "KH001",
                        "项目", "P001", "交货日期", "2026-11-10"),
                Map.of("items", List.of(poItem)));
        when(formMapper.selectOne(any())).thenReturn(order);
        when(formMapper.selectList(any())).thenReturn(List.of());
        when(formMapper.selectCount(any())).thenReturn(0L);
        when(panelMapper.selectOne(any())).thenReturn(arrivalConfig());

        Map<String, Object> result = service.callButton("PU_ORDER", "生成到货单",
                Map.of("编号", "PO-001"), Map.of());

        assertEquals("ARRIVAL_IN", result.get("gotoPanel"));
        ArgumentCaptor<FormData> inserted = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).insert(inserted.capture());
        FormData target = inserted.getValue();
        assertEquals("ARRIVAL_IN", target.getPanelCode());
        assertEquals("草稿", target.getStatus());

        Map<?, ?> head = json.readValue(target.getData(), Map.class);
        assertEquals("PU_ORDER", head.get("来源单据"));
        assertEquals("PO-001", head.get("来源单号"));
        assertEquals("PO-001", head.get("采购订单号"));
        assertEquals("华东铝业", head.get("供应商"));
        assertEquals("KH001", head.get("供应商编码"));
        assertEquals("P001", head.get("项目"));
        assertEquals("普通采购", head.get("业务类型"));
        assertEquals("未检", head.get("检验状态"));
        assertEquals("采购订单", head.get("数据来源"));
        assertEquals("原料仓", head.get("仓库"));

        Map<?, ?> detail = json.readValue(target.getDetailData(), Map.class);
        Map<?, ?> row = (Map<?, ?>) ((List<?>) detail.get("items")).get(0);
        assertEquals("CL001", row.get("存货编码"));
        assertEquals("铝棒", row.get("存货名称"));
        assertEquals("件", row.get("采购单位"));
        assertEquals("原料仓", row.get("仓库"));
        assertEquals(12.0, ((Number) row.get("到货数量")).doubleValue());
        assertEquals(12.0, ((Number) row.get("实收数量")).doubleValue());
        assertEquals(0.0, ((Number) row.get("到货拒收数量")).doubleValue());
        assertEquals(0.0, ((Number) row.get("检验数量")).doubleValue());
        assertEquals(0.0, ((Number) row.get("累计入库数量")).doubleValue());
        // 税额由含税金额与税率拆分（135.6 × 13 / 113 = 15.6），修复生单缺税额
        assertEquals(15.6, ((Number) row.get("税额")).doubleValue(), 0.0001);
        assertEquals("全检", row.get("检验方式"));
        assertEquals("PU_ORDER", row.get("来源面板"));
        assertEquals("PO-001", row.get("来源单号"));
        assertEquals(1, row.get("来源行号"));
    }

    @Test
    void rejectsDraftOrder() throws Exception {
        FormData draft = document("PU_ORDER", "PO-002", "草稿",
                Map.of("单据编号", "PO-002", "供应商", "华东铝业"),
                Map.of("items", List.of(Map.of(
                        "物料编码", "CL001", "物料名称", "铝棒", "单位", "件", "数量", 5))));
        when(formMapper.selectOne(any())).thenReturn(draft);

        IllegalStateException error = assertThrows(IllegalStateException.class,
                () -> service.callButton("PU_ORDER", "生成到货单", Map.of("编号", "PO-002"), Map.of()));

        assertTrue(error.getMessage().contains("仅已审核单据可生成到货单"));
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

    private PanelConfig arrivalConfig() throws Exception {
        PanelConfig config = new PanelConfig();
        config.setPanelCode("ARRIVAL_IN");
        config.setPanelName("到货单");
        config.setCategory("单据");
        config.setConfig(json.writeValueAsString(Map.of(
                "metadata", Map.of(
                        "panelCode", "ARRIVAL_IN", "panelName", "到货单", "panelCategory", "单据",
                        "autoCodeField", "单据编号",
                        "panelState", Map.of("dataName", "单据状态", "defaultOptions", List.of("草稿", "已审核")),
                        "buttonGroups", List.of(), "panelButtons", List.of(),
                        "panelPageDto", Map.of(
                                "tablePages", List.of(Map.of(
                                        "tableName", "到货单列表",
                                        "queryFields", List.of(),
                                        "gridTabs", List.of(Map.of("label", "明细", "columns", List.of())),
                                        "formPages", List.of())),
                                "formPages", List.of())),
                "dataSchema", Map.of("type", "object", "fields", List.of(
                        Map.of("dataName", "单据日期", "dataType", "日期"),
                        Map.of("dataName", "单据编号", "dataType", "文本", "autoCode", true),
                        Map.of("dataName", "业务类型", "dataType", "下拉框", "defaultValue", "普通采购"),
                        Map.of("dataName", "供应商", "dataType", "文本"),
                        Map.of("dataName", "供应商编码", "dataType", "文本"),
                        Map.of("dataName", "项目", "dataType", "文本"),
                        Map.of("dataName", "仓库", "dataType", "文本"),
                        Map.of("dataName", "采购订单号", "dataType", "文本"),
                        Map.of("dataName", "到货日期", "dataType", "日期"),
                        Map.of("dataName", "检验状态", "dataType", "下拉框", "defaultValue", "未检"),
                        Map.of("dataName", "数据来源", "dataType", "文本"))),
                "detail", Map.of("tabs", List.of(Map.of(
                        "key", "items", "label", "明细",
                        "fields", List.of(
                                Map.of("dataName", "存货编码", "dataType", "文本"),
                                Map.of("dataName", "存货名称", "dataType", "文本"),
                                Map.of("dataName", "仓库", "dataType", "文本"),
                                Map.of("dataName", "采购单位", "dataType", "文本"),
                                Map.of("dataName", "到货数量", "dataType", "小数"),
                                Map.of("dataName", "实收数量", "dataType", "小数"),
                                Map.of("dataName", "检验方式", "dataType", "文本"),
                                Map.of("dataName", "单价", "dataType", "小数"))))))));
        return config;
    }
}
