package com.mes.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.mapper.FormApprovalMapper;
import com.mes.mapper.FormDataMapper;
import com.mes.mapper.PanelConfigMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;

/**
 * 采购发票价格校验：来源订单/入库/退货的含税金额、未税金额、税额必须与发票行一致。
 */
@ExtendWith(MockitoExtension.class)
class PxServiceInvoicePriceTest {

    @Mock private PanelConfigMapper panelMapper;
    @Mock private FormDataMapper formMapper;
    @Mock private FormApprovalMapper approvalMapper;
    @Mock private ReportQueryService reportQueryService;

    private final ObjectMapper json = new ObjectMapper();
    private PxService service;

    @BeforeEach
    void setUp() {
        service = new PxService(panelMapper, formMapper, approvalMapper, reportQueryService);
    }

    @Test
    void rejectsInvoiceAmountDifferentFromPurchaseOrder() throws Exception {
        FormData invoice = document("PU_INVOICE", "PI-001", "草稿",
                Map.of("单据编号", "PI-001", "供应商", "华东铝业", "单据日期", "2026-11-12"),
                Map.of("items", List.of(Map.of(
                        "来源面板", "PU_ORDER", "来源单号", "PO-001", "来源行号", 1,
                        "存货编码", "CP001", "存货名称", "铝棒", "数量", 10,
                        "税率%", 13, "无税单价", 10, "税额", 23.01, "价税合计", 200))));
        FormData order = document("PU_ORDER", "PO-001", "已审核",
                Map.of("单据编号", "PO-001", "供应商", "华东铝业"),
                Map.of("items", List.of(Map.of(
                        "物料编码", "CP001", "物料名称", "铝棒", "数量", 10,
                        "单价", 10, "税率%", 13, "金额", 100, "含税金额", 113))));
        when(formMapper.selectOne(any())).thenReturn(invoice, order, order);
        when(formMapper.selectList(any())).thenReturn(List.of());

        IllegalStateException error = assertThrows(IllegalStateException.class,
                () -> service.callButton("PU_INVOICE", "提交审批", Map.of("编号", "PI-001"), Map.of()));
        assertTrue(error.getMessage().contains("金额与来源不一致"), error.getMessage());
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
}
