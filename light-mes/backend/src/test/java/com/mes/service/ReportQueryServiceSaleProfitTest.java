package com.mes.service;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.mapper.FormDataMapper;

/**
 * 销售毛利表：一张发票一行，毛利 = 价税合计 − 分摊费用 − 成本金额。
 *
 * <p>成本口径两级：优先「出库成本」（发票 → 销货单 → 销售出库单行的成本金额），
 * 取不到退回「参考成本」（数量 × 存货档案参考成本），并在「成本口径」列标明用的是哪个，
 * 避免 0 成本被当成真毛利。
 */
@ExtendWith(MockitoExtension.class)
class ReportQueryServiceSaleProfitTest {

    @Mock private FormDataMapper formMapper;

    private final ObjectMapper json = new ObjectMapper();

    private FormData doc(String panel, String no, Map<String, Object> head, Map<String, Object> detail)
            throws Exception {
        FormData d = new FormData();
        d.setPanelCode(panel);
        d.setFormNo(no);
        d.setStatus("已审核");
        d.setData(json.writeValueAsString(head));
        d.setDetailData(json.writeValueAsString(detail));
        d.setCreateTime(LocalDateTime.now());
        return d;
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> row(String no) {
        Map<String, Object> result = new ReportQueryService(formMapper, json)
                .query("SALE_PROFIT", "", Map.of(), 1, 50);
        List<Map<String, Object>> rows = (List<Map<String, Object>>) result.get("list");
        return rows.stream().filter(r -> no.equals(r.get("单据编号"))).findFirst().orElseThrow();
    }

    private static BigDecimal dec(Object value) {
        return new BigDecimal(String.valueOf(value));
    }

    @Test
    void profitUsesOutboundCostAndDeductsAllocation() throws Exception {
        FormData invoice = doc("SALE_INVOICE", "FP-1", Map.of(
                "单据编号", "FP-1", "单据日期", "2026-11-12", "客户", "西部材料",
                "销货单号", "XS-1", "价税合计", 113, "分摊费用", 13, "分摊单号", "FT-1"),
                Map.of("items", List.of(Map.of("存货名称", "轴套 C", "数量", 10))));
        FormData saleInv = doc("SALE_INV", "XS-1", Map.of("单据编号", "XS-1", "来源单号", "CK-1"),
                Map.of("items", List.of()));
        FormData saleOut = doc("SALE_OUT", "CK-1", Map.of("单据编号", "CK-1"),
                Map.of("items", List.of(Map.of("存货名称", "轴套 C", "数量", 10, "成本金额", 60))));
        FormData inv = doc("INV", "INV-1", Map.of(),
                Map.of("items", List.of(Map.of("存货名称", "轴套 C", "参考成本", 7))));

        // docs() 的调用顺序：销货单 → 出库单 → 存货 → 发票
        when(formMapper.selectList(any())).thenReturn(List.of(saleInv), List.of(saleOut), List.of(inv), List.of(invoice));

        Map<String, Object> row = row("FP-1");
        assertEquals(0, new BigDecimal("113").compareTo(dec(row.get("价税合计"))));
        assertEquals(0, new BigDecimal("13").compareTo(dec(row.get("分摊费用"))));
        assertEquals(0, new BigDecimal("60").compareTo(dec(row.get("成本金额"))), "成本取出库单的成本金额");
        assertEquals(0, new BigDecimal("40").compareTo(dec(row.get("毛利"))), "113 − 13 − 60 = 40");
        assertEquals("出库成本", row.get("成本口径"));
        assertEquals(0, new BigDecimal("35.40").compareTo(dec(row.get("毛利率%"))), "40 / 113 = 35.40%");
    }

    @Test
    void resolvesOutboundCostBySalesOrderWhenSaleInvComesFromFinishIn() throws Exception {
        // 真实数据形态：销货单的来源是「产成品入库单」，不是出库单，成本要按销售订单号找同订单的出库单
        FormData invoice = doc("SALE_INVOICE", "FP-3", Map.of(
                "单据编号", "FP-3", "单据日期", "2026-11-12", "客户", "西部材料",
                "销货单号", "XS-3", "价税合计", 100, "分摊费用", 0),
                Map.of("items", List.of(Map.of("存货名称", "轴套 C", "数量", 10))));
        FormData saleInv = doc("SALE_INV", "XS-3", Map.of(
                "单据编号", "XS-3", "来源单号", "FI-9", "销售订单号", "SO-9"),
                Map.of("items", List.of()));
        FormData saleOut = doc("SALE_OUT", "CK-9", Map.of(
                "单据编号", "CK-9", "来源单号", "SO-9", "销售订单号", "SO-9"),
                Map.of("items", List.of(Map.of("存货名称", "轴套 C", "数量", 10, "成本金额", 55))));

        when(formMapper.selectList(any())).thenReturn(List.of(saleInv), List.of(saleOut), List.of(), List.of(invoice));

        Map<String, Object> row = row("FP-3");
        assertEquals(0, new BigDecimal("55").compareTo(dec(row.get("成本金额"))), "按销售订单号找到出库成本");
        assertEquals("出库成本(按订单)", row.get("成本口径"));
        assertEquals(0, new BigDecimal("45").compareTo(dec(row.get("毛利"))), "100 − 0 − 55 = 45");
    }

    @Test
    void fallsBackToReferenceCostWhenOutboundMissing() throws Exception {
        FormData invoice = doc("SALE_INVOICE", "FP-2", Map.of(
                "单据编号", "FP-2", "单据日期", "2026-11-12", "客户", "华东铝业",
                "销货单号", "XS-404", "价税合计", 50, "分摊费用", 0),
                Map.of("items", List.of(Map.of("存货名称", "轴套 C", "数量", 2))));
        FormData inv = doc("INV", "INV-1", Map.of(),
                Map.of("items", List.of(Map.of("存货名称", "轴套 C", "参考成本", 5))));

        when(formMapper.selectList(any())).thenReturn(List.of(), List.of(), List.of(inv), List.of(invoice));

        Map<String, Object> row = row("FP-2");
        assertEquals(0, new BigDecimal("10").compareTo(dec(row.get("成本金额"))), "2 × 5 = 10");
        assertEquals("参考成本", row.get("成本口径"));
        assertEquals(0, new BigDecimal("40").compareTo(dec(row.get("毛利"))), "50 − 0 − 10 = 40");
    }
}
