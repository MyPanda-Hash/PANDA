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
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 推式生单的明细必须带编码，且材料需求量必须在后端算好。
 *
 * <p>2026-11-05 实测缺陷：
 * <ul>
 *   <li>产成品入库单只写「产品名称」，不写 产品编码/存货编码；材料出库单只写「材料名称」。
 *       结果 stock_txn / stock_balance 的 item_code 全是空串，按编码做报表、对账、追溯全部失效。
 *       查询接口按名称反查补了编码，把问题掩盖在界面上。</li>
 *   <li>加工单材料明细的 需用数量/计划数量 被硬写成 0，因为需求量只由前端表单 applyCalc 计算。
 *       用户在列表页直接点「生成材料出库单」时，数量为 0，而 StockTxnService 会跳过 0 数量行，
 *       于是材料库存一分不扣、也不报错。</li>
 * </ul>
 */
@ExtendWith(MockitoExtension.class)
class PxServiceGenerationCodesTest {

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

    /** 产成品入库单明细必须带存货编码/产品编码，否则库存台账编码为空。 */
    @Test
    void finishInFromMoCarriesItemCodes() throws Exception {
        when(panelMapper.selectOne(any())).thenReturn(config("FINISH_IN"));
        FormData mo = document("MANU_ORDER", "MO-001", "已审核",
                Map.of("单据编号", "MO-001", "生产车间", "熔铸车间"),
                Map.of("products", List.of(Map.of(
                        "产品编码", "CP001", "产品名称", "铝棒 Φ80", "规格型号", "Φ80×3000",
                        "生产单位", "件", "数量", 100))));
        when(formMapper.selectOne(any())).thenReturn(mo);
        when(formMapper.selectCount(any())).thenReturn(0L);

        service.callButton("MANU_ORDER", "生成产成品入库单", Map.of("编号", "MO-001"), Map.of());

        FormData target = captureInserted();
        Map<?, ?> row = firstItem(target);
        assertEquals("CP001", row.get("存货编码"), "产成品入库单必须写存货编码");
        assertEquals("CP001", row.get("产品编码"), "产成品入库单必须写产品编码");
        assertEquals("铝棒 Φ80", row.get("存货名称"), "产成品入库单必须写存货名称");
    }

    /** 材料出库单明细必须带材料编码，否则出库流水无法按编码入账。 */
    @Test
    void materialOutCarriesMaterialCodes() throws Exception {
        when(panelMapper.selectOne(any())).thenReturn(config("MATERIAL_OUT"));
        FormData mo = document("MANU_ORDER", "MO-002", "已审核",
                Map.of("单据编号", "MO-002", "生产车间", "熔铸车间"),
                Map.of("materials", List.of(Map.of(
                        "材料编码", "CL002", "材料名称", "6061铝锭", "规格型号", "A00",
                        "计量单位", "kg", "定额需用数量", 1.05, "定额生产数量", 1,
                        "计划数量", 105, "需用数量", 105, "预出仓库", "原料仓"))));
        when(formMapper.selectOne(any())).thenReturn(mo);
        when(formMapper.selectCount(any())).thenReturn(0L);

        service.callButton("MANU_ORDER", "生成材料出库单", Map.of("编号", "MO-002"), Map.of());

        FormData target = captureInserted();
        Map<?, ?> row = firstItem(target);
        assertEquals("CL002", row.get("材料编码"), "材料出库单必须写材料编码");
        assertEquals("6061铝锭", row.get("材料名称"));
    }

