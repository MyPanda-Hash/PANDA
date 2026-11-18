package com.mes.service.generation;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import com.mes.entity.FormData;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;

/**
 * 生单源单快照：把「查源单 → 判空 → 判状态 → 解析表头与明细」的重复头部收敛后，
 * 行为必须与原先逐行手写完全一致，否则生单会在无人察觉处改变文案或放行条件。
 */
class SourceDocumentTest {

    private static final ObjectMapper JSON = new ObjectMapper();

    /** 与 PxService.parseData 等价：空串/异常都退化成空 Map，绝不返回 null。 */
    private static Map<String, Object> parse(String s) {
        if (s == null || s.isBlank()) return new HashMap<>();
        try {
            return JSON.readValue(s, new TypeReference<Map<String, Object>>() {});
        } catch (Exception e) {
            return new HashMap<>();
        }
    }

    private static FormData doc(String panel, String no, String status, String data, String detail) {
        FormData f = new FormData();
        f.setPanelCode(panel);
        f.setFormNo(no);
        f.setStatus(status);
        f.setData(data);
        f.setDetailData(detail);
        return f;
    }

    @Test
    @DisplayName("源单为 null 时抛「<名称>不存在：<单号>」，与旧实现文案一致")
    void nullSourceThrowsNotExist() {
        IllegalArgumentException e = assertThrows(IllegalArgumentException.class,
                () -> SourceDocument.of(null, "费用单", "FY-1", SourceDocumentTest::parse));
        assertEquals("费用单不存在：FY-1", e.getMessage());
    }

    @Test
    @DisplayName("正常源单：表头与明细都能解析出来，且保留原实体")
    void parsesHeadAndDetail() {
        FormData raw = doc("EXPENSE", "FY-1", "已审核",
                "{\"费用类型\":\"销售费用\",\"部门\":\"销售部\"}",
                "{\"items\":[{\"费用项目\":\"差旅\",\"金额\":100}]}");
        SourceDocument d = SourceDocument.of(raw, "费用单", "FY-1", SourceDocumentTest::parse);

        assertSame(raw, d.raw(), "raw() 必须返回同一实体，调用方还要读状态");
        assertEquals("销售费用", d.head().get("费用类型"));
        assertEquals(1, d.rows("items").size());
        assertEquals(100, ((Number) d.rows("items").get(0).get("金额")).intValue());
    }

    @Test
    @DisplayName("状态不符时抛调用方给的文案")
    void requireStatusUsesCallerMessage() {
        FormData raw = doc("EXPENSE", "FY-1", "草稿", "{}", "{}");
        IllegalStateException e = assertThrows(IllegalStateException.class,
                () -> SourceDocument.of(raw, "费用单", "FY-1", SourceDocumentTest::parse)
                        .requireStatus("已审核", "仅已审核费用单可生成销售费用分摊单"));
        assertEquals("仅已审核费用单可生成销售费用分摊单", e.getMessage());
    }

    @Test
    @DisplayName("状态相符时 requireStatus 返回自身，可链式调用")
    void requireStatusReturnsSelfWhenMatches() {
        FormData raw = doc("EXPENSE", "FY-1", "已审核", "{}", "{}");
        SourceDocument d = SourceDocument.of(raw, "费用单", "FY-1", SourceDocumentTest::parse);
        assertSame(d, d.requireStatus("已审核", "不该触发"));
    }

    @Test
    @DisplayName("明细键缺失或类型不是 List 时返回空列表，不返回 null、不抛 NPE")
    void rowsMissingOrWrongTypeReturnsEmptyList() {
        SourceDocument d = SourceDocument.of(
                doc("EXPENSE", "FY-1", "已审核", "{}", "{\"items\":\"不是数组\"}"),
                "费用单", "FY-1", SourceDocumentTest::parse);

        assertNotNull(d.rows("items"), "缺键不能返回 null");
        assertTrue(d.rows("items").isEmpty());
        assertTrue(d.rows("products").isEmpty(), "完全不存在的键也要是空列表");
    }

    @Test
    @DisplayName("按不同明细键取值互不干扰（items / products 并存时各取各的）")
    void rowsSelectsByKey() {
        SourceDocument d = SourceDocument.of(
                doc("MANU_ORDER", "MO-1", "已审核", "{}",
                        "{\"items\":[{\"a\":1}],\"products\":[{\"b\":1},{\"b\":2}]}"),
                "生产加工单", "MO-1", SourceDocumentTest::parse);

        assertEquals(1, d.rows("items").size());
        assertEquals(2, d.rows("products").size());
    }

    @Test
    @DisplayName("源单 data/detailData 为空串时，表头与明细退化成空 Map 而不是 null")
    void blankJsonDegradesToEmptyMaps() {
        SourceDocument d = SourceDocument.of(doc("EXPENSE", "FY-1", "已审核", "", ""),
                "费用单", "FY-1", SourceDocumentTest::parse);

        assertNotNull(d.head());
        assertNotNull(d.detail());
        assertTrue(d.head().isEmpty());
        assertTrue(d.detail().isEmpty());
    }

    @Test
    @DisplayName("解析器返回 null 时兜底成空 Map，避免下游 getOrDefault 抛 NPE")
    void nullParserResultBecomesEmptyMap() {
        SourceDocument d = SourceDocument.of(doc("EXPENSE", "FY-1", "已审核", "x", "y"),
                "费用单", "FY-1", ignored -> null);

        assertNotNull(d.head());
        assertNotNull(d.detail());
    }

    @Test
    @DisplayName("判空先于判状态：源单不存在时不该报状态错误")
    void existenceCheckedBeforeStatus() {
        // 旧实现里这两句是相邻的 if，顺序颠倒会让「单据不存在」变成「状态不符」，误导排查
        IllegalArgumentException e = assertThrows(IllegalArgumentException.class,
                () -> SourceDocument.of(null, "费用单", "FY-9", SourceDocumentTest::parse)
                        .requireStatus("已审核", "不该走到这里"));
        assertEquals("费用单不存在：FY-9", e.getMessage());
    }
}
