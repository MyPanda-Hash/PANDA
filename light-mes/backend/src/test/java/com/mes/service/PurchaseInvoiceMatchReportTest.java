package com.mes.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.mapper.FormDataMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.math.BigDecimal;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;

/**
 * 采购发票勾稽表 PURCHASE_INVOICE_MATCH（2026-11-06）：
 * 按发票明细行输出开票数量、来源入库/退货数量、已开票/未开票数量与勾稽状态。
 * 覆盖无来源行、部分开票、红字负数、跨发票汇总与排序。
 */
@ExtendWith(MockitoExtension.class)
class PurchaseInvoiceMatchReportTest {

    @Mock private FormDataMapper formMapper;

    private final ObjectMapper json = new ObjectMapper();

    @Test
    void registersReportDefinition() {
        ReportPanelRegistry.Definition definition = ReportPanelRegistry.definition("PURCHASE_INVOICE_MATCH");

        assertNotNull(definition);
        assertEquals("采购发票勾稽表", definition.name());
        assertEquals(List.of("发票编号", "开票日期", "供应商", "发票号码", "业务类型", "来源单号", "来源行号",
                        "存货编码", "存货名称", "开票数量", "入库数量", "已开票数量", "未开票数量", "勾稽状态"),
                definition.columns());
        assertTrue(ReportPanelRegistry.isReport("PURCHASE_INVOICE_MATCH"));
    }

    @Test
    void reportsInvoiceLineWithoutSourceUsingHeaderStatus() {
        FormData invoice = document("PU_INVOICE", "PI-001", "已审核",
                fields("发票号码", "FP-0001", "供应商", "华东铝业", "开票日期", "2026-11-05",
                        "业务类型", "采购发票", "勾稽状态", "未勾稽"),
                Map.of("items", List.of(fields("存货编码", "CL001", "存货名称", "铝棒", "数量", 5))));

        List<Map<String, Object>> rows = report(invoice);

        assertEquals(1, rows.size());
        Map<String, Object> row = rows.get(0);
        assertEquals("PI-001", row.get("发票编号"));
        assertEquals("2026-11-05", row.get("开票日期"));
        assertEquals("华东铝业", row.get("供应商"));
        assertEquals("FP-0001", row.get("发票号码"));
        assertEquals("", row.get("来源单号"));
        assertEquals("", row.get("来源行号"));
        assertEquals("未勾稽", row.get("勾稽状态"));
        assertDecimal("5", row.get("开票数量"));
        assertDecimal("0", row.get("入库数量"));
        assertDecimal("0", row.get("已开票数量"));
        assertDecimal("0", row.get("未开票数量"));
    }

    @Test
    void computesRemainingQuantityForPartialInvoice() {
        FormData purchaseIn = document("PURCHASE_IN", "RK-001", "已审核",
                fields("单据编号", "RK-001", "供应商", "华东铝业"),
                Map.of("items", List.of(fields("存货编码", "CL001", "存货名称", "铝棒", "实收数量", 10, "数量", 10))));
        FormData invoice = document("PU_INVOICE", "PI-001", "已审核",
                fields("供应商", "华东铝业", "业务类型", "采购发票"),
                Map.of("items", List.of(fields(
                        "来源面板", "PURCHASE_IN", "来源单号", "RK-001", "来源行号", 1,
                        "存货编码", "CL001", "存货名称", "铝棒", "数量", 4))));

        List<Map<String, Object>> rows = report(purchaseIn, invoice);

        assertEquals(1, rows.size());
        Map<String, Object> row = rows.get(0);
        assertDecimal("4", row.get("开票数量"));
        assertDecimal("10", row.get("入库数量"));
        assertDecimal("4", row.get("已开票数量"));
        assertDecimal("6", row.get("未开票数量"));
        assertEquals("部分勾稽", row.get("勾稽状态"));
    }

