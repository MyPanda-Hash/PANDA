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

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;

@ExtendWith(MockitoExtension.class)
class QualityPanelRegistryTest {

    @Mock private PanelConfigMapper panelMapper;

    private final ObjectMapper json = new ObjectMapper();

    @Test
    void registersCapturedTraceSettingsContracts() throws Exception {
        new QualityPanelRegistry(panelMapper, json).syncPanelConfigs();

        ArgumentCaptor<PanelConfig> inserted = ArgumentCaptor.forClass(PanelConfig.class);
        verify(panelMapper, times(8)).insert(inserted.capture());
        Map<String, PanelConfig> configs = inserted.getAllValues().stream()
                .collect(java.util.stream.Collectors.toMap(PanelConfig::getPanelCode, value -> value));

        assertSetting(configs.get("COMPANY_TRACE_SETTINGS"), "QT0101", "产品基本信息");
        assertSetting(configs.get("CUSTOMER_TRACE_SETTINGS"), "QT0102", "追溯模板标题");

        PanelConfig print = configs.get("TRACE_PRINT_TEMPLATE");
        assertNotNull(print);
        assertEquals("设置", print.getCategory());
        Map<String, Object> printConfig = parse(print);
        Map<String, Object> metadata = map(printConfig.get("metadata"));
        assertEquals("QT0103", metadata.get("tplusCode"));
        List<Map<String, Object>> tabs = maps(map(printConfig.get("detail")).get("tabs"));
        assertEquals("controls", tabs.get(0).get("key"));
        List<Map<String, Object>> fields = maps(tabs.get(0).get("fields"));
        Map<String, Object> controlType = fields.stream()
                .filter(field -> "控件类型".equals(field.get("dataName"))).findFirst().orElseThrow();
        assertEquals(List.of("直线", "矩形", "静态文本", "文本框", "图片", "明细"), controlType.get("options"));
    }

    private void assertSetting(PanelConfig row, String tplusCode, String expectedField) throws Exception {
        assertNotNull(row);
        assertEquals("设置", row.getCategory());
        Map<String, Object> config = parse(row);
        Map<String, Object> metadata = map(config.get("metadata"));
        assertEquals(tplusCode, metadata.get("tplusCode"));
        assertEquals(true, metadata.get("singleDoc"));
        List<Map<String, Object>> fields = maps(map(config.get("dataSchema")).get("fields"));
        assertTrue(fields.stream().anyMatch(field -> expectedField.equals(field.get("dataName"))));
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
}
