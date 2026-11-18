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
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class PxServiceQuoteOrderTest {

    @Mock private PanelConfigMapper panelMapper;
    @Mock private FormDataMapper formMapper;
    @Mock private FormApprovalMapper approvalMapper;
    @Mock private ReportQueryService reportQueryService;

    private final ObjectMapper json = new ObjectMapper();
    private PxService service;

    @BeforeEach
    void setUp() throws Exception {
        service = new PxService(panelMapper, formMapper, approvalMapper, reportQueryService);
        when(panelMapper.selectOne(any())).thenReturn(quoteConfig());
    }

    @Test
    void upgradesQuoteAmountCalculationToSalesOrderSemantics() {
        Map<String, Object> config = service.getPanelConfig("QUOTE_ORDER");
        Map<?, ?> detail = (Map<?, ?>) config.get("detail");
        Map<?, ?> tab = (Map<?, ?>) ((List<?>) detail.get("tabs")).get(0);
        List<?> calculations = (List<?>) tab.get("calc");

        assertEquals(List.of(
                "报价单价 * (1 + 税率% / 100)",
                "数量 * 报价单价",
                "数量 * 含税单价"), calculations.stream()
                .map(value -> ((Map<?, ?>) value).get("formula")).toList());
        List<?> fields = (List<?>) tab.get("fields");
        assertTrue(fields.stream().filter(value -> value instanceof Map<?, ?>)
                .map(value -> (Map<?, ?>) value)
                .filter(field -> List.of("含税单价", "金额", "含税金额").contains(field.get("dataName")))
                .allMatch(field -> Boolean.TRUE.equals(field.get("computed"))));
    }

    @Test
    void quoteCurrentStockUsesStockStatusTotalAcrossWarehouses() throws Exception {
        FormData quote = new FormData();
        quote.setPanelCode("QUOTE_ORDER");
        quote.setFormNo("BJ-TEST-001");
        quote.setStatus("草稿");
        quote.setData(json.writeValueAsString(Map.of("单据编号", "BJ-TEST-001")));
        quote.setDetailData(json.writeValueAsString(Map.of("items", List.of(Map.of(
                "存货编码", "CP001", "存货名称", "测试产品", "现存量", 999)))));
        quote.setCreateTime(LocalDateTime.now());
        when(formMapper.selectOne(any())).thenReturn(quote);
        when(reportQueryService.currentStockRows()).thenReturn(List.of(
                Map.of("仓库", "成品仓", "存货编码", "CP001", "存货", "测试产品", "现存量(主)", 3),
                Map.of("仓库", "待检仓", "存货编码", "CP001", "存货", "测试产品", "现存量(主)", 4),
                Map.of("仓库", "成品仓", "存货编码", "CP002", "存货", "其他产品", "现存量(主)", 20)));

        Map<String, Object> descriptor = service.getFormDescriptor("QUOTE_ORDER", "BJ-TEST-001");
        Map<?, ?> detail = (Map<?, ?>) descriptor.get("detailData");
        Map<?, ?> row = (Map<?, ?>) ((List<?>) detail.get("items")).get(0);

        assertEquals(7.0, ((Number) row.get("现存量")).doubleValue());
    }

    private PanelConfig quoteConfig() throws Exception {
        PanelConfig panel = new PanelConfig();
        panel.setPanelCode("QUOTE_ORDER");
        panel.setPanelName("报价单");
        panel.setCategory("单据");
        panel.setConfig(json.writeValueAsString(Map.of(
                "metadata", Map.of(
                        "panelCode", "QUOTE_ORDER", "panelName", "报价单", "panelCategory", "单据",
                        "autoCodeField", "单据编号", "panelButtons", List.of(),
                        "buttonGroups", List.of(Map.of("name", "审核", "actions", List.of("审核", "弃审"))),
                        "panelPageDto", Map.of("tablePages", List.of(Map.of("queryFields", List.of())), "formPages", List.of())),
                "dataSchema", Map.of("fields", List.of(
                        Map.of("dataName", "单据日期", "dataType", "日期"),
                        Map.of("dataName", "单据编号", "dataType", "文本", "autoCode", true))),
                "detail", Map.of("tabs", List.of(Map.of(
                        "key", "items",
                        "calc", List.of(Map.of("target", "金额", "formula", "数量 * 单价", "round", 2)),
                        "fields", List.of(
                                Map.of("dataName", "数量", "dataType", "小数"),
                                Map.of("dataName", "报价单价", "dataType", "小数"),
                                Map.of("dataName", "税率%", "dataType", "小数"),
                                Map.of("dataName", "含税单价", "dataType", "小数"),
                                Map.of("dataName", "金额", "dataType", "小数", "computed", true),
                                Map.of("dataName", "含税金额", "dataType", "小数", "computed", true),
                                Map.of("dataName", "现存量", "dataType", "小数", "computed", true))))))));
        return panel;
    }
}
