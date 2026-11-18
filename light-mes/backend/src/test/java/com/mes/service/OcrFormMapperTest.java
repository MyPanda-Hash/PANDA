package com.mes.service;

import com.mes.ocr.OcrGateway.OcrCell;
import com.mes.ocr.OcrGateway.OcrDocument;
import com.mes.ocr.OcrGateway.OcrLine;
import com.mes.ocr.OcrGateway.OcrTable;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class OcrFormMapperTest {

    private final OcrFormMapper mapper = new OcrFormMapper();

    @Test
    void mapsOnlyEditableSchemaFieldsAndNormalizesValues() {
        Map<String, Object> config = Map.of(
                "metadata", Map.of(
                        "autoCodeField", "单据编号",
                        "panelState", Map.of("dataName", "单据状态")),
                "dataSchema", Map.of("fields", List.of(
                        field("供应商", "文本"),
                        field("单据日期", "日期"),
                        field("紧急", "是否"),
                        field("折扣", "小数"),
                        Map.of("dataName", "单据编号", "dataType", "文本", "autoCode", true),
                        Map.of("dataName", "合计金额", "dataType", "小数", "computed", true),
                        Map.of("dataName", "来源单号", "dataType", "文本", "readonly", true),
                        Map.of("dataName", "内部字段", "dataType", "文本", "hidden", true))),
                "detail", Map.of("tabs", List.of(Map.of(
                        "key", "items",
                        "fields", List.of(
                                field("存货名称", "文本"),
                                field("数量", "小数"),
                                field("是否赠品", "是否"),
                                Map.of("dataName", "金额", "dataType", "小数", "computed", true))))));
        OcrDocument document = new OcrDocument("req-1", "raw", List.of(
                new OcrLine("供应商：华东铝业", 98),
                new OcrLine("单据日期", 97),
                new OcrLine("2026年8月27日", 96),
                new OcrLine("紧急 是", 95),
                new OcrLine("折扣: 12.50", 94),
                new OcrLine("单据编号: PO-001", 99),
                new OcrLine("合计金额: 999", 99),
                new OcrLine("来源单号: SRC-1", 99)), List.of(new OcrTable(List.of(
                cell(0, 0, "存货名称"), cell(0, 1, "数量"), cell(0, 2, "是否赠品"), cell(0, 3, "金额"),
                cell(1, 0, "铝棒"), cell(1, 1, "1,200.50"), cell(1, 2, "是"), cell(1, 3, "999")))));

        var result = mapper.map(config, document);

        assertEquals("华东铝业", result.header().get("供应商"));
        assertEquals("2026-08-27", result.header().get("单据日期"));
        assertEquals(true, result.header().get("紧急"));
        assertEquals(new BigDecimal("12.50"), result.header().get("折扣"));
        assertFalse(result.header().containsKey("单据编号"));
        assertFalse(result.header().containsKey("合计金额"));
        assertFalse(result.header().containsKey("来源单号"));

        Map<String, Object> row = result.detail().get("items").get(0);
        assertEquals("铝棒", row.get("存货名称"));
        assertEquals(new BigDecimal("1200.50"), row.get("数量"));
        assertEquals(true, row.get("是否赠品"));
        assertFalse(row.containsKey("金额"));
        assertEquals(7, result.matches().size());
        assertTrue(result.warnings().isEmpty());
    }

    @Test
    void mapsDisplayNameToCanonicalFieldWithoutStealingAnotherFieldsName() {
        Map<String, Object> config = Map.of(
                "metadata", Map.of("panelCategory", "单据"),
                "dataSchema", Map.of("fields", List.of(
                        field("测试程序", "文本"),
                        Map.of("dataName", "测试程序2", "displayName", "测试程序", "dataType", "文本"),
                        Map.of("dataName", "加工单号", "displayName", "生产加工单号", "dataType", "文本"))),
                "detail", Map.of("tabs", List.of()));
        OcrDocument document = new OcrDocument("req-alias", "", List.of(
                new OcrLine("测试程序：光谱分析", 98),
                new OcrLine("生产加工单号：MO-20260827-01", 97)), List.of());

        var result = mapper.map(config, document);

        assertEquals("光谱分析", result.header().get("测试程序"));
        assertFalse(result.header().containsKey("测试程序2"));
        assertEquals("MO-20260827-01", result.header().get("加工单号"));
    }

    private Map<String, Object> field(String name, String type) {
        return Map.of("dataName", name, "dataType", type);
    }

    private OcrCell cell(int row, int column, String value) {
        return new OcrCell(row, row, column, column, value);
    }
}
