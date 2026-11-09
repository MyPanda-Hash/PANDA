package com.mes.service;

import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;

/**
 * 检验单不合格退回：退回数量只取「不合格处理」页签中处理方式=退回的处理数量，
 * 没有该页签时回退到表头行的验收退回数量/不合格数量。
 */
class PxServicePurchaseReturnTest {

    @Test
    void usesReturnDispositionQuantity() {
        Map<String, Object> line = Map.of("不合格数量", 5, "验收退回数量", 3);
        List<Map<String, Object>> unqualified = List.of(
                Map.of("关联明细行号", 1, "处理方式", "退回", "处理数量", 2),
                Map.of("关联明细行号", 1, "处理方式", "报废", "处理数量", 1),
                Map.of("关联明细行号", 2, "处理方式", "退回", "处理数量", 9));

        assertEquals(2.0, PxService.purchaseReturnQuantity(line, unqualified, 1), 0.0001);
    }

    @Test
    void capsAtRejectedQuantity() {
        Map<String, Object> line = Map.of("不合格数量", 2);
        List<Map<String, Object>> unqualified = List.of(
                Map.of("关联明细行号", 1, "处理方式", "退回", "处理数量", 5));

        assertEquals(2.0, PxService.purchaseReturnQuantity(line, unqualified, 1), 0.0001);
    }

    @Test
    void fallsBackToRejectedQuantityWithoutDispositionTab() {
        Map<String, Object> line = Map.of("不合格数量", 4);

        assertEquals(4.0, PxService.purchaseReturnQuantity(line, null, 1), 0.0001);
    }

    @Test
    void emptyDispositionTabIsAuthoritativeZero() {
        Map<String, Object> line = Map.of("不合格数量", 4);

        assertEquals(0.0, PxService.purchaseReturnQuantity(line, List.of(), 1), 0.0001);
    }

    @Test
    void returnsZeroWhenNothingRejected() {
        Map<String, Object> line = Map.of("合格数量", 10, "不合格数量", 0);

        assertEquals(0.0, PxService.purchaseReturnQuantity(line, List.of(), 1), 0.0001);
    }

    @Test
    void derivesDispositionQuantitiesPerLine() {
        Map<String, Object> first = new HashMap<>(Map.of("合格数量", 7, "不合格数量", 3));
        Map<String, Object> second = new HashMap<>(Map.of("合格数量", 10, "不合格数量", 0));
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", new ArrayList<>(List.of(first, second)));
        detail.put("unqualified", List.of(
                Map.of("关联明细行号", 1, "处理方式", "退回", "处理数量", 2),
                Map.of("关联明细行号", 1, "处理方式", "让步接收", "处理数量", 1),
                Map.of("关联明细行号", 1, "处理方式", "报废", "处理数量", 1),
                Map.of("关联明细行号", 2, "处理方式", "退回", "处理数量", 9)));

        PxService.deriveInspectionDetail("INSPECTION", detail);

        assertEquals(1.0, first.get("让步接收数量"));
        assertEquals(1.0, first.get("报废数量"));
        assertEquals(2.0, first.get("判定退货数量"));
        assertEquals(0.0, second.get("让步接收数量"));
        assertEquals(0.0, second.get("报废数量"));
        assertEquals(9.0, second.get("判定退货数量"));
    }

    @Test
    void emptyDispositionTabClearsDerivedQuantities() {
        Map<String, Object> row = new HashMap<>(Map.of(
                "让步接收数量", 3, "报废数量", 2, "判定退货数量", 1));
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", new ArrayList<>(List.of(row)));
        detail.put("unqualified", new ArrayList<>());

        PxService.deriveInspectionDetail("INSPECTION", detail);

        assertEquals(0.0, row.get("让步接收数量"));
        assertEquals(0.0, row.get("报废数量"));
        assertEquals(0.0, row.get("判定退货数量"));
    }

    @Test
    void fullBatchReturnUsesInspectedQuantity() {
        Map<String, Object> row = new HashMap<>(Map.of(
                "检验数量", 10, "不合格数量", 2, "处置方式", "全批退回"));

        assertEquals(10.0, PxService.inspectionReturnQuantity(new HashMap<>(), row, 1), 0.0001);
    }

    @Test
    void writesAcceptanceReturnAndSyncsUnqualifiedReason() {
        Map<String, Object> item = new HashMap<>(Map.of(
                "合格数量", 7, "不合格数量", 3, "不合格原因", "尺寸不良"));
        Map<String, Object> treatment = new HashMap<>(Map.of(
                "关联明细行号", 1, "处理方式", "退回", "处理数量", 3));
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", new ArrayList<>(List.of(item)));
        detail.put("unqualified", List.of(treatment));

        PxService.deriveInspectionDetail("INSPECTION", detail);

        assertEquals(3.0, ((Number) item.get("验收退回数量")).doubleValue(), 0.0001);
        assertEquals(3.0, ((Number) item.get("判定退货数量")).doubleValue(), 0.0001);
        assertEquals("尺寸不良", treatment.get("不合格原因"), "不合格处理行应自动带下主行原因");
    }

    @Test
    void acceptanceReturnQuantityIsAuthoritativeZero() {
        Map<String, Object> row = new HashMap<>(Map.of(
                "验收退回数量", 0, "判定退货数量", 4, "不合格数量", 4));

        assertEquals(0.0, PxService.inspectionReturnQuantity(new HashMap<>(), row, 1), 0.0001);
    }

    @Test
    void computesConcessionTaxPriceFromDiscount() {
        Map<String, Object> item = new HashMap<>(Map.of(
                "合格数量", 8, "不合格数量", 2, "检验数量", 10,
                "单价", 10, "含税单价", 11.3, "处置方式", "让步接收", "折价率%", 10));
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", new ArrayList<>(List.of(item)));
        detail.put("unqualified", List.of(Map.of(
                "关联明细行号", 1, "处理方式", "让步接收", "处理数量", 2, "不合格原因", "尺寸不良")));

        PxService.deriveInspectionDetail("INSPECTION", detail);

        assertEquals(10.17, ((Number) item.get("让步接收含税单价")).doubleValue(), 0.0001);
    }

    @Test
    void derivesProcessInspectionReworkQuantity() {
        Map<String, Object> row = new HashMap<>(Map.of("合格数量", 8, "不合格数量", 2));
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", new ArrayList<>(List.of(row)));
        detail.put("unqualified", List.of(Map.of(
                "关联明细行号", 1, "处理方式", "返工", "处理数量", 2, "不合格原因", "尺寸不良")));

        PxService.deriveInspectionDetail("PROCESS_INSPECTION", detail);

        assertEquals(2.0, ((Number) row.get("返工数量")).doubleValue(), 0.0001);
        assertEquals(2.0, ((Number) row.get("检验返工数量")).doubleValue(), 0.0001);
    }

    @Test
    void leavesQuantitiesUntouchedWithoutDispositionTab() {
        Map<String, Object> row = new HashMap<>(Map.of(
                "让步接收数量", 3, "报废数量", 2, "判定退货数量", 1));
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", new ArrayList<>(List.of(row)));

        PxService.deriveInspectionDetail("INSPECTION", detail);

        assertEquals(3.0, ((Number) row.get("让步接收数量")).doubleValue());
        assertEquals(2.0, ((Number) row.get("报废数量")).doubleValue());
        assertEquals(1.0, ((Number) row.get("判定退货数量")).doubleValue());
    }
}
