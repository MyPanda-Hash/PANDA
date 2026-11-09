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
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 采购发票「表头 + 行级来源」（2026-11-06）：
 * 行级选单只返回未开票来源行；保存时表头金额/税额汇总、供应商一致性、
 * 勾稽状态派生与红字行负数校验。
 */
@ExtendWith(MockitoExtension.class)
class PxServiceInvoiceSourceLinesTest {

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

    // ---------- invoiceSourceLines ----------

    @Test
    void listsRemainingPurchaseInLinesAfterPartialInvoice() throws Exception {
        FormData purchaseIn = document("PURCHASE_IN", "RK-001", "已审核",
                Map.of("单据编号", "RK-001", "供应商", "华东铝业", "供应商编码", "KH001",
                        "采购订单号", "PO-001"),
                Map.of("items", List.of(
                        fields("存货编码", "CL001", "存货名称", "铝棒", "规格型号", "80mm",
                                "计量单位", "件", "实收数量", 10, "数量", 10,
                                "单价", 10, "税率%", 13, "含税单价", 11.3,
                                "金额", 100, "税额", 13, "含税金额", 113),
                        fields("存货编码", "CL002", "存货名称", "铝板", "规格型号", "6061",
                                "计量单位", "kg", "实收数量", 5, "数量", 5,
                                "单价", 20, "税率%", 13, "含税单价", 22.6,
                                "金额", 100, "税额", 13, "含税金额", 113))));
        FormData invoice = document("PU_INVOICE", "PI-001", "已审核",
                Map.of("业务类型", "采购发票", "供应商", "华东铝业"),
                Map.of("items", List.of(fields(
                        "来源面板", "PURCHASE_IN", "来源单号", "RK-001", "来源行号", 1, "数量", 4))));
        when(formMapper.selectList(any())).thenReturn(List.of(purchaseIn, invoice));

        Map<String, Object> result = service.invoiceSourceLines("purchase", null, null);

        List<Map<String, Object>> list = listOf(result);
        assertEquals(2, list.size());
        Map<String, Object> first = list.get(0);
        assertEquals("华东铝业", first.get("供应商"));
        assertEquals("KH001", first.get("供应商编码"));
        assertEquals("PURCHASE_IN", first.get("来源面板"));
        assertEquals("RK-001", first.get("来源单号"));
        assertEquals(1, first.get("来源行号"));
        assertEquals(10.0, number(first.get("入库数量")), 0.0001);
        assertEquals(4.0, number(first.get("已开票数量")), 0.0001);
        assertEquals(6.0, number(first.get("未开票数量")), 0.0001);
        assertEquals(113.0, number(first.get("含税金额")), 0.0001);
        assertEquals("PO-001", first.get("采购订单号"));
        // 第二行未开票量保持整行可开
        assertEquals(5.0, number(list.get(1).get("未开票数量")), 0.0001);
        assertEquals(0.0, number(list.get(1).get("已开票数量")), 0.0001);
    }

    @Test
    void filtersSourceLinesBySupplierAndKeyword() throws Exception {
        FormData purchaseIn = document("PURCHASE_IN", "RK-001", "已审核",
                Map.of("单据编号", "RK-001", "供应商", "华东铝业", "供应商编码", "KH001"),
                Map.of("items", List.of(
                        fields("存货编码", "CL001", "存货名称", "铝棒", "实收数量", 10, "单价", 10),
                        fields("存货编码", "CL002", "存货名称", "铝板", "实收数量", 5, "单价", 20))));
        when(formMapper.selectList(any())).thenReturn(List.of(purchaseIn));

        assertEquals(2, listOf(service.invoiceSourceLines("purchase", "华东", null)).size());
        assertEquals(0, listOf(service.invoiceSourceLines("purchase", "中天", null)).size());
        List<Map<String, Object>> filtered = listOf(service.invoiceSourceLines("purchase", null, "铝板"));
        assertEquals(1, filtered.size());
        assertEquals(2, filtered.get(0).get("来源行号"));
    }

