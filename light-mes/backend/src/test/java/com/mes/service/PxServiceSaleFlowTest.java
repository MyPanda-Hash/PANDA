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
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doReturn;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.spy;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 销售订单的两条生单线（2026-11-05 补齐）。
 *
 * <p>业务规则：
 * <ol>
 *   <li>成品仓有货 → 可直接生成销售出库单；</li>
 *   <li>无货/数量不够 → 不能出库，应走生产加工单，完工入库后再出库；</li>
 *   <li>已经销售出库的销售订单，不允许再生成生产加工单；</li>
 *   <li>生产加工单可脱离销售订单直接排单（预生产入库），不受上述限制。</li>
 * </ol>
 *
 * <p>修复前的缺陷：{@code createSaleOutFromSo} 只校验订单已审核，完全没有库存校验，
 * 点「生单」就直接出一张数量照抄的销售出库单；{@code createMoFromSo} 也不检查该订单是否已出库。
 */
@ExtendWith(MockitoExtension.class)
class PxServiceSaleFlowTest {

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

    private FormData saleOrder(int qty) throws Exception {
        return document("SO_ORDER", "SO-1", "已审核",
                Map.of("单据编号", "SO-1", "客户", "华东铝业", "客户编码", "KH001"),
                Map.of("items", List.of(Map.of(
                        "存货编码", "CP001", "存货名称", "铝棒 Φ80", "规格型号", "Φ80×3000",
                        "数量", qty, "销售单位", "件", "含税单价", 20, "税率%", 13))));
    }

    // ---------- 线 1：有货才可直接出库 ----------

    @Test
    void rejectsSaleOutWhenStockInsufficient() throws Exception {
        // 库存校验在建单之前触发，走不到面板配置读取，故这里不 stub panelMapper
        when(formMapper.selectOne(any())).thenReturn(saleOrder(100));
        when(stockTxnService.quantityOf(eq("CP001"), anyString(), eq("成品仓")))
                .thenReturn(new BigDecimal("60"));

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                service.callButton("SO_ORDER", "生成销售出库单", Map.of("编号", "SO-1"), Map.of()));

