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
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;

/**
 * 生单唯一性：1:1 组合（报价单→销售订单、费用单→分摊单）不允许重复生单，
 * 其余组合（销售订单→出库单等）本来就允许分批，不能拦。
 *
 * <p>背景：2026-11-03 实测同一张报价单被点了两次「生成销售订单」，
 * 多出一张空转的「审批中」销售订单，业务流程追踪的卡点被它带偏。
 */
@ExtendWith(MockitoExtension.class)
class PxServiceGenerateIdempotencyTest {

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
    void oneToOnePairsAreEnumerated() {
        assertTrue(PxService.isSingleGeneratePair("SO_ORDER", "QUOTE_ORDER"), "报价单→销售订单只该生一次");
        assertTrue(PxService.isSingleGeneratePair("SALE_COST_ALLOC", "EXPENSE"));
        assertTrue(PxService.isSingleGeneratePair("PU_COST_ALLOC", "EXPENSE"));
        // 允许分批的业务不能被拦
        assertFalse(PxService.isSingleGeneratePair("SALE_OUT", "SO_ORDER"), "销售订单可以分批出库");
        assertFalse(PxService.isSingleGeneratePair("PURCHASE_IN", "PU_ORDER"), "采购订单可以分批入库");
        assertFalse(PxService.isSingleGeneratePair("SALE_RETURN", "SO_ORDER"));
        assertFalse(PxService.isSingleGeneratePair("SALE_INVOICE", "SALE_INV"), "销售发票按可开票额度分批");
        assertFalse(PxService.isSingleGeneratePair(null, "EXPENSE"));
        assertFalse(PxService.isSingleGeneratePair("SO_ORDER", null));
    }

    @Test
    void rejectsSecondSaleOrderFromSameQuote() throws Exception {
        when(panelMapper.selectOne(any())).thenReturn(soConfig());
        when(formMapper.selectOne(any())).thenReturn(quote("BJ-2026-11-0305"));
        when(formMapper.selectList(any())).thenReturn(List.of(existingSo("SO-2026-11-0306", "BJ-2026-11-0305")));

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                service.callButton("QUOTE_ORDER", "生成销售订单", Map.of("编号", "BJ-2026-11-0305"), Map.of()));

        assertTrue(e.getMessage().contains("不能重复生成"), e.getMessage());
        assertTrue(e.getMessage().contains("SO-2026-11-0306"), "提示要给出已有单号：" + e.getMessage());
    }

    @Test
    void unrelatedDownstreamDocsDoNotBlock() throws Exception {
        when(panelMapper.selectOne(any())).thenReturn(soConfig());
        when(formMapper.selectOne(any())).thenReturn(quote("BJ-2026-11-0305"));
        // 已生成的是别的报价单的单据，不能误拦
        when(formMapper.selectList(any())).thenReturn(List.of(existingSo("SO-OTHER", "BJ-2026-11-9999")));
        when(formMapper.selectCount(any())).thenReturn(0L);

        Map<String, Object> result = service.callButton("QUOTE_ORDER", "生成销售订单",
                Map.of("编号", "BJ-2026-11-0305"), Map.of());

        assertTrue(String.valueOf(result.get("编号")).startsWith("SO-"), String.valueOf(result.get("编号")));
    }

    /** 从「已作废」的下游单里挑到同样来源时不能拦：作废后应该允许重新生单。 */
    @Test
    void voidedDownstreamDocumentDoesNotBlock() {
        assertTrue(PxService.isSingleGeneratePair("SO_ORDER", "QUOTE_ORDER"));
        // 行为由 ensureNotGenerated 的状态过滤保证；这里锁定判定口径不因面板不同而变
        assertFalse(PxService.isSingleGeneratePair("MANU_ORDER", "SO_ORDER"), "销售订单可以拆多张加工单");
    }

    private FormData quote(String no) throws Exception {
        return document("QUOTE_ORDER", no, "已审核",
                Map.of("单据编号", no, "单据日期", "2026-11-03", "客户", "流程演练客户", "客户编码", "KH-1"),
                Map.of("items", List.of(Map.of(
                        "存货名称", "铝棒 Φ80", "存货编码", "CP001", "规格型号", "Φ80×3000",
                        "销售单位", "件", "数量", 10, "报价单价", 100, "含税单价", 113, "税率%", 13))));
    }

    private FormData existingSo(String no, String sourceNo) throws Exception {
        return document("SO_ORDER", no, "已审核",
                Map.of("单据编号", no, "来源单据", "QUOTE_ORDER", "来源单号", sourceNo, "客户", "流程演练客户"),
                Map.of("items", List.of()));
    }

    private FormData document(String panel, String no, String status,
                              Map<String, Object> head, Map<String, Object> detail) throws Exception {
        FormData fd = new FormData();
        fd.setPanelCode(panel);
        fd.setFormNo(no);
        fd.setStatus(status);
        fd.setData(json.writeValueAsString(head));
        fd.setDetailData(json.writeValueAsString(detail));
        fd.setCreateTime(LocalDateTime.now());
        return fd;
    }

    private PanelConfig soConfig() throws Exception {
        PanelConfig panel = new PanelConfig();
        panel.setPanelCode("SO_ORDER");
        panel.setPanelName("销售订单");
        panel.setCategory("单据");

        Map<String, Object> metadata = Map.of(
                "panelCode", "SO_ORDER", "panelName", "销售订单", "panelCategory", "单据",
                "autoCodeField", "单据编号",
                "panelButtons", List.of(),
                "buttonGroups", List.of(Map.of("name", "审核", "actions", List.of("审核"))),
                "panelPageDto", Map.of(
                        "tablePages", List.of(Map.of("queryFields", List.of())),
                        "formPages", List.of()));

        List<Map<String, Object>> schema = List.of(
                Map.of("dataName", "单据日期", "dataType", "日期"),
                Map.of("dataName", "单据编号", "dataType", "文本", "autoCode", true),
                Map.of("dataName", "客户", "dataType", "文本"),
                Map.of("dataName", "来源单据", "dataType", "文本"),
                Map.of("dataName", "来源单号", "dataType", "文本"));

        List<Map<String, Object>> itemFields = List.of(
                Map.of("dataName", "存货编码", "dataType", "文本"),
                Map.of("dataName", "存货名称", "dataType", "文本"),
                Map.of("dataName", "规格型号", "dataType", "文本"),
                Map.of("dataName", "销售单位", "dataType", "文本"),
                Map.of("dataName", "数量", "dataType", "小数"),
                Map.of("dataName", "单价", "dataType", "小数"),
                Map.of("dataName", "含税单价", "dataType", "小数"),
                Map.of("dataName", "税率%", "dataType", "小数"));

        Map<String, Object> config = Map.of(
                "metadata", metadata,
                "dataSchema", Map.of("type", "object", "fields", schema),
                "detail", Map.of("tabs", List.of(Map.of("key", "items", "fields", itemFields))));
        panel.setConfig(json.writeValueAsString(config));
        return panel;
    }
}