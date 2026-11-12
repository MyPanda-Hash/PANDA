package com.mes.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.PanelConfig;
import com.mes.mapper.PanelConfigMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.atLeastOnce;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/** 序列号落地：存货档案加开关、出入库单据加序列号页签，且幂等。 */
@ExtendWith(MockitoExtension.class)
class SerialPanelUpgradeTest {

    @Mock private PanelConfigMapper panelMapper;

    private final ObjectMapper json = new ObjectMapper();

    private PanelConfig row(String code, Map<String, Object> config) throws Exception {
        PanelConfig r = new PanelConfig();
        r.setId(1L);
        r.setPanelCode(code);
        r.setConfig(json.writeValueAsString(config));
        return r;
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> asMap(Object value) {
        return value instanceof Map<?, ?> m ? (Map<String, Object>) m : new LinkedHashMap<>();
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> asList(Object value) {
        List<Map<String, Object>> out = new ArrayList<>();
        if (value instanceof List<?> list) {
            for (Object item : list) if (item instanceof Map<?, ?> m) out.add((Map<String, Object>) m);
        }
        return out;
    }

    private Map<String, Object> baseConfig() {
        Map<String, Object> grid = new LinkedHashMap<>();
        grid.put("label", "明细");
        grid.put("columns", new ArrayList<>(List.of("存货编码", "存货名称")));
        List<Map<String, Object>> gridTabs = new ArrayList<>();
        gridTabs.add(grid);
        Map<String, Object> tablePage = new LinkedHashMap<>();
        tablePage.put("gridTabs", gridTabs);
        List<Map<String, Object>> tablePages = new ArrayList<>();
        tablePages.add(tablePage);
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("panelPageDto", Map.of("tablePages", tablePages));

        Map<String, Object> field = new LinkedHashMap<>();
        field.put("dataName", "存货编码");
        field.put("dataType", "文本");
        List<Map<String, Object>> fields = new ArrayList<>();
        fields.add(field);
        Map<String, Object> itemsTab = new LinkedHashMap<>();
        itemsTab.put("key", "items");
        itemsTab.put("label", "明细");
        itemsTab.put("fields", fields);
        List<Map<String, Object>> tabs = new ArrayList<>();
        tabs.add(itemsTab);
        Map<String, Object> detail = new LinkedHashMap<>();
        detail.put("tabs", tabs);

        Map<String, Object> config = new LinkedHashMap<>();
        config.put("metadata", metadata);
        config.put("detail", detail);
        return config;
    }

    @Test
    void inventoryGetsSerialFlagAndColumn() throws Exception {
        PanelConfig inv = row("INV", baseConfig());
        when(panelMapper.selectOne(any())).thenReturn(inv);

        new SerialPanelUpgrade(panelMapper, json).upgradeInventory();

        verify(panelMapper, atLeastOnce()).updateById(org.mockito.ArgumentMatchers.<PanelConfig>any());
        Map<String, Object> saved = json.readValue(inv.getConfig(), Map.class);
        List<Map<String, Object>> tabs = asList(asMap(saved.get("detail")).get("tabs"));
        List<Map<String, Object>> fields = asList(tabs.get(0).get("fields"));
        Map<String, Object> flag = fields.stream()
                .filter(f -> "序列号管理".equals(f.get("dataName"))).findFirst().orElse(null);
        assertNotNull(flag, "存货档案要有「序列号管理」开关");
        assertEquals("否", flag.get("defaultValue"));
        Map<String, Object> metadata = asMap(saved.get("metadata"));
        Map<String, Object> pageDto = asMap(metadata.get("panelPageDto"));
        List<Map<String, Object>> tablePages = asList(pageDto.get("tablePages"));
        List<Map<String, Object>> grids = asList(tablePages.get(0).get("gridTabs"));
        assertTrue(((List<?>) grids.get(0).get("columns")).contains("序列号管理"), "列表能直接看到开关");
    }

    @Test
    void documentsGetSerialTab() throws Exception {
        PanelConfig in = row("PURCHASE_IN", baseConfig());
        when(panelMapper.selectOne(any())).thenReturn(in);

        new SerialPanelUpgrade(panelMapper, json).upgradeDocument("PURCHASE_IN", true);

        Map<String, Object> saved = json.readValue(in.getConfig(), Map.class);
        List<Map<String, Object>> tabs = asList(asMap(saved.get("detail")).get("tabs"));
        Map<String, Object> serialTab = tabs.stream()
                .filter(t -> "serials".equals(t.get("key"))).findFirst().orElseThrow();
        List<Map<String, Object>> fields = asList(serialTab.get("fields"));
        assertTrue(fields.stream().anyMatch(f -> "序列号".equals(f.get("dataName"))));
        assertTrue(fields.stream().anyMatch(f -> "仓库".equals(f.get("dataName"))), "入库登记带仓库");
    }

    @Test
    void upgradeIsIdempotent() throws Exception {
        PanelConfig in = row("SALE_OUT", baseConfig());
        when(panelMapper.selectOne(any())).thenReturn(in);
        SerialPanelUpgrade upgrade = new SerialPanelUpgrade(panelMapper, json);

        upgrade.upgradeDocument("SALE_OUT", false);
        String first = in.getConfig();
        upgrade.upgradeDocument("SALE_OUT", false);
        assertEquals(first, in.getConfig(), "重复启动不能重复加页签");
    }
}
