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
 * 加工单 → 领料申请单：子件行必须带上「属于哪个产成品」。
 *
 * <p>修复前：产品编码取材料的「子件BOM」字段（老单为空），产品名称写死空串，
 * 生成出来的领料单整列产品编码/名称都是空的，无法判断这条材料是给哪个产品领的。
 */
@ExtendWith(MockitoExtension.class)
class PxServiceMaterialReqTest {

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

    private PanelConfig config(String code) {
        PanelConfig cfg = new PanelConfig();
        cfg.setPanelCode(code);
        cfg.setPanelName(code);
        cfg.setConfig("{\"metadata\":{\"panelCode\":\"" + code
                + "\",\"autoCodeField\":\"单据编号\",\"panelButtons\":[],\"buttonGroups\":[],\"panelPageDto\":{}},"
                + "\"dataSchema\":{\"fields\":[]},\"detail\":{\"tabs\":[]}}");
        return cfg;
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

    /** 加工单：一个产品，材料带「子件BOM」= 产品编码。 */
    private FormData moWithChildBom() throws Exception {
        return document("MANU_ORDER", "MO-1", "已审核",
                Map.of("单据编号", "MO-1", "生产车间", "熔铸车间", "仓库", "原料仓"),
                Map.of(
                        "products", List.of(Map.of("产品编码", "CP001", "产品名称", "铝棒 Φ80",
                                "生产单位", "件", "数量", 200)),
                        "materials", List.of(Map.of(
                                "材料编码", "CL002", "材料名称", "6061铝锭", "规格型号", "A00",
                                "子件BOM", "CP001", "预出仓库", "原料仓", "领料工序", "下料",
                                "计量单位", "kg", "计划数量", 330.75, "累计领用数量", 300.0)),
                        "processes", List.of(Map.of("工序编码", "PX001", "工序名称", "下料", "计划数量", 200))));
    }

    /** 老加工单：材料没有「子件BOM」，只能按物料清单的父件反查产品。 */
    private FormData moWithoutChildBom() throws Exception {
        return document("MANU_ORDER", "MO-2", "已审核",
                Map.of("单据编号", "MO-2", "生产车间", "熔铸车间"),
                Map.of(
                        "products", List.of(
                                Map.of("产品编码", "CP001", "产品名称", "铝棒 Φ80", "数量", 200),
                                Map.of("产品编码", "CP002", "产品名称", "铝板 6061", "数量", 100)),
                        "materials", List.of(Map.of(
                                "材料编码", "CL002", "材料名称", "6061铝锭", "预出仓库", "原料仓",
                                "领料工序", "下料", "计量单位", "kg", "计划数量", 100, "累计领用数量", 0)),
                        "processes", List.of(Map.of("工序编码", "PX001", "工序名称", "下料", "计划数量", 300))));
    }

    private FormData bomDoc() throws Exception {
        return document("BOM", "BOM-002", "已审核",
                Map.of("单据编号", "BOM-002"),
                Map.of("children", List.of(Map.of("父件编码", "CP002", "父件名称", "铝板 6061",
                        "子件编码", "CL002", "子件名称", "6061铝锭", "定额数量", 1.08, "生产数量", 1))));
    }

    private FormData insertedReq() {
        ArgumentCaptor<FormData> captor = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper, org.mockito.Mockito.atLeastOnce()).insert(captor.capture());
        for (FormData row : captor.getAllValues()) {
            if ("MATERIAL_REQ".equals(row.getPanelCode())) return row;
        }
        throw new AssertionError("没有生成领料申请单");
    }

