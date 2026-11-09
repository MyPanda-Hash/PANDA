package com.mes.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.entity.PanelConfig;
import com.mes.mapper.FormApprovalMapper;
import com.mes.mapper.FormDataMapper;
import com.mes.mapper.PanelConfigMapper;
import com.mes.panel.PanelActionRegistry;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doReturn;
import static org.mockito.Mockito.spy;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 销售订单 → 生产加工单 的生单预览与按选明细生成。
 *
 * <p>要解决的现场问题：
 * <ul>
 *   <li>生单前看不到库存，不知道哪些货不够，只能盲生；</li>
 *   <li>已经有货的也一起投产，造成过度生产；</li>
 *   <li>同一订单已经下过加工单，再点一次又整单投产一遍。</li>
 * </ul>
 *
 * <p>预期：预览逐行给出 需求数量 / 现存量 / 已下单生产数量 / 建议生产数量 / 是否不足；
 * 生成加工单时可只带用户勾选的行与数量。
 */
@ExtendWith(MockitoExtension.class)
class PxServiceProducePreviewTest {

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

    private PanelConfig config(String code) {
        PanelConfig cfg = new PanelConfig();
        cfg.setPanelCode(code);
        cfg.setPanelName(code);
        cfg.setConfig("{\"metadata\":{\"panelCode\":\"" + code
                + "\",\"autoCodeField\":\"单据编号\",\"panelButtons\":[],\"buttonGroups\":[],\"panelPageDto\":{}},"
                + "\"dataSchema\":{\"fields\":[]},\"detail\":{\"tabs\":[]}}");
        return cfg;
    }

    /** 两张产品：CP001 需求 100、CP002 需求 10。 */
    private FormData saleOrder() throws Exception {
        return document("SO_ORDER", "SO-1", "已审核",
                Map.of("单据编号", "SO-1", "客户", "华东铝业", "客户编码", "KH001"),
                Map.of("items", List.of(
                        Map.of("存货编码", "CP001", "存货名称", "铝棒 Φ80", "规格型号", "Φ80×3000",
                                "数量", 100, "销售单位", "件"),
                        Map.of("存货编码", "CP002", "存货名称", "铝板 6061", "规格型号", "1500×3000×2",
                                "数量", 10, "销售单位", "件"))));
    }

    @Test
    void previewExposesStockAndSuggestedQuantity() throws Exception {
        PxService spied = spy(service);
        doReturn(Map.of("CP001", 0.0, "CP002", 0.0)).when(spied).producedQtyByItem(anyString());
        when(formMapper.selectOne(any())).thenReturn(saleOrder());
        when(stockTxnService.quantityOf(eq("CP001"), anyString(), eq("成品仓"))).thenReturn(new BigDecimal("60"));
        when(stockTxnService.quantityOf(eq("CP002"), anyString(), eq("成品仓"))).thenReturn(new BigDecimal("50"));

        List<Map<String, Object>> rows = spied.producePreview("SO-1");

        assertEquals(2, rows.size());
        Map<String, Object> cp001 = rows.get(0);
        assertEquals("CP001", cp001.get("存货编码"));
        assertEquals(100.0, ((Number) cp001.get("需求数量")).doubleValue(), 0.001);
        assertEquals(60.0, ((Number) cp001.get("现存量")).doubleValue(), 0.001);
        assertEquals(40.0, ((Number) cp001.get("建议生产数量")).doubleValue(), 0.001, "建议生产 = 需求 - 现存量");
        assertEquals(true, cp001.get("是否不足"));

        Map<String, Object> cp002 = rows.get(1);
        assertEquals(10.0, ((Number) cp002.get("需求数量")).doubleValue(), 0.001);
        assertEquals(50.0, ((Number) cp002.get("现存量")).doubleValue(), 0.001);
        assertEquals(0.0, ((Number) cp002.get("建议生产数量")).doubleValue(), 0.001, "库存够的行不建议再生产");
        assertEquals(false, cp002.get("是否不足"));
    }

    /**
     * 同一存货出现在多行时，库存要按行顺序分配，不能每行都用全量现存量去减，
     * 否则会算出「两行都够货」这种根本不存在的结果。
     */
    @Test
    void previewAllocatesStockAcrossDuplicateLines() throws Exception {
        PxService spied = spy(service);
        doReturn(Map.of()).when(spied).producedQtyByItem(anyString());
        FormData duplicated = document("SO_ORDER", "SO-2", "已审核",
                Map.of("单据编号", "SO-2", "客户", "华东铝业"),
                Map.of("items", List.of(
                        Map.of("存货编码", "CP001", "存货名称", "铝棒 Φ80", "数量", 60, "销售单位", "件"),
                        Map.of("存货编码", "CP001", "存货名称", "铝棒 Φ80", "数量", 60, "销售单位", "件"))));
        when(formMapper.selectOne(any())).thenReturn(duplicated);
        when(stockTxnService.quantityOf(eq("CP001"), anyString(), eq("成品仓")))
                .thenReturn(new BigDecimal("80"));

        List<Map<String, Object>> rows = spied.producePreview("SO-2");

        assertEquals(2, rows.size());
        // 第 1 行吃掉 60，剩 20；第 2 行只能满足 20，还差 40
        assertEquals(0.0, ((Number) rows.get(0).get("建议生产数量")).doubleValue(), 0.001);
        assertEquals(false, rows.get(0).get("是否不足"));
        assertEquals(40.0, ((Number) rows.get(1).get("建议生产数量")).doubleValue(), 0.001,
                "第 2 行只能用剩下的 20，建议生产 40");
        assertEquals(true, rows.get(1).get("是否不足"));
    }

