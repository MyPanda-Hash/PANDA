package com.mes.service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.PanelConfig;
import com.mes.mapper.PanelConfigMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.List;
import java.util.Map;
import java.util.function.Function;
import java.util.stream.Collectors;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;

/**
 * 应收应付五个面板的契约校验：收款单、付款单、客户对账单、供应商对账单、账龄分析表。
 * 面板必须满足通用 PanelRuntime 配置结构（metadata + dataSchema + detail）。
 */
@ExtendWith(MockitoExtension.class)
class FinancePanelRegistryTest {

    @Mock private PanelConfigMapper panelMapper;

    private final ObjectMapper json = new ObjectMapper();

    @Test
    void registersFiveFinancePanels() throws Exception {
        new FinancePanelRegistry(panelMapper, json).syncPanelConfigs();

        ArgumentCaptor<PanelConfig> inserted = ArgumentCaptor.forClass(PanelConfig.class);
        verify(panelMapper, times(5)).insert(inserted.capture());
        Map<String, PanelConfig> configs = inserted.getAllValues().stream()
                .collect(Collectors.toMap(PanelConfig::getPanelCode, Function.identity()));

        assertEquals(List.of("AGING_ANALYSIS", "RECEIPT", "RECEIPT_PAYABLE", "SETTLE_CUSTOMER", "SETTLE_SUPPLIER"),
                configs.keySet().stream().sorted().toList());
    }

    @Test
    void receiptAndPaymentAreVouchersWithWriteOffAction() throws Exception {
        new FinancePanelRegistry(panelMapper, json).syncPanelConfigs();

        ArgumentCaptor<PanelConfig> inserted = ArgumentCaptor.forClass(PanelConfig.class);
        verify(panelMapper, times(5)).insert(inserted.capture());
        Map<String, PanelConfig> configs = inserted.getAllValues().stream()
                .collect(Collectors.toMap(PanelConfig::getPanelCode, Function.identity()));

        assertVoucher(configs.get("RECEIPT"), "收款单", "客户");
        assertVoucher(configs.get("RECEIPT_PAYABLE"), "付款单", "供应商");

        // 核销动作必须在按钮组里
        Map<String, Object> receipt = parse(configs.get("RECEIPT"));
        List<String> actions = groups(receipt).stream()
                .flatMap(g -> strings(g.get("actions")).stream()).toList();
        assertTrue(actions.contains("核销"), "收款单缺少核销动作");
        assertTrue(actions.contains("反核销"), "收款单缺少反核销动作");
    }

    @Test
    void statementsAndAgingAreReportPanels() throws Exception {
        new FinancePanelRegistry(panelMapper, json).syncPanelConfigs();

        ArgumentCaptor<PanelConfig> inserted = ArgumentCaptor.forClass(PanelConfig.class);
        verify(panelMapper, times(5)).insert(inserted.capture());
        Map<String, PanelConfig> configs = inserted.getAllValues().stream()
                .collect(Collectors.toMap(PanelConfig::getPanelCode, Function.identity()));

        for (String code : List.of("SETTLE_CUSTOMER", "SETTLE_SUPPLIER", "AGING_ANALYSIS")) {
            PanelConfig row = configs.get(code);
            assertNotNull(row, code + " 未注册");
            assertEquals("报表", row.getCategory(), code + " 应为报表类");
            Map<String, Object> cfg = parse(row);
            List<Map<String, Object>> pages = maps(map(map(cfg.get("metadata")).get("panelPageDto"))
                    .get("tablePages"));
            assertTrue(maps(pages.get(0).get("gridTabs")).size() > 0, code + " 缺少列表页签");
        }
    }

    private void assertVoucher(PanelConfig row, String name, String partnerField) throws Exception {
        assertNotNull(row, name + " 未注册");
        assertEquals(name, row.getPanelName());
        assertEquals("单据", row.getCategory());
        Map<String, Object> cfg = parse(row);
        Map<String, Object> schema = map(cfg.get("dataSchema"));
        List<Map<String, Object>> fields = maps(schema.get("fields"));
        assertTrue(fields.stream().anyMatch(f -> partnerField.equals(f.get("dataName"))),
                name + " 缺少字段 " + partnerField);
        assertTrue(fields.stream().anyMatch(f -> "金额".equals(f.get("dataName"))),
                name + " 缺少金额字段");
    }

    private Map<String, Object> parse(PanelConfig row) throws Exception {
        return json.readValue(row.getConfig(), new TypeReference<>() {});
    }

    private List<Map<String, Object>> groups(Map<String, Object> config) {
        return maps(map(config.get("metadata")).get("buttonGroups"));
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> map(Object value) {
        return (Map<String, Object>) value;
    }

    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> maps(Object value) {
        return (List<Map<String, Object>>) value;
    }

    @SuppressWarnings("unchecked")
    private List<String> strings(Object value) {
        return (List<String>) value;
    }
}
