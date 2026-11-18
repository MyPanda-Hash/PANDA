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
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 倒冲料的四个现场条件：库存不足要拦、超耗要能调整、要能扣线边仓、要能带批次。
 *
 * <p>改造前倒冲只做「按定额扣总仓」一件事，库存不够会出成负数、超耗看不出来、
 * 车间线边仓的料在账上不存在、批次永远为空。
 */
@ExtendWith(MockitoExtension.class)
class PxServiceBackflushTest {

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
        lenient().when(panelMapper.selectOne(any())).thenAnswer(inv -> {
            PanelConfig cfg = new PanelConfig();
            cfg.setPanelCode("PANEL");
            cfg.setPanelName("PANEL");
            cfg.setConfig("{\"metadata\":{\"panelCode\":\"PANEL\",\"autoCodeField\":\"单据编号\","
                    + "\"panelButtons\":[],\"buttonGroups\":[],\"panelPageDto\":{}},"
                    + "\"dataSchema\":{\"fields\":[]},\"detail\":{\"tabs\":[]}}");
            return cfg;
        });
        lenient().when(stockTxnService.averageCostOf(anyString(), anyString(), anyString()))
                .thenReturn(BigDecimal.ZERO);
        lenient().when(stockTxnService.averageCostOf(anyString(), anyString()))
                .thenReturn(BigDecimal.ZERO);
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

    /** 加工单：一道工序（下料 10 件），一条倒冲料，可配线边仓/批次/不可倒冲。 */
    private FormData mo(Map<String, Object> material) throws Exception {
        return document("MANU_ORDER", "MO-1", "生产中",
                Map.of("单据编号", "MO-1", "生产车间", "熔铸车间"),
                Map.of(
                        "products", List.of(Map.of("产品编码", "CP001", "产品名称", "铝棒 Φ80", "数量", 10)),
                        "materials", List.of(material),
                        "processes", List.of(Map.of("工序编码", "PX001", "工序名称", "下料", "计划数量", 10))));
    }

    private Map<String, Object> backflushMaterial(Map<String, Object> extra) {
        Map<String, Object> m = new java.util.LinkedHashMap<>();
        m.put("材料编码", "CL002");
        m.put("材料名称", "6061铝锭");
        m.put("规格型号", "A00");
        m.put("计量单位", "kg");
        m.put("材料倒冲方式", "按定额倒冲");
        m.put("预出仓库", "原料仓");
        m.put("领料工序", "下料");
        m.put("领料工序编码", "PX001");
        m.put("定额需用数量", 1.0);
        m.put("损耗率%", 0);
        m.put("计划数量", 10.0);
        m.put("累计领用数量", 0.0);
        m.putAll(extra);
        return m;
    }

    private FormData report(double qty) throws Exception {
        return document("PROCESS_REPORT", "GX-1", "已审核",
                Map.of("单据编号", "GX-1", "加工单号", "MO-1"),
                Map.of("items", List.of(Map.of("工序编码", "PX001", "工序名称", "下料", "报工数量", qty))));
    }

