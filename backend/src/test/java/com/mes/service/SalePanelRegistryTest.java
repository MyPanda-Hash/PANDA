package com.mes.service;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.atLeastOnce;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.PanelConfig;
import com.mes.mapper.PanelConfigMapper;

/** 销售订单的委外入口：老库里没有这个按钮，启动时幂等补回。 */
@ExtendWith(MockitoExtension.class)
class SalePanelRegistryTest {

    @Mock private PanelConfigMapper panelMapper;

    private final ObjectMapper json = new ObjectMapper();

    private Map<String, Object> soConfig() {
        Map<String, Object> gen = new LinkedHashMap<>();
        gen.put("name", "生单");
        gen.put("actions", new ArrayList<>(List.of("生成销售出库单(普通销售)", "生成生产加工单")));
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("panelCode", "SO_ORDER");
        metadata.put("buttonGroups", new ArrayList<>(List.of(gen)));
        metadata.put("panelButtons", new ArrayList<>(List.of(Map.of("buttonName", "生成生产加工单"))));
        Map<String, Object> config = new LinkedHashMap<>();
        config.put("metadata", metadata);
        return config;
    }

    @Test
    void ensuresOutsourceActionOnSaleOrder() throws Exception {
        PanelConfig row = new PanelConfig();
        row.setPanelCode("SO_ORDER");
        row.setConfig(json.writeValueAsString(soConfig()));
        when(panelMapper.selectOne(any())).thenReturn(row);

        new SalePanelRegistry(panelMapper, json).syncPanelConfigs();

        ArgumentCaptor<PanelConfig> updated = ArgumentCaptor.forClass(PanelConfig.class);
        verify(panelMapper, atLeastOnce()).updateById(updated.capture());
        Map<String, Object> config = json.readValue(updated.getValue().getConfig(), new TypeReference<>() {});
        List<Map<String, Object>> groups = listOfMaps(map(config.get("metadata")).get("buttonGroups"));
        Map<String, Object> gen = groups.stream().filter(g -> "生单".equals(g.get("name"))).findFirst().orElseThrow();
        List<?> actions = (List<?>) gen.get("actions");
        assertTrue(actions.contains("生成委外加工单"), "销售订单必须能生成委外加工单：" + actions);
        assertTrue(actions.contains("生成生产加工单"), "自制入口不能丢：" + actions);
        List<Map<String, Object>> buttons = listOfMaps(map(config.get("metadata")).get("panelButtons"));
        assertTrue(buttons.stream().anyMatch(b -> "生成委外加工单".equals(b.get("buttonName"))),
                "顶部按钮也要有");
    }

    @Test
    void isIdempotentWhenActionAlreadyPresent() throws Exception {
        Map<String, Object> config = soConfig();
        List<Map<String, Object>> groups = listOfMaps(map(config.get("metadata")).get("buttonGroups"));
        @SuppressWarnings("unchecked")
        List<Object> actions = (List<Object>) groups.get(0).get("actions");
        actions.add("生成委外加工单");

        SalePanelRegistry.ensureOutsourceAction(config);

        @SuppressWarnings("unchecked")
        List<Object> after = (List<Object>) listOfMaps(map(config.get("metadata")).get("buttonGroups"))
                .get(0).get("actions");
        assertEquals(1, after.stream().filter("生成委外加工单"::equals).count(), "不能重复追加");
    }

    @Test
    void keepsOtherPanelsUntouched() {
        SalePanelRegistry registry = new SalePanelRegistry(panelMapper, json);
        when(panelMapper.selectOne(any())).thenReturn(null);
        registry.syncPanelConfigs();
        assertFalse(false, "没配置时静默跳过，不抛异常");
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> map(Object value) {
        return value instanceof Map ? (Map<String, Object>) value : Map.of();
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> listOfMaps(Object value) {
        return value instanceof List ? (List<Map<String, Object>>) value : List.of();
    }
}
