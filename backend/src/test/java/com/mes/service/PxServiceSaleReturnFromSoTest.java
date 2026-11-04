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

import java.math.BigDecimal;
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
 * 销售订单 → 销售退货单 推式生单。
 * 来源须已审核；退货单审核后按 SALE_RETURN 走入库 + 冲减应收（见 StockTxnService / FinanceSettleService）。
 */
@ExtendWith(MockitoExtension.class)
class PxServiceSaleReturnFromSoTest {

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
    void generatesSaleReturnFromApprovedSo() throws Exception {
        when(panelMapper.selectOne(any())).thenReturn(saleReturnConfig());
        FormData so = document("SO_ORDER", "SO-2026-11-0304", "已审核",
                Map.of("单据编号", "SO-2026-11-0304", "单据日期", "2026-11-03",
                        "客户", "流程测试客户", "客户编码", "T-CUS-01", "结算客户", "流程测试客户"),
                Map.of("items", List.of(Map.of(
                        "存货编码", "CP001", "存货名称", "铝棒 Φ80", "规格型号", "Φ80×3000",
                        "销售单位", "件", "数量", 10, "单价", 100, "含税单价", 113, "税率%", 13))));
        when(formMapper.selectOne(any())).thenReturn(so);
        when(formMapper.selectCount(any())).thenReturn(0L);

        Map<String, Object> result = service.callButton("SO_ORDER", "生成销售退货单",
                Map.of("编号", "SO-2026-11-0304"), Map.of());

        assertEquals("SALE_RETURN", result.get("gotoPanel"));
        ArgumentCaptor<FormData> inserted = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).insert(inserted.capture());
        FormData target = inserted.getValue();

        assertEquals("SALE_RETURN", target.getPanelCode());
        assertEquals("草稿", target.getStatus());

        Map<?, ?> head = json.readValue(target.getData(), Map.class);
        assertEquals("SO-2026-11-0304", head.get("来源单号"));
        assertEquals("流程测试客户", head.get("客户"));

        Map<?, ?> detail = json.readValue(target.getDetailData(), Map.class);
        Map<?, ?> row = (Map<?, ?>) ((List<?>) detail.get("items")).get(0);
        assertEquals("CP001", row.get("存货编码"));
        assertEquals("成品仓", row.get("仓库"));
        assertEquals(10.0, ((Number) row.get("数量")).doubleValue());
        // 退货数量同时写实收数量：库存入库按 实收数量 取值
        assertEquals(10.0, ((Number) row.get("实收数量")).doubleValue());
        assertEquals("SO_ORDER", row.get("来源面板"));
        assertEquals("SO-2026-11-0304", row.get("来源单号"));
        assertEquals(1, row.get("来源行号"));
        assertEquals(0, new BigDecimal("1000").compareTo(
                new BigDecimal(String.valueOf(row.get("金额")))));
    }

    @Test
    void rejectsUnapprovedSource() throws Exception {
        FormData draft = document("SO_ORDER", "SO-DRAFT", "草稿",
                Map.of("单据编号", "SO-DRAFT", "客户", "中天精工"),
                Map.of("items", List.of(Map.of("存货编码", "CP001", "数量", 1, "单价", 10))));
        when(formMapper.selectOne(any())).thenReturn(draft);

        IllegalStateException e = assertThrows(IllegalStateException.class,
                () -> service.callButton("SO_ORDER", "生成销售退货单", Map.of("编号", "SO-DRAFT"), Map.of()));
        assertTrue(e.getMessage().contains("已审核"));
    }

    @Test
    void rejectsEmptyDetail() throws Exception {
        FormData empty = document("SO_ORDER", "SO-EMPTY", "已审核",
                Map.of("单据编号", "SO-EMPTY", "客户", "中天精工"),
                Map.of("items", List.of()));
        when(formMapper.selectOne(any())).thenReturn(empty);

        IllegalStateException e = assertThrows(IllegalStateException.class,
                () -> service.callButton("SO_ORDER", "生成销售退货单", Map.of("编号", "SO-EMPTY"), Map.of()));
        assertTrue(e.getMessage().contains("明细"));
    }

    @Test
    void rejectsMissingSource() {
        when(formMapper.selectOne(any())).thenReturn(null);

        IllegalArgumentException e = assertThrows(IllegalArgumentException.class,
                () -> service.callButton("SO_ORDER", "生成销售退货单", Map.of("编号", "NOT-EXIST"), Map.of()));
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

    private PanelConfig saleReturnConfig() throws Exception {
        PanelConfig panel = new PanelConfig();
        panel.setPanelCode("SALE_RETURN");
        panel.setPanelName("销售退货单");
        panel.setCategory("单据");

        List<Map<String, Object>> fields = List.of(
                Map.of("dataName", "仓库", "dataType", "文本"),
                Map.of("dataName", "存货编码", "dataType", "文本"),
                Map.of("dataName", "存货名称", "dataType", "文本"),
                Map.of("dataName", "数量", "dataType", "小数"),
                Map.of("dataName", "实收数量", "dataType", "小数"),
                Map.of("dataName", "单价", "dataType", "小数"),
                Map.of("dataName", "金额", "dataType", "小数", "computed", true));
        Map<String, Object> tab = Map.of("key", "items", "fields", fields);
        Map<String, Object> detail = Map.of("tabs", List.of(tab));
        Map<String, Object> schema = Map.of("fields", List.of(
                Map.of("dataName", "单据日期", "dataType", "日期"),
                Map.of("dataName", "单据编号", "dataType", "文本", "autoCode", true),
                Map.of("dataName", "来源单号", "dataType", "文本")));
        Map<String, Object> metadata = Map.of(
                "panelCode", "SALE_RETURN", "panelName", "销售退货单", "panelCategory", "单据",
                "autoCodeField", "单据编号", "panelButtons", List.of(),
                "buttonGroups", List.of(Map.of("name", "审核", "actions", List.of("审核", "弃审"))),
                "panelPageDto", Map.of("tablePages", List.of(Map.of("queryFields", List.of())),
                        "formPages", List.of()));

        Map<String, Object> config = Map.of(
                "metadata", metadata,
                "dataSchema", Map.of("type", "object", "fields", schema.get("fields")),
                "detail", detail);
        panel.setConfig(json.writeValueAsString(config));
        return panel;
    }
}