    /**
     * 销售订单生单出加工单时，材料需求量必须由后端算好：
     * 需用数量 = 定额需用数量 × 产品数量 ÷ 定额生产数量，计划数量 = 需用数量 + 损耗数量。
     */
    @Test
    void moFromSoComputesMaterialRequirement() throws Exception {
        when(panelMapper.selectOne(any())).thenReturn(config("MANU_ORDER"));
        FormData so = document("SO_ORDER", "SO-002", "已审核",
                Map.of("单据编号", "SO-002", "客户", "华东铝业", "客户编码", "KH001", "生产车间", "熔铸车间"),
                Map.of("items", List.of(Map.of(
                        "存货编码", "CP001", "存货名称", "铝棒 Φ80", "规格型号", "Φ80×3000",
                        "数量", 100, "销售单位", "件"))));
        when(formMapper.selectOne(any())).thenReturn(so);
        when(formMapper.selectCount(any())).thenReturn(0L);

        // BOM：父件 CP001，子件 CL002 每生产 1 个成品需 1.05kg
        FormData bom = document("BOM", "BOM-001", "启用", Map.of(),
                Map.of("children", List.of(Map.of(
                        "父件编码", "CP001", "子件编码", "CL002", "子件名称", "6061铝锭",
                        "规格型号", "A00", "子件计量单位", "kg", "定额数量", 1.05, "损耗率%", 0))));
        when(formMapper.selectList(any())).thenReturn(List.of(bom));

        service.callButton("SO_ORDER", "生成生产加工单", Map.of("编号", "SO-002"), Map.of());

        FormData target = captureInserted();
        Map<?, ?> detail = readJson(target.getDetailData());
        List<?> materials = (List<?>) detail.get("materials");
        assertNotNull(materials, "加工单必须带材料明细");
        assertEquals(1, materials.size());
        Map<?, ?> row = (Map<?, ?>) materials.get(0);

        assertEquals("CL002", row.get("材料编码"), "材料明细必须带材料编码");
        assertEquals(105.0, ((Number) row.get("需用数量")).doubleValue(), 0.001,
                "需用数量应为 定额1.05 × 产品数量100 ÷ 定额生产数量1 = 105");
        assertEquals(105.0, ((Number) row.get("计划数量")).doubleValue(), 0.001,
                "计划数量 = 需用数量 + 损耗数量");
    }

    /** 加工单材料明细也要带材料编码，供下游领料/出库按编码对账。 */
    @Test
    void moFromSoCarriesMaterialCode() throws Exception {
        when(panelMapper.selectOne(any())).thenReturn(config("MANU_ORDER"));
        FormData so = document("SO_ORDER", "SO-003", "已审核",
                Map.of("单据编号", "SO-003", "客户", "华东铝业"),
                Map.of("items", List.of(Map.of("存货编码", "CP001", "存货名称", "铝棒 Φ80", "数量", 10))));
        when(formMapper.selectOne(any())).thenReturn(so);
        when(formMapper.selectCount(any())).thenReturn(0L);
        FormData bom = document("BOM", "BOM-001", "启用", Map.of(),
                Map.of("children", List.of(Map.of("父件编码", "CP001", "子件编码", "CL004",
                        "子件名称", "切削液", "定额数量", 0.02, "子件计量单位", "升"))));
        when(formMapper.selectList(any())).thenReturn(List.of(bom));

        service.callButton("SO_ORDER", "生成生产加工单", Map.of("编号", "SO-003"), Map.of());

        FormData target = captureInserted();
        Map<?, ?> detail = readJson(target.getDetailData());
        Map<?, ?> row = (Map<?, ?>) ((List<?>) detail.get("materials")).get(0);
        assertEquals("CL004", row.get("材料编码"));
        assertEquals(0.2, ((Number) row.get("需用数量")).doubleValue(), 0.001);
    }

    private FormData captureInserted() {
        ArgumentCaptor<FormData> captor = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).insert(captor.capture());
        return captor.getValue();
    }

    private Map<?, ?> readJson(String text) throws Exception {
        return json.readValue(text, Map.class);
    }

    private Map<?, ?> firstItem(FormData target) throws Exception {
        Map<?, ?> detail = readJson(target.getDetailData());
        List<?> items = (List<?>) detail.get("items");
        assertNotNull(items, "生单明细不能为空");
        assertTrue(items.size() >= 1);
        return (Map<?, ?>) items.get(0);
    }
}