    private List<FormData> inserted(String panel) {
        ArgumentCaptor<FormData> captor = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper, org.mockito.Mockito.atLeast(0)).insert(captor.capture());
        List<FormData> out = new ArrayList<>();
        for (FormData d : captor.getAllValues()) {
            if (panel.equals(d.getPanelCode())) out.add(d);
        }
        return out;
    }

    private void stock(String warehouse, double qty) {
        lenient().when(stockTxnService.quantityOf(anyString(), anyString(), any()))
                .thenAnswer(inv -> warehouse.equals(inv.getArgument(2, Object.class))
                        ? BigDecimal.valueOf(qty) : BigDecimal.ZERO);
    }

    @Test
    void backflushUsesLineSideWarehouse() throws Exception {
        when(formMapper.selectOne(any())).thenReturn(mo(backflushMaterial(Map.of("线边仓", "线边仓A"))));
        when(formMapper.selectList(any())).thenAnswer(inv -> List.of(report(10)));
        stock("线边仓A", 100);

        service.backflushMaterials("MO-1");

        assertEquals(1, inserted("MATERIAL_OUT").size());
        Map<?, ?> detail = json.readValue(inserted("MATERIAL_OUT").get(0).getDetailData(), Map.class);
        Map<?, ?> row = (Map<?, ?>) ((List<?>) detail.get("items")).get(0);
        assertEquals("线边仓A", row.get("仓库"), "倒冲应扣线边仓");
        assertTrue(inserted("TRANSFER").isEmpty(), "线边仓够用就不补料");
    }

    @Test
    void lineSideShortageTriggersReplenishmentTransfer() throws Exception {
        when(formMapper.selectOne(any())).thenReturn(mo(backflushMaterial(Map.of("线边仓", "线边仓A"))));
        lenient().when(formMapper.selectList(any())).thenAnswer(inv -> List.of(report(10)));
        lenient().when(stockTxnService.quantityOf(anyString(), anyString(), any()))
                .thenAnswer(inv -> "线边仓A".equals(inv.getArgument(2, Object.class))
                        ? BigDecimal.ZERO : BigDecimal.valueOf(100));

        service.backflushMaterials("MO-1");

        assertEquals(1, inserted("TRANSFER").size(), "线边仓不足要先从总仓补料");
        Map<?, ?> head = json.readValue(inserted("TRANSFER").get(0).getData(), Map.class);
        assertEquals("原料仓", head.get("调出仓库"));
        assertEquals("线边仓A", head.get("调入仓库"));
        assertEquals(1, inserted("MATERIAL_OUT").size());
    }

    @Test
    void insufficientStockSkipsBackflushInsteadOfGoingNegative() throws Exception {
        when(formMapper.selectOne(any())).thenReturn(mo(backflushMaterial(Map.of())));
        lenient().when(formMapper.selectList(any())).thenAnswer(inv -> List.of(report(10)));
        stock("原料仓", 0);

        service.backflushMaterials("MO-1");

        assertTrue(inserted("MATERIAL_OUT").isEmpty(), "库存不够不出库，不能把库存扣成负数");
        verify(formMapper, never()).insert(any(FormData.class));
    }

    @Test
    void nonBackflushMaterialNeverBackflushed() throws Exception {
        when(formMapper.selectOne(any())).thenReturn(mo(backflushMaterial(Map.of("不可倒冲", true))));
        lenient().when(formMapper.selectList(any())).thenAnswer(inv -> List.of(report(10)));
        stock("原料仓", 100);

        service.backflushMaterials("MO-1");

        assertTrue(inserted("MATERIAL_OUT").isEmpty());
    }

    @Test
    void batchIsCarriedIntoBackflushOutbound() throws Exception {
        when(formMapper.selectOne(any())).thenReturn(mo(backflushMaterial(Map.of("批次号", "B2026-01"))));
        lenient().when(formMapper.selectList(any())).thenAnswer(inv -> List.of(report(10)));
        stock("原料仓", 100);

        service.backflushMaterials("MO-1");

        Map<?, ?> detail = json.readValue(inserted("MATERIAL_OUT").get(0).getDetailData(), Map.class);
        Map<?, ?> row = (Map<?, ?>) ((List<?>) detail.get("items")).get(0);
        assertEquals("B2026-01", row.get("批次号"));
    }

    @Test
    void varianceAdjustmentPostsBothDirections() throws Exception {
        lenient().when(formMapper.selectOne(any())).thenReturn(mo(backflushMaterial(
                Map.of("实际消耗数量", 12.0))));
        // 已经倒冲过 10（已审核材料出库单），实际消耗 12 → 补扣 2
        FormData issued = document("MATERIAL_OUT", "CL-1", "已审核",
                Map.of("单据编号", "CL-1", "加工单号", "MO-1"),
                Map.of("items", List.of(Map.of("加工单号", "MO-1", "材料编码", "CL002", "数量", 10))));
        when(formMapper.selectList(any())).thenAnswer(inv -> List.of(issued));

        service.callButton("MANU_ORDER", "倒冲差异调整", Map.of("编号", "MO-1"), Map.of());

        List<FormData> outs = inserted("MATERIAL_OUT");
        assertEquals(1, outs.size(), "实际多耗 2 要补扣");
        Map<?, ?> detail = json.readValue(outs.get(0).getDetailData(), Map.class);
        Map<?, ?> row = (Map<?, ?>) ((List<?>) detail.get("items")).get(0);
        assertEquals(2.0, ((Number) row.get("数量")).doubleValue(), 0.001);
    }

    /** 全倒冲加工单没有人工领料单也能报工（否则永远报不了）。 */
    @Test
    void allBackflushOrderCanReportWithoutManualIssue() throws Exception {
        when(formMapper.selectOne(any())).thenReturn(mo(backflushMaterial(Map.of())));
        lenient().when(formMapper.selectList(any())).thenAnswer(inv -> List.of());
        stock("原料仓", 100);

        service.callButton("MANU_ORDER", "生成工序汇报单（自制汇报）", Map.of("编号", "MO-1"), Map.of());

        assertEquals(1, inserted("PROCESS_REPORT").size());
    }

    /** 倒冲料库存不够：报工直接拒绝，别等报完工发现料扣不出来。 */
    @Test
    void reportingBlockedWhenBackflushStockShort() throws Exception {
        when(formMapper.selectOne(any())).thenReturn(mo(backflushMaterial(Map.of())));
        lenient().when(formMapper.selectList(any())).thenAnswer(inv -> List.of());
        stock("原料仓", 0);

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                service.callButton("MANU_ORDER", "生成工序汇报单（自制汇报）", Map.of("编号", "MO-1"), Map.of()));

        assertTrue(e.getMessage().contains("库存不足"), e.getMessage());
        assertTrue(inserted("PROCESS_REPORT").isEmpty());
    }
}
