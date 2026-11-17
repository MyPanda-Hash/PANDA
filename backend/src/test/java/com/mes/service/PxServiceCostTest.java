package com.mes.service;

import com.mes.entity.FormData;
import com.mes.entity.PanelConfig;
import com.mes.mapper.FormApprovalMapper;
import com.mes.mapper.FormDataMapper;
import com.mes.mapper.PanelConfigMapper;
import com.mes.panel.PanelActionRegistry;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyDouble;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doNothing;
import static org.mockito.Mockito.spy;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 自制成本与销售成本结转口径。
 *
 * <p>产成品入库按「材料结存均价 × 计划用量 + 工序工价 × 计划数量」折出单位成本；
 * 销售出库按出库时刻的结存均价取成本价，会计凭证记 借 6401 主营业务成本 / 贷 1405 库存商品。
 */
@ExtendWith(MockitoExtension.class)
class PxServiceCostTest {

    @Mock private PanelConfigMapper panelMapper;
    @Mock private FormDataMapper formMapper;
    @Mock private FormApprovalMapper approvalMapper;
    @Mock private ReportQueryService reportQueryService;
    @Mock private StockTxnService stockTxnService;
    @Mock private AccountingService accountingService;

    private PxService service;

    @BeforeEach
    void setUp() {
        // 用完整构造器：凭证相关行为需要 accountingService 非空，否则 postAccounting 直接返回
        service = new PxService(panelMapper, formMapper, approvalMapper, reportQueryService,
                new PanelActionRegistry(List.of()), null, null, stockTxnService,
                null, null, null, accountingService, null);
    }

    @Test
    void unitCostAddsMaterialAndLabor() {
        Map<String, Object> moDetail = Map.of(
                "products", List.of(Map.of("产品编码", "CP004", "数量", 2)),
                "materials", List.of(
                        Map.of("材料编码", "CL001", "材料名称", "45#圆钢", "计划数量", 4),
                        Map.of("材料编码", "CL004", "材料名称", "切削液", "计划数量", 2)),
                "processes", List.of(
                        Map.of("工序编码", "PX001", "工价", 5, "计划数量", 2),
                        Map.of("工序编码", "PX002", "工价", 2.5, "计划数量", 2)));
        when(stockTxnService.averageCostOf(eq("CL001"), any())).thenReturn(new BigDecimal("10"));
        when(stockTxnService.averageCostOf(eq("CL004"), any())).thenReturn(new BigDecimal("1"));

        // 材料 4×10 + 2×1 = 42；工费 (5+2.5)×2 = 15；合计 57 / 2 件 = 28.5
        assertEquals(28.5, service.manufactureUnitCost(moDetail, 2), 0.001);
    }

    @Test
    void unitCostIsZeroWithoutQuantity() {
        Map<String, Object> moDetail = Map.of("products", List.of(), "materials", List.of(), "processes", List.of());
        assertEquals(0.0, service.manufactureUnitCost(moDetail, 0), 0.001);
    }

