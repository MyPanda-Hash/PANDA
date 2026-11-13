package com.mes.service;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

/**
 * 内置计量单位库：覆盖要够全、补齐要幂等、已有配置不能被覆盖。
 */
class UomSeedRegistryTest {

    private static Map<String, Object> unit(String no, String name, String type, Object ratio) {
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("编号", no);
        row.put("计量单位编码", "U" + no.substring(4));
        row.put("计量单位名称", name);
        row.put("单位类型", type);
        row.put("主单位", name);
        row.put("换算率", ratio);
        return row;
    }

    @Test
    @DisplayName("内置库覆盖常见量纲：长度/面积/体积/容积/重量/数量/包装/时间/工艺")
    void libraryCoversCommonDimensions() {
        List<String> units = UomSeedRegistry.UNIT_LIBRARY;
        assertTrue(units.size() >= 60, "内置单位太少：" + units.size());
        assertEquals(units.size(), units.stream().distinct().count(), "内置库有重复名称");
        for (String expected : List.of("毫米(mm)", "米(m)", "立方米(m³)", "升(L)",
                "克(g)", "千克(kg)", "吨(t)", "件", "套", "台", "箱", "托盘",
                "小时(h)", "摄氏度(℃)", "兆帕(MPa)")) {
            assertTrue(units.contains(expected), "内置库缺少：" + expected);
        }
    }

    @Test
    @DisplayName("空档案：整套补齐，编号从 UOM-001 起")
    void seedsIntoEmptyArchive() {
        List<Map<String, Object>> existing = new ArrayList<>();

        List<Map<String, Object>> added = UomSeedRegistry.merge(existing);

        assertEquals(UomSeedRegistry.UNIT_LIBRARY.size(), added.size());
        assertEquals(existing.size(), added.size(), "新增行要并进原列表");
        assertEquals("UOM-001", added.get(0).get("编号"));
        assertEquals("U01", added.get(0).get("计量单位编码"));
        assertEquals("单计量", added.get(0).get("单位类型"));
        assertEquals(1, added.get(0).get("换算率"));
        assertEquals("毫米(mm)", added.get(0).get("主单位"), "主单位缺省取名称");
    }

    @Test
    @DisplayName("已有单位按名称判重：只补缺，不改动已有行（主单位/换算率保留）")
    void keepsExistingEntries() {
        List<Map<String, Object>> existing = new ArrayList<>();
        Map<String, Object> kg = unit("UOM-002", "千克(kg)", "多计量", 1000);
        existing.add(kg);

        List<Map<String, Object>> added = UomSeedRegistry.merge(existing);

        assertEquals(UomSeedRegistry.UNIT_LIBRARY.size() - 1, added.size());
        assertFalse(added.stream().anyMatch(r -> "千克(kg)".equals(r.get("计量单位名称"))));
        assertEquals("多计量", kg.get("单位类型"), "已有行不能被覆盖");
        assertEquals(1000, kg.get("换算率"));
    }

    @Test
    @DisplayName("幂等：连跑两次第二次零新增")
    void secondRunAddsNothing() {
        List<Map<String, Object>> existing = new ArrayList<>();
        UomSeedRegistry.merge(existing);

        List<Map<String, Object>> second = UomSeedRegistry.merge(existing);

        assertTrue(second.isEmpty(), "第二次不应再加");
        assertEquals(UomSeedRegistry.UNIT_LIBRARY.size(), existing.size());
    }

    @Test
    @DisplayName("编号接着现有最大序号往上排，不覆盖已有编号")
    void continuesNumbering() {
        List<Map<String, Object>> existing = new ArrayList<>();
        existing.add(unit("UOM-006", "件", "单计量", 1));

        List<Map<String, Object>> added = UomSeedRegistry.merge(existing);

        assertFalse(added.stream().anyMatch(r -> "件".equals(r.get("计量单位名称"))));
        assertEquals("UOM-007", added.get(0).get("编号"));
        assertEquals("U07", added.get(0).get("计量单位编码"));
    }
}
