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
 * 产成品入库单 → 销货单 推式生单（O2C 缺失的一环）。
 * 来源须已审核，明细带来源面板/单号/行号以便占用反查。
 */
@ExtendWith(MockitoExtension.class)
class PxServiceSaleInvFromFinishInTest {

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
    void generatesSaleInvFromApprovedFinishIn() throws Exception {
        when(panelMapper.selectOne(any())).thenReturn(saleInvConfig());
        FormData finishIn = document("FINISH_IN", "RK-CP-001", "已审核",
                Map.of("单据编号", "RK-CP-001", "客户", "中天精工", "客户编码", "KH002",
                        "仓库", "成品仓", "销售订单号", "SO-001"),
                Map.of("items", List.of(Map.of(
                        "存货编码", "CP001", "存货名称", "铝棒 Φ80", "规格型号", "Φ80×3000",
                        "计量单位", "件", "实收数量", 10, "单价", 25, "金额", 250))));
        when(formMapper.selectOne(any())).thenReturn(finishIn);
        when(formMapper.selectCount(any())).thenReturn(0L);

        Map<String, Object> result = service.callButton("FINISH_IN", "生成销货单",
                Map.of("编号", "RK-CP-001"), Map.of());

        assertEquals("SALE_INV", result.get("gotoPanel"));
        ArgumentCaptor<FormData> inserted = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).insert(inserted.capture());
        FormData target = inserted.getValue();

        assertEquals("SALE_INV", target.getPanelCode());
        assertEquals("草稿", target.getStatus());
        Map<?, ?> detail = json.readValue(target.getDetailData(), Map.class);
        Map<?, ?> row = (Map<?, ?>) ((List<?>) detail.get("items")).get(0);
        assertEquals("FINISH_IN", row.get("来源面板"));
        assertEquals("RK-CP-001", row.get("来源单号"));
        assertEquals(1, row.get("来源行号"));
        assertEquals(10.0, ((Number) row.get("数量")).doubleValue());
        assertEquals(0, new java.math.BigDecimal("250").compareTo(
                new java.math.BigDecimal(String.valueOf(row.get("销售金额")))));
    }

    @Test
    void rejectsUnapprovedSource() throws Exception {
        FormData draft = document("FINISH_IN", "RK-CP-002", "草稿",
                Map.of("单据编号", "RK-CP-002", "客户", "中天精工"),
                Map.of("items", List.of(Map.of("存货编码", "CP001", "实收数量", 5, "单价", 10))));
        when(formMapper.selectOne(any())).thenReturn(draft);

        IllegalStateException e = assertThrows(IllegalStateException.class,
                () -> service.callButton("FINISH_IN", "生成销货单", Map.of("编号", "RK-CP-002"), Map.of()));
        assertTrue(e.getMessage().contains("已审核"));
    }

    @Test
    void rejectsEmptyDetail() throws Exception {
        FormData empty = document("FINISH_IN", "RK-CP-003", "已审核",
                Map.of("单据编号", "RK-CP-003", "客户", "中天精工"),
                Map.of("items", List.of()));
        when(formMapper.selectOne(any())).thenReturn(empty);

        IllegalStateException e = assertThrows(IllegalStateException.class,
                () -> service.callButton("FINISH_IN", "生成销货单", Map.of("编号", "RK-CP-003"), Map.of()));
        assertTrue(e.getMessage().contains("明细"));
    }

    @Test
    void rejectsMissingSource() {
        when(formMapper.selectOne(any())).thenReturn(null);

        IllegalArgumentException e = assertThrows(IllegalArgumentException.class,
                () -> service.callButton("FINISH_IN", "生成销货单", Map.of("编号", "NOT-EXIST"), Map.of()));
        assertTrue(e.getMessage().contains("不存在"));
    }

    private FormData document(String panel, String no, String status,
                              Map<String, Object> head, Map<String, Object> detail) throws Exception {
        FormData fd = new FormData();
        fd.setPanelCode(panel);
        fd.setFormNo(no);
        fd.setStatus(status);
        fd.setData(json.writeValueAsString(head));
        fd.setDetailData(json.writeValueAsString(detail));
        fd.setCreateTime(LocalDateTime.now());
        return fd;
    }

    private PanelConfig saleInvConfig() throws Exception {
        PanelConfig panel = new PanelConfig();
        panel.setPanelCode("SALE_INV");
        panel.setPanelName("销货单");
        panel.setCategory("单据");

        List<Map<String, Object>> fields = List.of(
                Map.of("dataName", "存货编码", "dataType", "文本"),
                Map.of("dataName", "数量", "dataType", "小数"),
                Map.of("dataName", "单价", "dataType", "小数"),
                Map.of("dataName", "销售金额", "dataType", "小数", "computed", true));
        Map<String, Object> tab = Map.of("key", "items", "fields", fields);
        Map<String, Object> detail = Map.of("tabs", List.of(tab));
        Map<String, Object> schema = Map.of("fields", List.of(
                Map.of("dataName", "单据日期", "dataType", "日期"),
                Map.of("dataName", "单据编号", "dataType", "文本", "autoCode", true)));
        Map<String, Object> metadata = Map.of(
                "panelCode", "SALE_INV", "panelName", "销货单", "panelCategory", "单据",
                "autoCodeField", "单据编号", "panelButtons", List.of(),
                "buttonGroups", List.of(Map.of("name", "审核", "actions", List.of("审核", "弃审"))),
                "panelPageDto", Map.of("tablePages", List.of(Map.of("queryFields", List.of())), "formPages", List.of()));

        panel.setConfig(json.writeValueAsString(Map.of(
                "metadata", metadata,
                "dataSchema", schema,
                "detail", detail)));
        return panel;
    }
}
