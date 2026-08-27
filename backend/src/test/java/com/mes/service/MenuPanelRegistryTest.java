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
import java.util.Set;
import java.util.function.Function;
import java.util.stream.Collectors;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;

@ExtendWith(MockitoExtension.class)
class MenuPanelRegistryTest {

    private static final Set<String> QUERY_PANELS = Set.of(
            "PU_REQ_ANALYSIS", "SERIAL_STATUS", "SERIAL_TRACE");

    @Mock private PanelConfigMapper panelMapper;

    private final ObjectMapper json = new ObjectMapper();

    @Test
    void registersEveryMissingMenuPanelWithRenderableContracts() throws Exception {
        new MenuPanelRegistry(panelMapper, json).syncPanelConfigs();

        ArgumentCaptor<PanelConfig> inserted = ArgumentCaptor.forClass(PanelConfig.class);
        verify(panelMapper, times(13)).insert(inserted.capture());
        Map<String, PanelConfig> rows = inserted.getAllValues().stream()
                .collect(Collectors.toMap(PanelConfig::getPanelCode, Function.identity()));

        assertEquals(MenuPanelRegistry.PANEL_CODES, rows.keySet());
        for (PanelConfig row : rows.values()) {
            assertEquals(MenuPanelRegistry.VERSION, row.getVersion());
            Map<String, Object> config = parse(row);
            assertFalse(maps(map(config.get("dataSchema")).get("fields")).isEmpty(), row.getPanelCode());

            if (QUERY_PANELS.contains(row.getPanelCode())) {
                assertEquals("查询", row.getCategory());
            } else {
                assertEquals("单据", row.getCategory());
                assertFalse(maps(map(config.get("detail")).get("tabs")).isEmpty(), row.getPanelCode());
                assertTrue(groups(config).stream().anyMatch(group ->
                        strings(group.get("actions")).contains("提交审批")), row.getPanelCode());
            }
        }

        Map<String, Object> outsourceOrder = parse(rows.get("OUTSOURCE_ORDER"));
        List<String> tabKeys = maps(map(outsourceOrder.get("detail")).get("tabs")).stream()
                .map(tab -> String.valueOf(tab.get("key"))).toList();
        assertEquals(List.of("products", "materials"), tabKeys);

        assertEquals("MATERIAL_REQ", map(parse(rows.get("TRANSFER")).get("selectConfig")).get("source"));
        assertEquals("EXPENSE", map(parse(rows.get("PU_COST_ALLOC")).get("selectConfig")).get("source"));
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
