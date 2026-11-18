package com.mes.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.mapper.FormApprovalMapper;
import com.mes.mapper.FormDataMapper;
import com.mes.mapper.PanelConfigMapper;
import com.mes.panel.PanelActionRegistry;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;

/**
 * 开票工作台数据：以「销货单」为行，带出发票号与状态，供选择销货单开票。
 */
@ExtendWith(MockitoExtension.class)
class PxServiceInvoiceWorkbenchTest {

    @Mock private PanelConfigMapper panelMapper;
    @Mock private FormDataMapper formMapper;
    @Mock private FormApprovalMapper approvalMapper;
    @Mock private ReportQueryService reportQueryService;
    @Mock private StockTxnService stockTxnService;

    private final ObjectMapper json = new ObjectMapper();
    private PxService service;

    @BeforeEach
    void setUp() {
        service = new PxService(panelMapper, formMapper, approvalMapper, reportQueryService,
                new PanelActionRegistry(List.of()), null, null, stockTxnService);
    }

    private FormData document(String panel, String no, String status,
                              Map<String, Object> head, Map<String, Object> detail) throws Exception {
        FormData row = new FormData();
        row.setId(1L);
        row.setPanelCode(panel);
        row.setFormNo(no);
        row.setStatus(status);
        row.setData(json.writeValueAsString(head));
        row.setDetailData(json.writeValueAsString(detail));
        row.setCreateTime(LocalDateTime.now());
        return row;
    }

    @Test
    void workbenchListsSaleInvoicesWithInvoiceState() throws Exception {
        FormData si1 = document("SALE_INV", "XS-1", "已审核",
                Map.of("单据编号", "XS-1", "单据日期", "2026-11-05", "客户", "华东铝业", "客户编码", "KH001"),
                Map.of("items", List.of(Map.of("数量", 10, "含税金额", 339))));
        FormData si2 = document("SALE_INV", "XS-2", "已审核",
                Map.of("单据编号", "XS-2", "单据日期", "2026-11-05", "客户", "中天精工"),
                Map.of("items", List.of(Map.of("数量", 5, "含税金额", 100))));
        FormData si3 = document("SALE_INV", "XS-3", "草稿",
                Map.of("单据编号", "XS-3", "客户", "华东铝业"),
                Map.of("items", List.of(Map.of("数量", 1, "含税金额", 10))));
        FormData inv = document("SALE_INVOICE", "FP-1", "已审核",
                Map.of("单据编号", "FP-1", "销货单号", "XS-1"), Map.of("items", List.of()));

        when(formMapper.selectList(any())).thenReturn(List.of(si1, si2, si3), List.of(inv));

        List<Map<String, Object>> rows = service.invoiceWorkbench();

        assertEquals(3, rows.size());
        Map<String, Object> r1 = rows.get(0);
        assertEquals("XS-1", r1.get("销货单号"));
        assertEquals("FP-1", r1.get("发票号"));
        assertEquals("已审核", r1.get("发票状态"));
        assertEquals(false, r1.get("可否开票"), "已开票的不再允许重复开票");
        assertEquals(10.0, ((Number) r1.get("数量合计")).doubleValue(), 0.001);
        assertEquals(339.0, ((Number) r1.get("价税合计")).doubleValue(), 0.001);

        Map<String, Object> r2 = rows.get(1);
        assertEquals("XS-2", r2.get("销货单号"));
        assertEquals("", r2.get("发票号"));
        assertEquals(true, r2.get("可否开票"), "已审核且未开票的可开票");

        Map<String, Object> r3 = rows.get(2);
        assertEquals(false, r3.get("可否开票"), "未审核的销货单不可开票");
        assertTrue(String.valueOf(r3.get("不可开票原因")).contains("未审核"));
    }
}