    @Test
    void accountingAmountUsesCostForSaleOut() {
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货编码", "CP004", "数量", 5, "成本价", 30, "成本金额", 150,
                        "销售金额", 500, "含税销售金额", 565)));

        assertEquals(150.0, service.accountingAmount("SALE_OUT", detail), 0.001, "成本结转按成本金额，不按销售额");
    }

    @Test
    void accountingAmountFallsBackToCostPriceTimesQuantity() {
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货编码", "CP004", "数量", 5, "成本价", 30, "销售金额", 500)));

        assertEquals(150.0, service.accountingAmount("SALE_OUT", detail), 0.001);
    }

    @Test
    void accountingAmountKeepsSalesAmountForOtherPanels() {
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货编码", "CP004", "数量", 5, "含税金额", 565, "成本价", 30)));

        assertEquals(565.0, service.accountingAmount("SALE_INV", detail), 0.001);
    }

    /**
     * 接线测试：销售出库的凭证金额必须走成本口径。
     *
     * <p>这是补上真正漏掉的那一环 —— accountingAmount 的三个单测一直是绿的，
     * 但生产代码调用的是通用 settlementAmount，于是成本 45.77 元被记成销售金额 600 元。
     * 本用例盯的是 postAccounting 的调用参数，函数本身算对不算数。
     */
    @Test
    void postAccountingSendsCostAmountForSaleOut() {
        Map<String, Object> head = Map.of("客户", "华东铝业", "备注", "销售出库");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货编码", "CP005", "数量", 10, "成本价", 4.58, "成本金额", 45.77,
                        "销售金额", 600, "含税销售金额", 678)));

        service.postAccounting("SALE_OUT", "CK-TEST-001", head, detail);

        org.mockito.ArgumentCaptor<BigDecimal> amount = org.mockito.ArgumentCaptor.forClass(BigDecimal.class);
        verify(accountingService).post(eq("SALE_OUT"), eq("CK-TEST-001"), amount.capture(),
                any(), any(), any());
        assertEquals(0, new BigDecimal("45.77").compareTo(amount.getValue()),
                "凭证金额应为成本金额 45.77，而不是销售金额 600");
    }

    /** 反向保护：其它单据仍按单据金额入账，不能被成本口径带偏。 */
    @Test
    void postAccountingKeepsInvoiceAmountForSaleInvoice() {
        Map<String, Object> head = Map.of("客户", "华东铝业");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货编码", "CP005", "数量", 10, "含税金额", 678, "成本价", 4.58)));

        service.postAccounting("SALE_INV", "XS-TEST-001", head, detail);

        org.mockito.ArgumentCaptor<BigDecimal> gross = org.mockito.ArgumentCaptor.forClass(BigDecimal.class);
        verify(accountingService).postSaleInvoice(eq("XS-TEST-001"), gross.capture(),
                any(), any(), any(), any());
        assertEquals(0, new BigDecimal("678").compareTo(gross.getValue()),
                "销货单按含税金额确认应收，不走成本口径");
    }

    private FormData doc(String panel, String no, String status, Map<String, Object> head, Map<String, Object> detail)
            throws Exception {
        FormData d = new FormData();
        d.setId(1L);
        d.setPanelCode(panel);
        d.setFormNo(no);
        d.setStatus(status);
        d.setData(new ObjectMapper().writeValueAsString(head));
        d.setDetailData(new ObjectMapper().writeValueAsString(detail));
        d.setCreateTime(LocalDateTime.now());
        return d;
    }

    private PanelConfig config(String code) {
        PanelConfig cfg = new PanelConfig();
        cfg.setPanelCode(code);
        cfg.setPanelName(code);
        cfg.setConfig("{\"metadata\":{\"panelCode\":\"" + code
                + "\",\"autoCodeField\":\"单据编号\",\"panelButtons\":[],\"buttonGroups\":[],\"panelPageDto\":{}},"
                + "\"dataSchema\":{\"fields\":[]},\"detail\":{\"tabs\":[]}}");
        return cfg;
    }

    /** 生产领料按库存结存均价出库，否则领料不冲减结存金额，平均成本越滚越大。 */
    @Test
    void materialIssuePricesAtStockAverage() throws Exception {
        FormData batch = doc("DISPATCH", "PG-1", "已审核",
                Map.of("单据编号", "PG-1", "加工单号", "MO-1", "生产车间", "熔铸车间"),
                Map.of("items", List.of(Map.of("工序编码", "PX001", "工序名称", "下料", "派工数量", 20))));
        FormData mo = doc("MANU_ORDER", "MO-1", "已审核",
                Map.of("单据编号", "MO-1"),
                Map.of("products", List.of(Map.of("产品编码", "CP004", "数量", 20)),
                        "processes", List.of(),
                        "materials", List.of(Map.of("材料编码", "CL001", "材料名称", "45#圆钢",
                                "计划数量", 882, "预出仓库", "原料仓", "单价", 0))));

        PxService spied = spy(service);
        doNothing().when(spied).validateBatchIssue(anyString(), anyString(), anyString(), anyDouble(), any());
        doNothing().when(spied).validateMaterialIssue(any(), any());
        doNothing().when(spied).submitIssueForApproval(anyString(), any());
        when(panelMapper.selectOne(any())).thenReturn(config("MATERIAL_OUT"));
        when(formMapper.selectOne(any())).thenReturn(batch, mo);
        when(formMapper.selectCount(any())).thenReturn(0L);
        when(stockTxnService.averageCostOf(eq("CL001"), any(), eq("原料仓"))).thenReturn(new BigDecimal("6.5"));

        spied.issueBatch("PG-1", List.of(Map.of("材料编码", "CL001", "数量", 882)), "王强", "");

        org.mockito.ArgumentCaptor<FormData> captor = org.mockito.ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).insert(captor.capture());
        Map<?, ?> detail = new ObjectMapper().readValue(captor.getValue().getDetailData(), Map.class);
        Map<?, ?> row = (Map<?, ?>) ((List<?>) detail.get("items")).get(0);
        assertEquals(6.5, ((Number) row.get("单价")).doubleValue(), 0.001, "领料单价取结存均价");
        assertEquals(5733.0, ((Number) row.get("金额")).doubleValue(), 0.001);
    }

    /** 销货单税额：优先取明细税额合计，缺税额时按含税金额×税率/(100+税率) 反算。 */
    @Test
    void taxAmountSumsDetailTaxOrDerivesFromRate() {
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("含税金额", 1084.8, "税额", 124.8),
                Map.of("含税金额", 2169.6, "税额", 249.6)));
        assertEquals(374.4, service.taxAmount(detail), 0.001);

        Map<String, Object> noTax = Map.of("items", List.of(
                Map.of("含税金额", 1130, "税率%", 13)));
        assertEquals(130.0, service.taxAmount(noTax), 0.001);
    }
}