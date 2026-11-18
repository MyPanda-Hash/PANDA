package com.mes.service;

import org.junit.jupiter.api.Test;

import java.time.LocalDate;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 单据编号规则：前缀-yyyyMMdd-NNN；带批次的单据追加批号段 前缀-yyyyMMdd-批号-NNN。
 *
 * <p>背景缺陷（2026-11-05 实测）：
 * <ul>
 *   <li>旧格式 前缀-yyyy-MM-dd+2位，日期带连字符且只有 2 位序号；</li>
 *   <li>BJ- 同时分配给成品报检单与报价单，两张单据会撞号；</li>
 *   <li>未登记的面板兜底成 MO-，与生产加工单撞号；</li>
 *   <li>查重语句带 panel_code 条件，注释却写「全局唯一」，实际只做到面板内唯一；</li>
 *   <li>加工单「批号」字段存的是「正常/加急」优先级，不能直接当批号段塞进单号。</li>
 * </ul>
 */
class DocNoRulesTest {

    private static final LocalDate DAY = LocalDate.of(2026, 11, 5);

    @Test
    void standardDocNoUsesCompactDateAndThreeDigitSeq() {
        assertEquals("SO-20261105-001", DocNoRules.format("SO", DAY, "", 1));
        assertEquals("MO-20261105-012", DocNoRules.format("MO", DAY, "", 12));
    }

    @Test
    void batchPanelEmbedsBatchCode() {
        assertEquals("GX-20261105-P2026A-001", DocNoRules.format("GX", DAY, "P2026A", 1));
    }

    @Test
    void batchCodeIsTakenFromBatchField() {
        assertEquals("P2026A", DocNoRules.batchSegment(Map.of("批次号", "P2026A")));
        assertEquals("P2026A", DocNoRules.batchSegment(Map.of("批号", "P2026A")));
    }

    /** 加工单「批号」当前存的是优先级词，不能进单号。 */
    @Test
    void priorityWordIsNotEmbeddedAsBatch() {
        assertEquals("", DocNoRules.batchSegment(Map.of("批号", "正常")));
        assertEquals("", DocNoRules.batchSegment(Map.of("批号", "加急")));
    }

    @Test
    void missingOrBlankBatchYieldsNoSegment() {
        assertEquals("", DocNoRules.batchSegment(Map.of()));
        assertEquals("", DocNoRules.batchSegment(Map.of("批次号", "   ")));
    }

    /** 前缀表不允许重复，否则两张不同单据会生成同一个单号。 */
    @Test
    void prefixTableHasNoDuplicatePrefix() {
        List<String> values = List.copyOf(DocNoRules.prefixes().values());
        Set<String> unique = new HashSet<>(values);
        assertEquals(values.size(), unique.size(),
                "前缀重复：" + values.stream().filter(v -> values.indexOf(v) != values.lastIndexOf(v)).distinct().toList());
    }

    /** 所有前缀必须是纯 ASCII 字母数字，单号要能被外部系统/条码安全承载。 */
    @Test
    void allPrefixesAreAsciiAlphanumeric() {
        for (Map.Entry<String, String> e : DocNoRules.prefixes().entrySet()) {
            assertTrue(e.getValue().matches("[A-Z0-9]{2,4}"),
                    e.getKey() + " 的前缀不是 2-4 位 ASCII 大写字母数字：" + e.getValue());
        }
    }

    /**
     * 未登记的面板不得兜底成 MO-：旧实现的 else 分支让任何新面板都叫 MO-，
     * 和生产加工单直接撞号。
     */
    @Test
    void unknownPanelDoesNotFallBackToManufactureOrderPrefix() {
        String prefix = DocNoRules.prefixOf("SOME_NEW_PANEL");
        assertNotEquals("MO", prefix);
        assertNotEquals("", prefix);
        assertFalse(prefix.contains("_"), "前缀不应含下划线：" + prefix);
    }

    /** 同一面板的前缀必须稳定，重复调用结果一致。 */
    @Test
    void prefixOfIsStable() {
        assertEquals(DocNoRules.prefixOf("SO_ORDER"), DocNoRules.prefixOf("SO_ORDER"));
        assertEquals("SO", DocNoRules.prefixOf("SO_ORDER"));
        assertEquals("GX", DocNoRules.prefixOf("PROCESS_REPORT"));
    }

    /** 报价单与成品报检单曾共用 BJ-，必须已拆分。 */
    @Test
    void quoteOrderAndFinishInspectUseDifferentPrefixes() {
        assertNotEquals(DocNoRules.prefixOf("QUOTE_ORDER"), DocNoRules.prefixOf("FINISH_INSPECT"));
    }

    @Test
    void batchPanelsRecognised() {
        assertTrue(DocNoRules.isBatchPanel("PROCESS_REPORT"));
        assertTrue(DocNoRules.isBatchPanel("MANU_ORDER"));
        assertFalse(DocNoRules.isBatchPanel("SO_ORDER"));
    }
}