    @Test
    void materialRequestCarriesProductCodeAndName() throws Exception {
        when(panelMapper.selectOne(any())).thenReturn(config("MATERIAL_REQ"));
        when(formMapper.selectOne(any())).thenReturn(moWithChildBom());
        when(formMapper.selectList(any())).thenReturn(List.of());

        service.callButton("MANU_ORDER", "生成领料申请单", Map.of("编号", "MO-1"), Map.of());

        Map<?, ?> detail = json.readValue(insertedReq().getDetailData(), Map.class);
        Map<?, ?> row = (Map<?, ?>) ((List<?>) detail.get("items")).get(0);
        assertEquals("CP001", row.get("产品编码"));
        assertEquals("铝棒 Φ80", row.get("产品名称"), "产品名称不能是空串");
        assertEquals("CL002", row.get("材料编码"));
        assertEquals("6061铝锭", row.get("材料名称"));
        assertEquals(30.75, ((Number) row.get("数量")).doubleValue(), 0.001, "只领剩余量");
    }

    @Test
    void materialRequestInfersProductFromBomWhenChildBomMissing() throws Exception {
        when(panelMapper.selectOne(any())).thenReturn(config("MATERIAL_REQ"));
        when(formMapper.selectOne(any())).thenReturn(moWithoutChildBom());
        when(formMapper.selectList(any())).thenReturn(List.of(bomDoc()));

        service.callButton("MANU_ORDER", "生成领料申请单", Map.of("编号", "MO-2"), Map.of());

        Map<?, ?> detail = json.readValue(insertedReq().getDetailData(), Map.class);
        Map<?, ?> row = (Map<?, ?>) ((List<?>) detail.get("items")).get(0);
        assertEquals("CP002", row.get("产品编码"), "子件BOM缺失时按物料清单的父件反查");
        assertEquals("铝板 6061", row.get("产品名称"));
    }

    /** 倒冲料不进领料申请单：报工时自动扣账，手工再领一次会重复扣料。 */
    @Test
    void backflushMaterialsAreExcludedFromRequest() throws Exception {
        when(panelMapper.selectOne(any())).thenReturn(config("MATERIAL_REQ"));
        FormData mo = document("MANU_ORDER", "MO-3", "已审核",
                Map.of("单据编号", "MO-3", "生产车间", "熔铸车间"),
                Map.of("products", List.of(Map.of("产品编码", "CP001", "产品名称", "铝棒 Φ80", "数量", 200)),
                        "materials", List.of(
                                Map.of("材料编码", "CL002", "材料名称", "6061铝锭", "子件BOM", "CP001",
                                        "材料倒冲方式", "按定额倒冲", "预出仓库", "原料仓",
                                        "计量单位", "kg", "计划数量", 330.75, "累计领用数量", 0),
                                Map.of("材料编码", "CL004", "材料名称", "切削液", "子件BOM", "CP001",
                                        "材料倒冲方式", "不倒冲", "预出仓库", "辅料仓",
                                        "计量单位", "升", "计划数量", 6.12, "累计领用数量", 0)),
                        "processes", List.of(Map.of("工序编码", "PX001", "工序名称", "下料", "计划数量", 200))));
        when(formMapper.selectOne(any())).thenReturn(mo);
        when(formMapper.selectList(any())).thenReturn(List.of());

        service.callButton("MANU_ORDER", "生成领料申请单", Map.of("编号", "MO-3"), Map.of());

        Map<?, ?> detail = json.readValue(insertedReq().getDetailData(), Map.class);
        List<?> rows = (List<?>) detail.get("items");
        assertEquals(1, rows.size(), "倒冲料不该出现在领料单里");
        Map<?, ?> row = (Map<?, ?>) rows.get(0);
        assertEquals("CL004", row.get("材料编码"));
        assertEquals("否", row.get("倒冲料"));
    }

    /** 全部是倒冲料：直接告诉用户不需要领料单。 */
    @Test
    void allBackflushMaterialsNeedNoRequest() throws Exception {
        // 全倒冲时在生单前就抛，面板配置根本不会被读
        org.mockito.Mockito.lenient().when(panelMapper.selectOne(any())).thenReturn(config("MATERIAL_REQ"));
        FormData mo = document("MANU_ORDER", "MO-4", "已审核",
                Map.of("单据编号", "MO-4", "生产车间", "熔铸车间"),
                Map.of("products", List.of(Map.of("产品编码", "CP001", "产品名称", "铝棒 Φ80", "数量", 200)),
                        "materials", List.of(Map.of("材料编码", "CL002", "材料名称", "6061铝锭",
                                "子件BOM", "CP001", "材料倒冲方式", "按定额倒冲",
                                "计量单位", "kg", "计划数量", 330.75, "累计领用数量", 0)),
                        "processes", List.of(Map.of("工序编码", "PX001", "工序名称", "下料", "计划数量", 200))));
        when(formMapper.selectOne(any())).thenReturn(mo);
        org.mockito.Mockito.lenient().when(formMapper.selectList(any())).thenReturn(List.of());

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                service.callButton("MANU_ORDER", "生成领料申请单", Map.of("编号", "MO-4"), Map.of()));