    /** 已下过加工单的数量要扣掉，否则重复点生单会越做越多。 */
    @Test
    void previewDeductsAlreadyOrderedQuantity() throws Exception {
        PxService spied = spy(service);
        doReturn(Map.of("CP001", 100.0, "CP002", 0.0)).when(spied).producedQtyByItem(anyString());
        when(formMapper.selectOne(any())).thenReturn(saleOrder());
        when(stockTxnService.quantityOf(eq("CP001"), anyString(), eq("成品仓"))).thenReturn(BigDecimal.ZERO);
        when(stockTxnService.quantityOf(eq("CP002"), anyString(), eq("成品仓"))).thenReturn(new BigDecimal("10"));

        List<Map<String, Object>> rows = spied.producePreview("SO-1");

        Map<String, Object> cp001 = rows.get(0);
        assertEquals(100.0, ((Number) cp001.get("已下单生产数量")).doubleValue(), 0.001);
        assertEquals(0.0, ((Number) cp001.get("建议生产数量")).doubleValue(), 0.001,
                "已全部下过单，建议生产应为 0");
        assertEquals(false, cp001.get("是否不足"));
    }

    /** 勾选后只生成被勾选的行，且数量以用户填的为准。 */
    @Test
    void createsMoWithSelectedLinesOnly() throws Exception {
        PxService spied = spy(service);
        doReturn(null).when(spied).findBlockingSaleOut(anyString());
        when(panelMapper.selectOne(any())).thenReturn(config("MANU_ORDER"));
        when(formMapper.selectOne(any())).thenReturn(saleOrder());
        when(formMapper.selectList(any())).thenReturn(List.of());
        when(formMapper.selectCount(any())).thenReturn(0L);

        spied.callButton("SO_ORDER", "生成生产加工单",
                Map.of("编号", "SO-1"),
                Map.of("lines", List.of(Map.of("存货编码", "CP001", "数量", 40))));

        FormData target = captureInserted();
        Map<?, ?> detail = json.readValue(target.getDetailData(), Map.class);
        List<?> products = (List<?>) detail.get("products");
        assertEquals(1, products.size(), "只生成勾选的那一行");
        Map<?, ?> p = (Map<?, ?>) products.get(0);
        assertEquals("CP001", p.get("产品编码"));
        assertEquals(40.0, ((Number) p.get("数量")).doubleValue(), 0.001, "数量以用户填的为准，不照抄订单");
    }

    /** 没传 lines 时保持旧行为：整单明细都投产（供接口/脚本兼容）。 */
    @Test
    void createsMoWithAllItemsWhenNoSelection() throws Exception {
        PxService spied = spy(service);
        doReturn(null).when(spied).findBlockingSaleOut(anyString());
        when(panelMapper.selectOne(any())).thenReturn(config("MANU_ORDER"));
        when(formMapper.selectOne(any())).thenReturn(saleOrder());
        when(formMapper.selectList(any())).thenReturn(List.of());
        when(formMapper.selectCount(any())).thenReturn(0L);

        spied.callButton("SO_ORDER", "生成生产加工单", Map.of("编号", "SO-1"), Map.of());

        FormData target = captureInserted();
        Map<?, ?> detail = json.readValue(target.getDetailData(), Map.class);
        assertEquals(2, ((List<?>) detail.get("products")).size());
    }

    /** 数量 <= 0 的行即使被勾选也不投产。 */
    @Test
    void skipsSelectedLinesWithNonPositiveQuantity() throws Exception {
        PxService spied = spy(service);
        doReturn(null).when(spied).findBlockingSaleOut(anyString());
        when(panelMapper.selectOne(any())).thenReturn(config("MANU_ORDER"));
        when(formMapper.selectOne(any())).thenReturn(saleOrder());
        when(formMapper.selectList(any())).thenReturn(List.of());
        when(formMapper.selectCount(any())).thenReturn(0L);

        spied.callButton("SO_ORDER", "生成生产加工单",
                Map.of("编号", "SO-1"),
                Map.of("lines", List.of(
                        Map.of("存货编码", "CP001", "数量", 0),
                        Map.of("存货编码", "CP002", "数量", 5))));

        FormData target = captureInserted();
        Map<?, ?> detail = json.readValue(target.getDetailData(), Map.class);
        List<?> products = (List<?>) detail.get("products");
        assertEquals(1, products.size(), "数量为 0 的行不投产");
        assertEquals("CP002", ((Map<?, ?>) products.get(0)).get("产品编码"));
        assertNotNull(detail.get("processes"));
    }

    private FormData captureInserted() {
        ArgumentCaptor<FormData> captor = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).insert(captor.capture());
        return captor.getValue();
    }
}