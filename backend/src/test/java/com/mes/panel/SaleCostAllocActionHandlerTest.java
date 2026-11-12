package com.mes.panel;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.mapper.FormDataMapper;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 销售费用分摊「执行分摊」：把分摊金额按发票号回写发票，形成「费用 → 分摊 → 发票毛利」闭环。
 *
 * <p>这道动作补的是链条最后一棒：分摊单审批后如果不回写发票，费用摊了等于没摊。
 */
class SaleCostAllocActionHandlerTest {

    private final FormDataMapper formMapper = mock(FormDataMapper.class);
    private final ObjectMapper json = new ObjectMapper();
    private final SaleCostAllocActionHandler handler = new SaleCostAllocActionHandler(formMapper, json);

    @Test
    void onlyHandlesExecuteAction() {
        assertTrue(handler.supports("SALE_COST_ALLOC", "执行分摊"));
        assertTrue(!handler.supports("SALE_COST_ALLOC", "保存"));
        assertTrue(!handler.supports("EXPENSE", "执行分摊"));
    }

    @Test
    void rejectsUnapprovedAllocation() {
        when(formMapper.selectOne(any())).thenReturn(doc("SALE_COST_ALLOC", "FT-001", "草稿",
                Map.of(), List.of(row("FP-001", 100))));
        IllegalStateException error = assertThrows(IllegalStateException.class,
                () -> handler.handle(ctx("FT-001")));
        assertTrue(error.getMessage().contains("审核"));
        verify(formMapper, never()).updateById(any(FormData.class));
    }

    @Test
    void rejectsSecondExecution() {
        when(formMapper.selectOne(any())).thenReturn(doc("SALE_COST_ALLOC", "FT-001", "已审核",
                Map.of("分摊状态", "已分摊"), List.of(row("FP-001", 100))));
        IllegalStateException error = assertThrows(IllegalStateException.class,
                () -> handler.handle(ctx("FT-001")));
        assertTrue(error.getMessage().contains("重复执行"));
    }

    @Test
    void writesAllocationBackToInvoiceAndMarksExecuted() throws Exception {
        FormData alloc = doc("SALE_COST_ALLOC", "FT-001", "已审核",
                Map.of("分摊合计", 300), List.of(row("FP-001", 200), row("FP-001", 50), row("FP-002", 50)));
        FormData invoice = doc("SALE_INVOICE", "FP-001", "已审核", Map.of("分摊费用", 20), List.of());
        // 调用顺序固定：先查分摊单，再按发票号逐张查发票（LinkedHashMap 保持明细顺序）
        FormData invoice2 = doc("SALE_INVOICE", "FP-002", "已审核", Map.of(), List.of());
        when(formMapper.selectOne(any())).thenReturn(alloc, invoice, invoice2);

        Map<String, Object> out = handler.handle(ctx("FT-001"));

        assertEquals("已分摊", out.get("分摊状态"));
        assertEquals(2, out.get("分摊发票数"), "同一张票两行要合并成一条");
        Map<String, Object> allocHead = json.readValue(alloc.getData(), Map.class);
        assertEquals("已分摊", allocHead.get("分摊状态"));
        Map<String, Object> invHead = json.readValue(invoice.getData(), Map.class);
        assertEquals(270.0, invHead.get("分摊费用"), "20 + 200 + 50 = 270，按累计写回");
        assertEquals("FT-001", invHead.get("分摊单号"));
        Map<String, Object> inv2Head = json.readValue(invoice2.getData(), Map.class);
        assertEquals(50.0, inv2Head.get("分摊费用"));
    }

    @Test
    void requiresInvoiceNumberOnRows() {
        when(formMapper.selectOne(any())).thenReturn(doc("SALE_COST_ALLOC", "FT-001", "已审核",
                Map.of(), List.of(Map.of("分摊对象", "客户", "分摊金额", 100))));
        IllegalStateException error = assertThrows(IllegalStateException.class,
                () -> handler.handle(ctx("FT-001")));
        assertTrue(error.getMessage().contains("销售发票号"));
    }

    @Test
    void missingInvoiceFailsLoudly() {
        when(formMapper.selectOne(any())).thenReturn(
                doc("SALE_COST_ALLOC", "FT-001", "已审核", Map.of(), List.of(row("FP-404", 100))),
                null);
        IllegalStateException error = assertThrows(IllegalStateException.class,
                () -> handler.handle(ctx("FT-001")));
        assertTrue(error.getMessage().contains("销售发票不存在"));
    }

    // ---------- helpers ----------

    private static PanelActionContext ctx(String no) {
        return new PanelActionContext("SALE_COST_ALLOC", "执行分摊",
                Map.of("编号", no), Map.of(), "admin");
    }

    private static Map<String, Object> row(String invoiceNo, double amount) {
        return Map.of("分摊对象", "销售发票", "销售发票号", invoiceNo, "分摊金额", amount);
    }

    private FormData doc(String panel, String no, String status,
                         Map<String, Object> head, List<Map<String, Object>> items) {
        FormData fd = new FormData();
        fd.setPanelCode(panel);
        fd.setFormNo(no);
        fd.setStatus(status);
        try {
            fd.setData(json.writeValueAsString(head));
            fd.setDetailData(json.writeValueAsString(Map.of("items", items)));
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
        return fd;
    }
}