        assertTrue(e.getMessage().contains("倒冲"), e.getMessage());
    }

    /** 一张领料申请单只允许生成一张材料出库单：已经生成过（哪怕还是草稿）就不许再来一张。 */
    @Test
    void materialOutCannotBeGeneratedTwice() throws Exception {
        org.mockito.Mockito.lenient().when(panelMapper.selectOne(any())).thenReturn(config("MATERIAL_OUT"));
        FormData req = document("MATERIAL_REQ", "LL-1", "已审核",
                Map.of("单据编号", "LL-1", "生产车间", "熔铸车间", "加工单号", "MO-1"),
                Map.of("items", List.of(Map.of("加工单号", "MO-1", "材料编码", "CL004",
                        "材料名称", "切削液", "数量", 0.62, "仓库", "辅料仓"))));
        FormData existing = document("MATERIAL_OUT", "CL-1", "草稿",
                Map.of("单据编号", "CL-1", "来源单据", "MATERIAL_REQ", "来源单号", "LL-1"),
                Map.of("items", List.of(Map.of("材料编码", "CL004", "数量", 0.62))));
        when(formMapper.selectOne(any())).thenReturn(req);
        when(formMapper.selectList(any())).thenReturn(List.of(existing));

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                service.callButton("MATERIAL_REQ", "生成材料出库单", Map.of("编号", "LL-1"), Map.of()));

        assertTrue(e.getMessage().contains("已生成材料出库单"), e.getMessage());
        verify(formMapper, org.mockito.Mockito.never()).insert(any(FormData.class));
    }

    /** 领料按批次：材料行带上它所属工序的排产批次号，材料出库才好按批次控量。 */
    @Test
    void materialRequestCarriesDispatchNoOfItsProcess() throws Exception {
        when(panelMapper.selectOne(any())).thenReturn(config("MATERIAL_REQ"));
        FormData mo = document("MANU_ORDER", "MO-5", "已审核",
                Map.of("单据编号", "MO-5", "生产车间", "熔铸车间"),
                Map.of("products", List.of(Map.of("产品编码", "CP001", "产品名称", "铝棒 Φ80", "数量", 10)),
                        "materials", List.of(Map.of("材料编码", "CL001", "材料名称", "45#圆钢",
                                "子件BOM", "CP001", "材料倒冲方式", "不倒冲", "领料工序编码", "PX001",
                                "预出仓库", "原料仓", "计量单位", "kg",
                                "计划数量", 6.18, "累计领用数量", 0)),
                        "processes", List.of(Map.of("工序编码", "PX001", "工序名称", "下料", "计划数量", 10))));
        FormData batch = document("DISPATCH", "PG-1", "已审核",
                Map.of("单据编号", "PG-1", "加工单号", "MO-5"),
                Map.of("items", List.of(Map.of("工序编码", "PX001", "工序名称", "下料", "派工数量", 10))));
        when(formMapper.selectOne(any())).thenReturn(mo);
        when(formMapper.selectList(any())).thenReturn(List.of(batch));

        service.callButton("MANU_ORDER", "生成领料申请单", Map.of("编号", "MO-5"), Map.of());

        Map<?, ?> detail = json.readValue(insertedReq().getDetailData(), Map.class);
        Map<?, ?> row = (Map<?, ?>) ((List<?>) detail.get("items")).get(0);
        assertEquals("PG-1", row.get("排产单号"));
    }
}
