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

@ExtendWith(MockitoExtension.class)
class SalesFinancePanelRegistryTest {

    @Mock private PanelConfigMapper panelMapper;

    private final ObjectMapper json = new ObjectMapper();

    @Test
    void registersCompleteSalesFinancePanelContracts() throws Exception {
        new SalesFinancePanelRegistry(panelMapper, json).syncPanelConfigs();

        ArgumentCaptor<PanelConfig> inserted = ArgumentCaptor.forClass(PanelConfig.class);
        verify(panelMapper, times(3)).insert(inserted.capture());
        Map<String, PanelConfig> configs = inserted.getAllValues().stream()
                .collect(Collectors.toMap(PanelConfig::getPanelCode, Function.identity()));

        assertEquals(List.of("EXPENSE", "SALE_COST_ALLOC", "SALE_INVOICE"),
                configs.keySet().stream().sorted().toList());
        assertPanel(configs.get("SALE_INVOICE"), "销售发票", "价税合计", "选销货单");
        assertPanel(configs.get("EXPENSE"), "费用单", "含税金额", null);
        assertPanel(configs.get("SALE_COST_ALLOC"), "销售费用分摊单", "分摊金额", "选费用单");

        Map<String, Object> expense = parse(configs.get("EXPENSE"));
        List<String> generateActions = groups(expense).stream()
                .filter(group -> "生单".equals(group.get("name")))
                .flatMap(group -> strings(group.get("actions")).stream()).toList();
        assertEquals(List.of("生成销售费用分摊单", "生成采购费用分摊单"), generateActions);

        Map<String, Object> allocation = parse(configs.get("SALE_COST_ALLOC"));
        Map<String, Object> select = map(map(allocation.get("selectConfigs")).get("选费用单"));
        assertEquals("EXPENSE", select.get("source"));
        assertEquals("销售费用", map(select.get("condition")).get("费用类型"));
        assertEquals("金额", select.get("sourceQuantityField"));
        assertEquals("分摊金额", select.get("targetQuantityField"));
    }

    private void assertPanel(PanelConfig row, String name, String detailField, String selectAction) throws Exception {
        assertNotNull(row);
        assertEquals(name, row.getPanelName());
        assertEquals("单据", row.getCategory());
        assertEquals(SalesFinancePanelRegistry.VERSION, row.getVersion());
        Map<String, Object> config = parse(row);
        Map<String, Object> metadata = map(config.get("metadata"));
        assertEquals(row.getPanelCode(), metadata.get("panelCode"));
        assertTrue(strings(map(metadata.get("panelState")).get("defaultOptions")).contains("审批中"));
        assertTrue(groups(config).stream().anyMatch(group ->
                strings(group.get("actions")).contains("提交审批")));
        List<Map<String, Object>> tabs = maps(map(config.get("detail")).get("tabs"));
        assertTrue(maps(tabs.get(0).get("fields")).stream()
                .anyMatch(field -> detailField.equals(field.get("dataName"))));
        if (selectAction != null) {
            assertTrue(map(config.get("selectConfigs")).containsKey(selectAction));
        }
    }

    private List<Map<String, Object>> groups(Map<String, Object> config) {
        return maps(map(config.get("metadata")).get("buttonGroups"));
    }

    private Map<String, Object> parse(PanelConfig row) throws Exception {
        return json.readValue(row.getConfig(), new TypeReference<>() {});
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