    @Test
    void redInvoiceNegativeQuantityCountsOnReturnSource() {
        FormData purchaseReturn = document("PURCHASE_RETURN", "RT-001", "已审核",
                fields("单据编号", "RT-001", "供应商", "华东铝业"),
                Map.of("items", List.of(fields("存货编码", "CL001", "存货名称", "铝棒", "数量", 3))));
        FormData redInvoice = document("PU_INVOICE", "PI-002", "已审核",
                fields("供应商", "华东铝业", "业务类型", "采购发票(退货)"),
                Map.of("items", List.of(fields(
                        "来源面板", "PURCHASE_RETURN", "来源单号", "RT-001", "来源行号", 1,
                        "存货编码", "CL001", "存货名称", "铝棒", "数量", -3))));

        List<Map<String, Object>> rows = report(purchaseReturn, redInvoice);

        assertEquals(1, rows.size());
        Map<String, Object> row = rows.get(0);
        assertDecimal("-3", row.get("开票数量"));
        assertDecimal("3", row.get("入库数量"));
        assertDecimal("3", row.get("已开票数量"));
        assertDecimal("0", row.get("未开票数量"));
        assertEquals("已勾稽", row.get("勾稽状态"));
    }

    @Test
    void sumsInvoicedQuantityAcrossEffectiveInvoices() {
        FormData purchaseIn = document("PURCHASE_IN", "RK-001", "已审核",
                fields("单据编号", "RK-001"),
                Map.of("items", List.of(fields("存货编码", "CL001", "实收数量", 10))));
        FormData first = document("PU_INVOICE", "PI-001", "已审核", fields("业务类型", "采购发票"),
                Map.of("items", List.of(fields("来源面板", "PURCHASE_IN", "来源单号", "RK-001",
                        "来源行号", 1, "数量", 4))));
        FormData second = document("PU_INVOICE", "PI-002", "已审核", fields("业务类型", "采购发票"),
                Map.of("items", List.of(fields("来源面板", "PURCHASE_IN", "来源单号", "RK-001",
                        "来源行号", 1, "数量", 6))));

        List<Map<String, Object>> rows = report(purchaseIn, first, second);

        assertEquals(2, rows.size());
        for (Map<String, Object> row : rows) {
            assertDecimal("10", row.get("已开票数量"));
            assertDecimal("0", row.get("未开票数量"));
            assertEquals("已勾稽", row.get("勾稽状态"));
        }
    }

    @Test
    void ignoresDraftInvoicesWhenComputingInvoicedQuantity() {
        FormData purchaseIn = document("PURCHASE_IN", "RK-001", "已审核",
                fields("单据编号", "RK-001"),
                Map.of("items", List.of(fields("存货编码", "CL001", "实收数量", 10))));
        FormData draft = document("PU_INVOICE", "PI-000", "草稿", fields("业务类型", "采购发票"),
                Map.of("items", List.of(fields("来源面板", "PURCHASE_IN", "来源单号", "RK-001",
                        "来源行号", 1, "数量", 4))));
        FormData effective = document("PU_INVOICE", "PI-001", "已审核", fields("业务类型", "采购发票"),
                Map.of("items", List.of(fields("来源面板", "PURCHASE_IN", "来源单号", "RK-001",
                        "来源行号", 1, "数量", 4))));

        List<Map<String, Object>> rows = report(purchaseIn, draft, effective);

        assertEquals(1, rows.size());
        assertEquals("PI-001", rows.get(0).get("发票编号"));
        assertDecimal("4", rows.get(0).get("已开票数量"));
        assertDecimal("6", rows.get(0).get("未开票数量"));
    }

    @Test
    void fallsBackToDisplaySourceColumns() {
        FormData purchaseIn = document("PURCHASE_IN", "RK-001", "已审核",
                fields("单据编号", "RK-001"),
                Map.of("items", List.of(fields("存货编码", "CL001", "实收数量", 8))));
        FormData invoice = document("PU_INVOICE", "PI-001", "已审核", fields("业务类型", "采购发票"),
                Map.of("items", List.of(fields(
                        "源单类型", "PURCHASE_IN", "源单编号", "RK-001", "源单行号", 1, "数量", 2))));

        List<Map<String, Object>> rows = report(purchaseIn, invoice);

        assertEquals(1, rows.size());
        Map<String, Object> row = rows.get(0);
        assertEquals("PURCHASE_IN", row.get("来源面板"));
        assertEquals("RK-001", row.get("来源单号"));
        assertEquals(1, row.get("来源行号"));
        assertDecimal("8", row.get("入库数量"));
        assertDecimal("2", row.get("已开票数量"));
        assertDecimal("6", row.get("未开票数量"));
    }

