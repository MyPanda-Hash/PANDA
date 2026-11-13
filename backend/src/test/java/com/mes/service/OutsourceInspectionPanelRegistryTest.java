package com.mes.service;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.atLeastOnce;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.PanelConfig;
import com.mes.mapper.PanelConfigMapper;

/**
 * 委外检验单面板：委外回厂检验独立成单，必须自动建出配置（新环境不必手工建面板）。
 */
@ExtendWith(MockitoExtension.class)
class OutsourceInspectionPanelRegistryTest {

    @Mock private PanelConfigMapper panelMapper;

    private final ObjectMapper json = new ObjectMapper();

    @Test
    @DisplayName("没有该面板时插一条：编码/名称/审批状态/检验明细字段齐全")
    void createsPanelWhenMissing() throws Exception {
        when(panelMapper.selectOne(any())).thenReturn(null);

        new OutsourceInspectionPanelRegistry(panelMapper, json).syncPanelConfigs();

        ArgumentCaptor<PanelConfig> captor = ArgumentCaptor.forClass(PanelConfig.class);
        verify(panelMapper, atLeastOnce()).insert(captor.capture());
        PanelConfig row = captor.getValue();
        assertEquals("OUTSOURCE_INSPECTION", row.getPanelCode());
        assertEquals("委外检验单", row.getPanelName());
        assertEquals("单据", row.getCategory());

        Map<String, Object> config = json.readValue(row.getConfig(), new TypeReference<>() { });
        List<Map<String, Object>> groups = listOfMaps(map(config.get("metadata")).get("buttonGroups"));
        Map<String, Object> gen = groups.stream().filter(g -> "生单".equals(g.get("name")))
                .findFirst().orElseThrow();
        assertTrue(((List<?>) gen.get("actions")).contains("生成委外入库单"), "要有生成委外入库单");

        List<Map<String, Object>> headerFields = listOfMaps(map(config.get("dataSchema")).get("fields"));
        List<String> headerNames = headerFields.stream()
                .map(f -> String.valueOf(f.get("dataName"))).toList();
        assertTrue(headerNames.contains("委外加工单号"), "表头要能挂到委外加工单：" + headerNames);
        assertTrue(headerNames.contains("委外供应商"));
        assertTrue(headerNames.contains("检验日期"));

        List<Map<String, Object>> tabs = listOfMaps(map(config.get("detail")).get("tabs"));
        List<String> itemNames = listOfMaps(tabs.get(0).get("fields")).stream()
                .map(f -> String.valueOf(f.get("dataName"))).toList();
        for (String required : List.of("产品编码", "报检数量", "检验数量", "合格数量",
                "不合格数量", "检验结果判定", "处置方式", "委外单价")) {
            assertTrue(itemNames.contains(required), "明细缺列 " + required + "：" + itemNames);
        }
        Object state = map(map(config.get("metadata")).get("panelState")).get("defaultOptions");
        assertTrue(state instanceof List<?> options && options.contains("审批中"), "要走审批：" + state);
    }

    @Test
    @DisplayName("已有面板时不覆盖管理员改动，只补缺失的生单动作")
    void keepsExistingAndEnsuresAction() throws Exception {
        PanelConfig existing = new PanelConfig();
        existing.setPanelCode("OUTSOURCE_INSPECTION");
        existing.setPanelName("委外检验单（改过名）");
        existing.setConfig(json.writeValueAsString(Map.of(
                "metadata", Map.of(
                        "panelCode", "OUTSOURCE_INSPECTION",
                        "buttonGroups", java.util.List.of(Map.of("name", "审批", "actions", java.util.List.of("审批通过")))))));
        when(panelMapper.selectOne(any())).thenReturn(existing);

        new OutsourceInspectionPanelRegistry(panelMapper, json).syncPanelConfigs();

        ArgumentCaptor<PanelConfig> captor = ArgumentCaptor.forClass(PanelConfig.class);
        verify(panelMapper, atLeastOnce()).updateById(captor.capture());
        assertEquals("委外检验单（改过名）", captor.getValue().getPanelName(), "不改已有名称");
        Map<String, Object> config = json.readValue(captor.getValue().getConfig(), new TypeReference<>() { });
        List<Map<String, Object>> groups = listOfMaps(map(config.get("metadata")).get("buttonGroups"));
        assertTrue(groups.stream().anyMatch(g -> "生单".equals(g.get("name"))
                && ((List<?>) g.get("actions")).contains("生成委外入库单")), "补上生单入口");
    }

    @Test
    @DisplayName("单号前缀 WJ：委外检验单与来料检验单 JY 区分开")
    void docNoPrefix() {
        assertEquals("WJ", DocNoRules.prefixOf("OUTSOURCE_INSPECTION"));
        assertFalse("JY".equals(DocNoRules.prefixOf("OUTSOURCE_INSPECTION")));
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
