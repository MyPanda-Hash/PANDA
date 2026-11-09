package com.mes.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.entity.PanelConfig;
import com.mes.mapper.FormApprovalMapper;
import com.mes.mapper.FormDataMapper;
import com.mes.mapper.PanelConfigMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 采购发票来源改造（2026-11-04）：进货单废弃后，正票由采购入库单生成、红字发票由采购退货单生成。
 */
@ExtendWith(MockitoExtension.class)
class PxServicePurchaseInvoiceTest {

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
    void generatesInvoiceFromPurchaseIn() throws Exception {
        FormData purchaseIn = document("PURCHASE_IN", "RK-001", "已审核",
                Map.of("单据编号", "RK-001", "供应商", "华东铝业", "供应商编码", "KH001",
                        "采购订单号", "PO-001"),
                Map.of("items", List.of(Map.of(
                        "存货编码", "CL001", "存货名称", "铝棒", "规格型号", "80mm",
                        "计量单位", "件", "实收数量", 10, "数量", 10,
                        "单价", 10, "税率%", 13, "含税金额", 113))));
        when(formMapper.selectOne(any())).thenReturn(purchaseIn);
        when(formMapper.selectList(any())).thenReturn(List.of());
        when(formMapper.selectCount(any())).thenReturn(0L);
        when(panelMapper.selectOne(any())).thenReturn(invoiceConfig());

        Map<String, Object> result = service.callButton("PURCHASE_IN", "生成采购发票（普通采购）",
                Map.of("编号", "RK-001"), Map.of());

        assertEquals("PU_INVOICE", result.get("gotoPanel"));
        ArgumentCaptor<FormData> inserted = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).insert(inserted.capture());
        FormData target = inserted.getValue();
        assertEquals("PU_INVOICE", target.getPanelCode());
        assertEquals("草稿", target.getStatus());

        Map<?, ?> head = json.readValue(target.getData(), Map.class);
        assertEquals("采购发票", head.get("业务类型"));
        assertEquals("RK-001", head.get("入库单号"));
        assertEquals("PO-001", head.get("采购订单号"));
        assertEquals("华东铝业", head.get("供应商"));
        assertEquals(113.0, ((Number) head.get("价税合计")).doubleValue());