    @Test
    void listsReturnLinesWithOriginalPurchaseInNo() throws Exception {
        FormData purchaseReturn = document("PURCHASE_RETURN", "CT-001", "已审核",
                Map.of("供应商", "华东铝业", "入库单号", "RK-009", "退货原因", "库存退料"),
                Map.of("items", List.of(fields(
                        "存货编码", "CL001", "存货名称", "铝棒", "计量单位", "件",
                        "数量", 3, "实收数量", 3, "单价", 10, "税率%", 13,
                        "含税单价", 11.3, "金额", 30, "税额", 3.9, "含税金额", 33.9))));
        FormData red = document("PU_INVOICE", "PI-002", "已审核",
                Map.of("业务类型", "采购发票(退货)", "供应商", "华东铝业"),
                Map.of("items", List.of(fields(
                        "来源面板", "PURCHASE_RETURN", "来源单号", "CT-001", "来源行号", 1, "数量", -1))));
        when(formMapper.selectList(any())).thenReturn(List.of(purchaseReturn, red));

        List<Map<String, Object>> list = listOf(service.invoiceSourceLines("return", null, null));

        assertEquals(1, list.size());
        Map<String, Object> row = list.get(0);
        assertEquals("PURCHASE_RETURN", row.get("来源面板"));
        assertEquals(3.0, number(row.get("入库数量")), 0.0001);
        assertEquals(1.0, number(row.get("已开票数量")), 0.0001);
        assertEquals(2.0, number(row.get("未开票数量")), 0.0001);
        assertEquals("RK-009", row.get("入库单号"));
    }

    @Test
    void rejectsUnknownSourceLineMode() {
        IllegalArgumentException error = assertThrows(IllegalArgumentException.class,
                () -> service.invoiceSourceLines("unknown", null, null));

        assertTrue(error.getMessage().contains("不支持的发票选单模式"));
    }

    // ---------- 发票保存 ----------

    @Test
    void saveAggregatesHeaderAndReconcilesFromSourceLines() throws Exception {
        stubInvoiceSave(purchaseInWithTwoLines());
        Map<String, Object> form = invoiceForm("采购发票", "华东铝业", List.of(
                invoiceRow("PURCHASE_IN", "RK-001", 1, 10, 10, 13, 13, 113),
                invoiceRow("PURCHASE_IN", "RK-001", 2, 5, 10, 13, 6.5, 56.5)));

        service.callButton("PU_INVOICE", "新增流程", form, Map.of());

        ArgumentCaptor<FormData> inserted = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).insert(inserted.capture());
        Map<?, ?> head = json.readValue(inserted.getValue().getData(), Map.class);
        assertEquals(150.0, number(head.get("不含税金额")), 0.0001);
        assertEquals(19.5, number(head.get("税额")), 0.0001);
        assertEquals(169.5, number(head.get("价税合计")), 0.0001);
        assertEquals("部分勾稽", head.get("勾稽状态"));