    @Test
    void keepsHeaderStatusWhenPresent() {
        FormData purchaseIn = document("PURCHASE_IN", "RK-001", "已审核",
                fields("单据编号", "RK-001"),
                Map.of("items", List.of(fields("存货编码", "CL001", "实收数量", 10))));
        FormData invoice = document("PU_INVOICE", "PI-001", "已审核",
                fields("业务类型", "采购发票", "勾稽状态", "部分勾稽"),
                Map.of("items", List.of(fields("来源面板", "PURCHASE_IN", "来源单号", "RK-001",
                        "来源行号", 1, "数量", 4))));

        List<Map<String, Object>> rows = report(purchaseIn, invoice);

        assertEquals("部分勾稽", rows.get(0).get("勾稽状态"));
        assertDecimal("6", rows.get(0).get("未开票数量"));
    }

    @Test
    void sortsByInvoiceNoThenSourceLine() {
        FormData purchaseIn = document("PURCHASE_IN", "RK-001", "已审核",
                fields("单据编号", "RK-001"),
                Map.of("items", List.of(
                        fields("存货编码", "CL001", "实收数量", 10),
                        fields("存货编码", "CL002", "实收数量", 10))));
        FormData later = document("PU_INVOICE", "PI-002", "已审核", fields("业务类型", "采购发票"),
                Map.of("items", List.of(fields("来源面板", "PURCHASE_IN", "来源单号", "RK-001",
                        "来源行号", 2, "数量", 1))));
        FormData first = document("PU_INVOICE", "PI-001", "已审核", fields("业务类型", "采购发票"),
                Map.of("items", List.of(
                        fields("来源面板", "PURCHASE_IN", "来源单号", "RK-001", "来源行号", 2, "数量", 1),
                        fields("来源面板", "PURCHASE_IN", "来源单号", "RK-001", "来源行号", 1, "数量", 1))));

        List<Map<String, Object>> rows = report(purchaseIn, later, first);

        assertEquals(3, rows.size());
        assertEquals("PI-001", rows.get(0).get("发票编号"));
        assertEquals(1, rows.get(0).get("来源行号"));
        assertEquals("PI-001", rows.get(1).get("发票编号"));
        assertEquals(2, rows.get(1).get("来源行号"));
        assertEquals("PI-002", rows.get(2).get("发票编号"));
    }

    // ---------- helpers ----------

    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> report(FormData... documents) {
        when(formMapper.selectList(any())).thenReturn(List.of(documents));
        Map<String, Object> result = new ReportQueryService(formMapper, json)
                .query("PURCHASE_INVOICE_MATCH", null, Map.of(), 1, 100);
        return (List<Map<String, Object>>) result.get("list");
    }

    private FormData document(String panel, String no, String status,
                              Map<String, Object> head, Map<String, Object> detail) {
        FormData entity = new FormData();
        entity.setPanelCode(panel);
        entity.setFormNo(no);
        entity.setStatus(status);
        try {
            entity.setData(json.writeValueAsString(head));
            entity.setDetailData(json.writeValueAsString(detail));
        } catch (Exception e) {
            throw new IllegalStateException("测试单据序列化失败", e);
        }
        return entity;
    }

    private static Map<String, Object> fields(Object... values) {
        Map<String, Object> out = new LinkedHashMap<>();
        for (int i = 0; i + 1 < values.length; i += 2) out.put(String.valueOf(values[i]), values[i + 1]);
        return out;
    }

    private static void assertDecimal(String expected, Object actual) {
        assertTrue(actual instanceof BigDecimal, "应为数值：" + actual);
        assertEquals(0, new BigDecimal(expected).compareTo((BigDecimal) actual),
                "期望 " + expected + "，实际 " + actual);
    }
}