        String msg = e.getMessage();
        assertTrue(msg.contains("库存不足"), "提示要说明库存不足：" + msg);
        assertTrue(msg.contains("60"), "提示要体现现存量：" + msg);
        assertTrue(msg.contains("100"), "提示要体现需求量：" + msg);
        assertTrue(msg.contains("生产加工单"), "提示要指向正确的下一步：" + msg);
        verify(formMapper, never()).insert(any(FormData.class));
    }

    @Test
    void generatesSaleOutWhenStockEnough() throws Exception {
        PxService spied = spy(service);
        doReturn(null).when(spied).findOpenSaleOut(anyString());
        doReturn(Map.of()).when(spied).shippedQtyByItem(anyString());
        when(panelMapper.selectOne(any())).thenReturn(config("SALE_OUT"));
        when(formMapper.selectOne(any())).thenReturn(saleOrder(100));
        when(stockTxnService.quantityOf(eq("CP001"), anyString(), eq("成品仓")))
                .thenReturn(new BigDecimal("200"));
        when(formMapper.selectCount(any())).thenReturn(0L);

        spied.callButton("SO_ORDER", "生成销售出库单", Map.of("编号", "SO-1"), Map.of());

        ArgumentCaptor<FormData> captor = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).insert(captor.capture());
        assertEquals("SALE_OUT", captor.getValue().getPanelCode());
    }

    /** 同一订单不能反复生成草稿出库单：草稿不写库存流水，否则每张都能过库存校验，审核后超卖。 */
    @Test
    void rejectsDuplicateSaleOutForSameOrder() throws Exception {
        PxService spied = spy(service);
        doReturn("草稿").when(spied).findOpenSaleOut("SO-1");
        when(formMapper.selectOne(any())).thenReturn(saleOrder(100));

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                spied.callButton("SO_ORDER", "生成销售出库单", Map.of("编号", "SO-1"), Map.of()));

        String msg = e.getMessage();
        assertTrue(msg.contains("已有销售出库单"), "提示要说明已存在出库单：" + msg);
        assertTrue(msg.contains("草稿"), "提示要带出已有单据的状态：" + msg);
        verify(formMapper, never()).insert(any(FormData.class));
    }

    /** 需求为 0 的行被跳过，不会因为空行把正常出库挡掉。 */
    @Test
    void skipsZeroQuantityLines() throws Exception {
        PxService spied = spy(service);
        doReturn(null).when(spied).findOpenSaleOut(anyString());
        doReturn(Map.of()).when(spied).shippedQtyByItem(anyString());
        when(panelMapper.selectOne(any())).thenReturn(config("SALE_OUT"));
        FormData mixed = document("SO_ORDER", "SO-9", "已审核",
                Map.of("单据编号", "SO-9", "客户", "华东铝业"),
                Map.of("items", List.of(
                        Map.of("存货编码", "CP001", "存货名称", "铝棒 Φ80", "数量", 0, "销售单位", "件"),
                        Map.of("存货编码", "CP002", "存货名称", "铝板 6061", "数量", 10, "销售单位", "件"))));
        when(formMapper.selectOne(any())).thenReturn(mixed);
        when(stockTxnService.quantityOf(eq("CP002"), anyString(), eq("成品仓")))
                .thenReturn(new BigDecimal("100"));
        when(formMapper.selectCount(any())).thenReturn(0L);

        spied.callButton("SO_ORDER", "生成销售出库单", Map.of("编号", "SO-9"), Map.of());

        FormData target = captureInserted();
        Map<?, ?> detail = json.readValue(target.getDetailData(), Map.class);
        List<?> items = (List<?>) detail.get("items");
        assertEquals(1, items.size(), "数量为 0 的行不出库");
        assertEquals("CP002", ((Map<?, ?>) items.get(0)).get("存货编码"));
    }

    // ---------- 线 2：已出库的订单不许再投生产 ----------

    @Test
    void rejectsCreateMoWhenSaleOutExists() throws Exception {
        PxService spied = spy(service);
        doReturn("已审核").when(spied).findBlockingSaleOut("SO-1");
        when(formMapper.selectOne(any())).thenReturn(saleOrder(100));

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                spied.callButton("SO_ORDER", "生成生产加工单", Map.of("编号", "SO-1"), Map.of()));

        String msg = e.getMessage();
        assertTrue(msg.contains("销售出库"), "提示要说明已有出库单：" + msg);
        assertTrue(msg.contains("不能再生成生产加工单"), "提示要给出结论：" + msg);
        assertTrue(msg.contains("直接新增"), "要告诉用户预生产怎么办：" + msg);
        verify(formMapper, never()).insert(any(FormData.class));
    }

    @Test
    void createsMoWhenNoSaleOut() throws Exception {
        PxService spied = spy(service);
        doReturn(null).when(spied).findBlockingSaleOut(anyString());
        when(panelMapper.selectOne(any())).thenReturn(config("MANU_ORDER"));
        when(formMapper.selectOne(any())).thenReturn(saleOrder(100));
        when(formMapper.selectList(any())).thenReturn(List.of());
        when(formMapper.selectCount(any())).thenReturn(0L);

        spied.callButton("SO_ORDER", "生成生产加工单", Map.of("编号", "SO-1"), Map.of());

        ArgumentCaptor<FormData> captor = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).insert(captor.capture());
        assertEquals("MANU_ORDER", captor.getValue().getPanelCode());
        Map<?, ?> detail = json.readValue(captor.getValue().getDetailData(), Map.class);
        assertNotNull(detail.get("products"), "加工单必须带产成品明细");
    }

    private FormData captureInserted() {
        ArgumentCaptor<FormData> captor = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).insert(captor.capture());
        return captor.getValue();
    }

    /** 销售出库明细要存净价与税价两套金额，否则销货单选单会把含税金额当净额重复计税。 */
    @Test
    void saleOutStoresNetAndGrossAmounts() throws Exception {
        FormData so = document("SO_ORDER", "SO-COST-1", "已审核",
                Map.of("单据编号", "SO-COST-1", "客户", "华东铝业", "客户编码", "KH001", "结算客户", "华东铝业"),
                Map.of("items", List.of(Map.of(
                        "存货编码", "CP004", "存货名称", "减速箱体 A", "数量", 2,
                        "销售单位", "件", "单价", 480, "税率%", 13, "含税单价", 542.4))));
        when(formMapper.selectOne(any())).thenReturn(so);
        when(panelMapper.selectOne(any())).thenReturn(config("SALE_OUT"));
        when(formMapper.selectCount(any())).thenReturn(0L);
        when(stockTxnService.quantityOf(any(), any(), any())).thenReturn(new BigDecimal("10"));

        service.callButton("SO_ORDER", "生成销售出库单", Map.of("编号", "SO-COST-1"),
                Map.of("warehouse", "成品仓", "lines", List.of(Map.of("存货编码", "CP004", "数量", 2))));

        ArgumentCaptor<FormData> captor = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).insert(captor.capture());
        Map<?, ?> detail = json.readValue(captor.getValue().getDetailData(), Map.class);
        Map<?, ?> row = (Map<?, ?>) ((List<?>) detail.get("items")).get(0);
        assertEquals(480.0, ((Number) row.get("售价")).doubleValue(), 0.01, "售价=不含税单价");
        assertEquals(542.4, ((Number) row.get("含税售价")).doubleValue(), 0.01);
        assertEquals(960.0, ((Number) row.get("销售金额")).doubleValue(), 0.01);
        assertEquals(1084.8, ((Number) row.get("含税销售金额")).doubleValue(), 0.01);
        assertEquals(124.8, ((Number) row.get("税额")).doubleValue(), 0.01);
    }
}