        List<?> rows = (List<?>) ((Map<?, ?>) json.readValue(
                inserted.getValue().getDetailData(), Map.class)).get("items");
        Map<?, ?> first = (Map<?, ?>) rows.get(0);
        assertEquals(10.0, number(first.get("入库数量")), 0.0001);
        assertEquals(10.0, number(first.get("已开票数量")), 0.0001);
        assertEquals(0.0, number(first.get("未开票数量")), 0.0001);
        // 展示列与校验键互兜底：来源面板/来源单号 回填为源单类型/源单编号
        assertEquals("PURCHASE_IN", first.get("源单类型"));
        assertEquals("RK-001", first.get("源单编号"));
        Map<?, ?> second = (Map<?, ?>) rows.get(1);
        assertEquals(5.0, number(second.get("已开票数量")), 0.0001);
        assertEquals(5.0, number(second.get("未开票数量")), 0.0001);
    }

    @Test
    void marksInvoiceFullyReconciledWhenAllSourceLinesInvoiced() throws Exception {
        stubInvoiceSave(purchaseInWithTwoLines());
        Map<String, Object> form = invoiceForm("采购发票", "华东铝业", List.of(
                invoiceRow("PURCHASE_IN", "RK-001", 1, 10, 10, 13, 13, 113)));

        service.callButton("PU_INVOICE", "新增流程", form, Map.of());

        ArgumentCaptor<FormData> inserted = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).insert(inserted.capture());
        Map<?, ?> head = json.readValue(inserted.getValue().getData(), Map.class);
        assertEquals("已勾稽", head.get("勾稽状态"));
        assertEquals(100.0, number(head.get("不含税金额")), 0.0001);
        assertEquals(13.0, number(head.get("税额")), 0.0001);
        assertEquals(113.0, number(head.get("价税合计")), 0.0001);
    }

    @Test
    void backfillsBlankInvoiceSupplierFromFirstSource() throws Exception {
        stubInvoiceSave(purchaseInWithTwoLines());
        Map<String, Object> form = invoiceForm("采购发票", "", List.of(
                invoiceRow("PURCHASE_IN", "RK-001", 1, 2, 10, 13, 2.6, 22.6)));

        service.callButton("PU_INVOICE", "新增流程", form, Map.of());

        ArgumentCaptor<FormData> inserted = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).insert(inserted.capture());
        Map<?, ?> head = json.readValue(inserted.getValue().getData(), Map.class);
        assertEquals("华东铝业", head.get("供应商"));
        assertEquals("KH001", head.get("供应商编码"));
    }

    @Test
    void rejectsInvoiceSupplierDifferentFromSource() throws Exception {
        stubInvoiceSave(purchaseInWithTwoLines());
        Map<String, Object> form = invoiceForm("采购发票", "中天精工", List.of(
                invoiceRow("PURCHASE_IN", "RK-001", 1, 1, 10, 13, 1.3, 11.3)));

        IllegalStateException error = assertThrows(IllegalStateException.class,
                () -> service.callButton("PU_INVOICE", "新增流程", form, Map.of()));

        assertTrue(error.getMessage().contains("发票供应商与来源单据不一致"));
        verify(formMapper, never()).insert(any(FormData.class));
    }

    @Test
    void rejectsPositiveRedInvoiceLine() throws Exception {
        stubInvoiceSave(purchaseReturnWithQuantity());
        Map<String, Object> form = invoiceForm("采购发票(退货)", "华东铝业", List.of(
                invoiceRow("PURCHASE_RETURN", "CT-001", 1, 2, 10, 13, 2.6, 22.6)));

        IllegalStateException error = assertThrows(IllegalStateException.class,
                () -> service.callButton("PU_INVOICE", "新增流程", form, Map.of()));

        assertTrue(error.getMessage().contains("红字发票数量/税额/价税合计必须为负数"));
        verify(formMapper, never()).insert(any(FormData.class));
    }

    @Test
    void savesNegativeRedInvoiceAgainstReturnLine() throws Exception {
        stubInvoiceSave(purchaseReturnWithQuantity());
        Map<String, Object> form = invoiceForm("采购发票(退货)", "华东铝业", List.of(
                invoiceRow("PURCHASE_RETURN", "CT-001", 1, -2, 10, 13, -2.6, -22.6)));

        service.callButton("PU_INVOICE", "新增流程", form, Map.of());

        ArgumentCaptor<FormData> inserted = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).insert(inserted.capture());
        Map<?, ?> head = json.readValue(inserted.getValue().getData(), Map.class);
        assertEquals(-20.0, number(head.get("不含税金额")), 0.0001);
        assertEquals(-2.6, number(head.get("税额")), 0.0001);
        assertEquals(-22.6, number(head.get("价税合计")), 0.0001);
        assertEquals("部分勾稽", head.get("勾稽状态"));
    }

    // ---------- fixtures ----------

    private void stubInvoiceSave(FormData source) throws Exception {
        when(formMapper.selectOne(any())).thenReturn(source);
        when(formMapper.selectList(any())).thenReturn(List.of());
        // 拒绝类用例在生成编号/读取面板配置之前就抛错，这两项用 lenient 保持共享夹具
        lenient().when(formMapper.selectCount(any())).thenReturn(0L);
        lenient().when(panelMapper.selectOne(any())).thenReturn(invoiceConfig());
    }

    /** 入库单两行：第 1 行 10 件、第 2 行 10 件，均为含税 113。 */
    private FormData purchaseInWithTwoLines() throws Exception {
        return document("PURCHASE_IN", "RK-001", "已审核",
                Map.of("单据编号", "RK-001", "供应商", "华东铝业", "供应商编码", "KH001"),
                Map.of("items", List.of(
                        fields("存货编码", "CL001", "存货名称", "铝棒", "计量单位", "件",
                                "实收数量", 10, "数量", 10, "单价", 10, "税率%", 13,
                                "含税单价", 11.3, "金额", 100, "税额", 13, "含税金额", 113),
                        fields("存货编码", "CL002", "存货名称", "铝板", "计量单位", "kg",
                                "实收数量", 10, "数量", 10, "单价", 10, "税率%", 13,
                                "含税单价", 11.3, "金额", 100, "税额", 13, "含税金额", 113))));
    }

    /** 退货单一行 5 件：实收数量故意留 0，验证可开票量以「数量」为准。 */
    private FormData purchaseReturnWithQuantity() throws Exception {
        return document("PURCHASE_RETURN", "CT-001", "已审核",
                Map.of("供应商", "华东铝业", "供应商编码", "KH001", "入库单号", "RK-001"),
                Map.of("items", List.of(fields(
                        "存货编码", "CL001", "存货名称", "铝棒", "计量单位", "件",
                        "数量", 5, "实收数量", 0, "单价", 10, "税率%", 13,
                        "含税单价", 11.3, "金额", 50, "税额", 6.5, "含税金额", 56.5))));
    }

    private Map<String, Object> invoiceForm(String bizType, String supplier, List<Map<String, Object>> rows) {
        Map<String, Object> form = new HashMap<>();
        form.put("单据日期", "2026-11-06");
        form.put("业务类型", bizType);
        form.put("供应商", supplier);
        form.put("detail", Map.of("items", rows));
        return form;
    }

    private Map<String, Object> invoiceRow(String sourcePanel, String sourceNo, int lineNo,
                                           double quantity, double price, double taxRate,
                                           double tax, double gross) {
        Map<String, Object> row = fields(
                "来源面板", sourcePanel, "来源单号", sourceNo, "来源行号", lineNo,
                "存货编码", "CL001", "存货名称", "铝棒", "计量单位", "件",
                "数量", quantity, "无税单价", price, "税率%", taxRate,
                "税额", tax, "价税合计", gross);
        return row;
    }

    private static Map<String, Object> fields(Object... values) {
        Map<String, Object> map = new HashMap<>();
        for (int index = 0; index + 1 < values.length; index += 2) {
            map.put(String.valueOf(values[index]), values[index + 1]);
        }
        return map;
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> listOf(Map<String, Object> result) {
        return (List<Map<String, Object>>) result.get("list");
    }

    private static double number(Object value) {
        return ((Number) value).doubleValue();
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
                        Map.of("dataName", "供应商编码", "dataType", "文本"),
                        Map.of("dataName", "开票类型", "dataType", "文本"),
                        Map.of("dataName", "发票号码", "dataType", "文本"),
                        Map.of("dataName", "开票日期", "dataType", "日期"),
                        Map.of("dataName", "来源单据号", "dataType", "文本"),
                        Map.of("dataName", "不含税金额", "dataType", "小数"),
                        Map.of("dataName", "税额", "dataType", "小数"),
                        Map.of("dataName", "价税合计", "dataType", "小数"),
                        Map.of("dataName", "勾稽状态", "dataType", "下拉框", "defaultValue", "未勾稽"))),
                "detail", Map.of("tabs", List.of(Map.of(
                        "key", "items", "label", "明细",
                        "fields", List.of(
                                Map.of("dataName", "源单类型", "dataType", "文本"),
                                Map.of("dataName", "源单编号", "dataType", "文本"),
                                Map.of("dataName", "源单行号", "dataType", "整数"),
                                Map.of("dataName", "存货编码", "dataType", "文本"),
                                Map.of("dataName", "存货名称", "dataType", "文本"),
                                Map.of("dataName", "计量单位", "dataType", "文本"),
                                Map.of("dataName", "数量", "dataType", "小数"),
                                Map.of("dataName", "入库数量", "dataType", "小数"),
                                Map.of("dataName", "已开票数量", "dataType", "小数"),
                                Map.of("dataName", "未开票数量", "dataType", "小数"),
                                Map.of("dataName", "无税单价", "dataType", "小数"),
                                Map.of("dataName", "税率%", "dataType", "小数"),
                                Map.of("dataName", "税额", "dataType", "小数"),
                                Map.of("dataName", "价税合计", "dataType", "小数"))))))));
        return config;
    }
}
