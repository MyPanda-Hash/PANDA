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
 * 委外加工费用单的自动计算配置。
 *
 * <p>修复前的缺陷：明细配置的 calc 写的是 `金额 = 数量 * 单价`，但这张单的列叫
 * 「费用金额」「委外单价」，两个变量都不存在，evaluateExpr 直接抛错被吞成 0 ——
 * 表现为「填了数量和单价，费用金额永远是 0」；表头「费用合计」也没有任何汇总规则。
 */
@ExtendWith(MockitoExtension.class)
class OutsourceFeePanelRegistryTest {

    @Mock private PanelConfigMapper panelMapper;

    private final ObjectMapper json = new ObjectMapper();

    private Map<String, Object> feeConfig() {
        List<Map<String, Object>> fields = new ArrayList<>();
        fields.add(field("费用项目", "下拉框"));
        fields.add(field("产品名称", "参照"));
        fields.add(field("计量单位", "下拉框"));
        fields.add(field("数量", "小数"));
        fields.add(field("委外单价", "小数"));
        fields.add(field("费用金额", "小数"));
        fields.add(field("备注", "文本"));
        Map<String, Object> tab = new LinkedHashMap<>();
        tab.put("key", "items");
        tab.put("label", "明细");
        tab.put("fields", fields);
        tab.put("calc", new ArrayList<>(List.of(Map.of("target", "金额", "formula", "数量 * 单价", "round", 2))));
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("panelCode", "OUTSOURCE_FEE");
        Map<String, Object> config = new LinkedHashMap<>();
        config.put("metadata", metadata);
        config.put("dataSchema", Map.of("fields", List.of(field("费用合计", "小数"))));
        config.put("detail", Map.of("tabs", new ArrayList<>(List.of(tab))));
        return config;
    }

    private static Map<String, Object> field(String name, String type) {
        Map<String, Object> f = new LinkedHashMap<>();
        f.put("dataName", name);
        f.put("dataType", type);
        return f;
    }

    @Test
    @DisplayName("calc 的 target/formula 变量必须是这张单真实存在的列")
    void calcUsesExistingColumns() {
        Map<String, Object> config = feeConfig();

        OutsourceFeePanelRegistry.upgrade(config);

        List<Map<String, Object>> tabs = listOfMaps(map(config.get("detail")).get("tabs"));
        List<Map<String, Object>> calc = listOfMaps(tabs.get(0).get("calc"));
        assertEquals(1, calc.size(), "只保留一条金额计算规则");
        assertEquals("费用金额", calc.get(0).get("target"));
        assertEquals("数量 * 委外单价", calc.get(0).get("formula"));
        assertEquals(2, calc.get(0).get("round"));
    }

    @Test
    @DisplayName("表头 费用合计 配成明细汇总字段（只读金额信息）")
    void headerTotalIsSummaryOfDetail() {
        Map<String, Object> config = feeConfig();

        OutsourceFeePanelRegistry.upgrade(config);

        Map<String, Object> summary = map(config.get("formSummary"));
        assertFalse(summary.isEmpty(), "要配 formSummary，表头才会显示汇总");
        assertEquals("金额信息", summary.get("title"));
        List<?> fields = (List<?>) summary.get("fields");
        assertTrue(fields.contains("费用合计"), "费用合计要在汇总区：" + fields);
    }

    @Test
    @DisplayName("费用金额标成 computed：前端锁定为只读，由 calc 算出")
    void amountFieldIsComputed() {
        Map<String, Object> config = feeConfig();

        OutsourceFeePanelRegistry.upgrade(config);

        List<Map<String, Object>> fields = listOfMaps(
                listOfMaps(map(config.get("detail")).get("tabs")).get(0).get("fields"));
        Map<String, Object> amount = fields.stream()
                .filter(f -> "费用金额".equals(String.valueOf(f.get("dataName")))).findFirst().orElseThrow();
        assertEquals(true, amount.get("computed"));
        assertEquals(false, amount.get("isRequired"), "算出来的字段不该必填");
    }

    @Test
    @DisplayName("页签补 headerCalc，让表头费用合计由明细自动汇总")
    void tabDeclaresHeaderCalc() {
        Map<String, Object> config = feeConfig();

        OutsourceFeePanelRegistry.upgrade(config);

        List<Map<String, Object>> tabs = listOfMaps(map(config.get("detail")).get("tabs"));
        List<Map<String, Object>> headerCalc = listOfMaps(tabs.get(0).get("headerCalc"));
        assertEquals(1, headerCalc.size(), "一条表头汇总规则");
        assertEquals("费用合计", headerCalc.get(0).get("target"));
        assertEquals("费用金额", headerCalc.get(0).get("source"));
        assertEquals("sum", headerCalc.get(0).get("op"));
    }

    @Test
    @DisplayName("幂等：重复执行不会重复追加 calc 或汇总字段")
    void isIdempotent() {
        Map<String, Object> config = feeConfig();

        OutsourceFeePanelRegistry.upgrade(config);
        OutsourceFeePanelRegistry.upgrade(config);

        List<Map<String, Object>> tabs = listOfMaps(map(config.get("detail")).get("tabs"));
        assertEquals(1, listOfMaps(tabs.get(0).get("calc")).size());
        assertEquals(1, listOfMaps(tabs.get(0).get("headerCalc")).size());
        assertEquals(1, ((List<?>) map(config.get("formSummary")).get("fields")).size());
    }

    @Test
    @DisplayName("启动时把修正写回面板配置")
    void syncWritesBackToPanel() throws Exception {
        PanelConfig row = new PanelConfig();
        row.setPanelCode("OUTSOURCE_FEE");
        row.setConfig(json.writeValueAsString(feeConfig()));
        when(panelMapper.selectOne(any())).thenReturn(row);

        new OutsourceFeePanelRegistry(panelMapper, json).syncPanelConfigs();

        ArgumentCaptor<PanelConfig> updated = ArgumentCaptor.forClass(PanelConfig.class);
        verify(panelMapper, atLeastOnce()).updateById(updated.capture());
        Map<String, Object> config = json.readValue(updated.getValue().getConfig(), new TypeReference<>() { });
        assertEquals("费用金额",
                listOfMaps(listOfMaps(map(config.get("detail")).get("tabs")).get(0).get("calc")).get(0).get("target"));
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