        Map<?, ?> detail = json.readValue(target.getDetailData(), Map.class);
        Map<?, ?> row = (Map<?, ?>) ((List<?>) detail.get("items")).get(0);
        assertEquals("CL001", row.get("存货编码"));
        assertEquals(10.0, ((Number) row.get("数量")).doubleValue());
        assertEquals(13.0, ((Number) row.get("税额")).doubleValue(), 0.0001);
        assertEquals(113.0, ((Number) row.get("价税合计")).doubleValue());
        assertEquals("PURCHASE_IN", row.get("来源面板"));
        assertEquals("RK-001", row.get("来源单号"));
        assertEquals(1, row.get("来源行号"));
    }

    @Test
    void generatesNegativeRedInvoiceLinkedToBlueInvoice() throws Exception {
        FormData purchaseReturn = document("PURCHASE_RETURN", "CT-001", "已审核",
                Map.of("单据编号", "CT-001", "供应商", "华东铝业", "供应商编码", "KH001",
                        "入库单号", "RK-001", "采购订单号", "PO-001"),
                Map.of("items", List.of(Map.of(
                        "存货编码", "CL001", "存货名称", "铝棒", "计量单位", "件",
                        "数量", 2, "实收数量", 2, "单价", 10, "税率%", 13, "含税金额", 22.6))));
        FormData blue = document("PU_INVOICE", "PI-001", "已审核",
                Map.of("单据编号", "PI-001", "业务类型", "采购发票", "入库单号", "RK-001",
                        "开票类型", "增值税专用发票"),
                Map.of("items", List.of(Map.of("存货编码", "CL001", "数量", 2, "价税合计", 22.6))));
        when(formMapper.selectOne(any())).thenReturn(purchaseReturn);
        when(formMapper.selectList(any())).thenReturn(List.of(blue));
        when(formMapper.selectCount(any())).thenReturn(0L);
        when(panelMapper.selectOne(any())).thenReturn(invoiceConfig());

        service.callButton("PURCHASE_RETURN", "生成采购发票（采购退货）",
                Map.of("编号", "CT-001"), Map.of());

        ArgumentCaptor<FormData> inserted = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).insert(inserted.capture());
        Map<?, ?> head = json.readValue(inserted.getValue().getData(), Map.class);
        assertEquals("采购发票(退货)", head.get("业务类型"));
        assertEquals("RK-001", head.get("入库单号"));
        assertEquals("PI-001", head.get("原发票号"));
        assertEquals("增值税专用发票", head.get("开票类型"));
        assertEquals(-22.6, ((Number) head.get("价税合计")).doubleValue(), 0.0001);
        Map<?, ?> detail = json.readValue(inserted.getValue().getDetailData(), Map.class);
        Map<?, ?> row = (Map<?, ?>) ((List<?>) detail.get("items")).get(0);
        // 红字口径：数量、税额、价税合计为负，单价为正
        assertEquals(-2.0, ((Number) row.get("数量")).doubleValue());
        assertEquals(10.0, ((Number) row.get("无税单价")).doubleValue());
        assertEquals(-2.6, ((Number) row.get("税额")).doubleValue(), 0.0001);
        assertEquals(-22.6, ((Number) row.get("价税合计")).doubleValue(), 0.0001);
        assertEquals("PURCHASE_RETURN", row.get("来源面板"));
    }

    @Test
    void rejectsRedInvoiceWithoutBlueInvoice() throws Exception {
        FormData purchaseReturn = document("PURCHASE_RETURN", "CT-002", "已审核",
                Map.of("单据编号", "CT-002", "供应商", "华东铝业", "入库单号", "RK-002"),
                Map.of("items", List.of(Map.of(
                        "存货编码", "CL001", "存货名称", "铝棒", "数量", 1, "实收数量", 1,
                        "单价", 10, "税率%", 13, "含税金额", 11.3))));
        when(formMapper.selectOne(any())).thenReturn(purchaseReturn);
        when(formMapper.selectList(any())).thenReturn(List.of());

        IllegalStateException error = assertThrows(IllegalStateException.class,
                () -> service.callButton("PURCHASE_RETURN", "生成采购发票（采购退货）",
                        Map.of("编号", "CT-002"), Map.of()));

        assertTrue(error.getMessage().contains("尚未开具已审核蓝字采购发票"));
    }

    @Test
    void rejectsRedInvoiceExceedingBlueQuantity() throws Exception {
        FormData purchaseReturn = document("PURCHASE_RETURN", "CT-003", "已审核",
                Map.of("单据编号", "CT-003", "供应商", "华东铝业", "入库单号", "RK-003"),
                Map.of("items", List.of(Map.of(
                        "存货编码", "CL001", "存货名称", "铝棒", "数量", 3, "实收数量", 3,
                        "单价", 10, "税率%", 13, "含税金额", 33.9))));
        FormData blue = document("PU_INVOICE", "PI-003", "已审核",
                Map.of("单据编号", "PI-003", "业务类型", "采购发票", "入库单号", "RK-003"),
                Map.of("items", List.of(Map.of("存货编码", "CL001", "数量", 1, "价税合计", 11.3))));
        when(formMapper.selectOne(any())).thenReturn(purchaseReturn);
        when(formMapper.selectList(any())).thenReturn(List.of(blue));

        IllegalStateException error = assertThrows(IllegalStateException.class,
                () -> service.callButton("PURCHASE_RETURN", "生成采购发票（采购退货）",
                        Map.of("编号", "CT-003"), Map.of()));

        assertTrue(error.getMessage().contains("超过原蓝字发票可冲数量"));
    }

    @Test
    void rejectsDraftSourceInvoice() throws Exception {
        FormData draft = document("PURCHASE_IN", "RK-002", "草稿",
                Map.of("单据编号", "RK-002", "供应商", "华东铝业"),
                Map.of("items", List.of(Map.of(
                        "存货编码", "CL001", "存货名称", "铝棒", "实收数量", 1, "单价", 10))));
        when(formMapper.selectOne(any())).thenReturn(draft);

        IllegalStateException error = assertThrows(IllegalStateException.class,
                () -> service.callButton("PURCHASE_IN", "生成采购发票（普通采购）",
                        Map.of("编号", "RK-002"), Map.of()));

        assertTrue(error.getMessage().contains("仅已审核单据可生成采购发票"));
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

    private PanelConfig invoiceConfig() throws Exception {
        PanelConfig config = new PanelConfig();
        config.setPanelCode("PU_INVOICE");
        config.setPanelName("采购发票");
        config.setCategory("单据");
        config.setConfig(json.writeValueAsString(Map.of(
                "metadata", Map.of(
                        "panelCode", "PU_INVOICE", "panelName", "采购发票", "panelCategory", "单据",
                        "autoCodeField", "单据编号",
                        "panelState", Map.of("dataName", "单据状态", "defaultOptions", List.of("草稿", "已审核")),
                        "buttonGroups", List.of(), "panelButtons", List.of(),
                        "panelPageDto", Map.of(
                                "tablePages", List.of(Map.of(
                                        "tableName", "采购发票列表",
                                        "queryFields", List.of(),
                                        "gridTabs", List.of(Map.of("label", "明细", "columns", List.of())),
                                        "formPages", List.of())),
                                "formPages", List.of())),
                "dataSchema", Map.of("type", "object", "fields", List.of(
                        Map.of("dataName", "单据日期", "dataType", "日期"),
                        Map.of("dataName", "单据编号", "dataType", "文本", "autoCode", true),
                        Map.of("dataName", "业务类型", "dataType", "下拉框", "defaultValue", "采购发票"),
                        Map.of("dataName", "供应商", "dataType", "文本"),
                        Map.of("dataName", "开票类型", "dataType", "文本"),
                        Map.of("dataName", "入库单号", "dataType", "文本"),
                        Map.of("dataName", "采购订单号", "dataType", "文本"),
                        Map.of("dataName", "价税合计", "dataType", "小数"))),
                "detail", Map.of("tabs", List.of(Map.of(
                        "key", "items", "label", "明细",
                        "fields", List.of(
                                Map.of("dataName", "存货编码", "dataType", "文本"),
                                Map.of("dataName", "存货名称", "dataType", "文本"),
                                Map.of("dataName", "计量单位", "dataType", "文本"),
                                Map.of("dataName", "数量", "dataType", "小数"),
                                Map.of("dataName", "无税单价", "dataType", "小数"),
                                Map.of("dataName", "税率%", "dataType", "小数"),
                                Map.of("dataName", "税额", "dataType", "小数"),
                                Map.of("dataName", "价税合计", "dataType", "小数"))))))));
        return config;
    }
}
