package com.mes.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.mapper.FormApprovalMapper;
import com.mes.mapper.FormDataMapper;
import com.mes.mapper.PanelConfigMapper;
import com.mes.panel.PanelActionRegistry;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.lenient;

/**
 * 加工单产成品明细的三条硬约束：
 * <ol>
 *   <li>产成品行只能是「产成品 / 半成品」，原材料、辅助材料、包装物要进材料明细；</li>
 *   <li>「适用BOM」按产品自身解析，不能写死 BOM-001；</li>
 *   <li>材料计划数量 = 定额数量 ÷ 生产数量 × 产品数量 × (1+损耗率)。</li>
 * </ol>
 */
@ExtendWith(MockitoExtension.class)
class PxServiceManuProductsTest {

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

    private FormData inv(String category, String... codes) throws Exception {
        FormData d = new FormData();
        d.setPanelCode("INV");
        d.setFormNo("存货");
        d.setStatus("启用");
        List<Map<String, Object>> items = new ArrayList<>();
        for (String code : codes) {
            items.add(new HashMap<>(Map.of("存货编码", code, "存货名称", code + "名称", "所属类别", category)));
        }
        d.setData(json.writeValueAsString(new HashMap<>(Map.of("备注", "存货"))));
        d.setDetailData(json.writeValueAsString(Map.of("items", items)));
        d.setCreateTime(LocalDateTime.now());
        return d;
    }

    private FormData bom(String no, String parent, String route, double baseQty, double quota, double loss) throws Exception {
        FormData d = new FormData();
        d.setPanelCode("BOM");
        d.setFormNo(no);
        d.setStatus("已审核");
        d.setData(json.writeValueAsString(new HashMap<>(Map.of("备注", "物料清单"))));
        d.setDetailData(json.writeValueAsString(Map.of("children", List.of(new HashMap<>(Map.of(
                "父件编码", parent, "子件编码", "CL001", "子件名称", "45#圆钢", "默认BOM", true,
                "生产数量", baseQty, "定额数量", quota, "损耗率%", loss))))));
        d.setCreateTime(LocalDateTime.now());
        return d;
    }

    private void stubDocs(FormData... docs) {
        lenient().when(formMapper.selectList(any())).thenReturn(List.of(docs));
    }

    @Test
    void defaultBomIsResolvedFromProduct() throws Exception {
        stubDocs(bom("BOM-003", "CP003", "", 1, 1.05, 0), bom("BOM-004", "CP004", "", 1, 2.2, 0));
        assertEquals("BOM-004", service.defaultBomNoOf("CP004"), "按产品自己的父件关系解析，不是恒取第一张");
        assertEquals("BOM-003", service.defaultBomNoOf("CP003"));
        assertEquals("", service.defaultBomNoOf("CP999"), "没有 BOM 就返回空，不要瞎填");
    }

    @Test
    void rawMaterialCannotBeAProductRow() throws Exception {
        stubDocs(inv("辅助材料", "CL004"));
        IllegalStateException e = assertThrows(IllegalStateException.class,
                () -> service.assertProductCategories(List.of(new HashMap<>(Map.of(
                        "产品编码", "CL004", "产品名称", "切削液", "数量", 1)))));
        assertTrue(e.getMessage().contains("切削液") || e.getMessage().contains("CL004"), "要点名是哪个物料填错了");
        assertTrue(e.getMessage().contains("材料明细"), "要告诉用户该填到材料明细");
    }

    @Test
    void finishedAndSemiFinishedPass() throws Exception {
        stubDocs(inv("产成品", "CP001"), inv("半成品", "BC001"));
        service.assertProductCategories(List.of(
                new HashMap<>(Map.of("产品编码", "CP001", "数量", 1)),
                new HashMap<>(Map.of("产品编码", "BC001", "数量", 1))));
        // 不抛异常即通过
    }

    @Test
    void materialQtyDividesByBomBaseQuantity() throws Exception {
        // 基准 10 件 / 定额 25kg / 损耗 10%，产品 20 件 → 25/10 × 20 × 1.1 = 55
        stubDocs(bom("BOM-6", "BC001", "", 10, 25, 10), inv("半成品", "BC001"));
        List<Map<String, Object>> mats = service.materialsForProducts(
                List.of(new HashMap<>(Map.of("产品编码", "BC001", "数量", 20))));
        assertEquals(1, mats.size());
        assertEquals(55.0, Double.parseDouble(String.valueOf(mats.get(0).get("计划数量"))), 0.001);
    }

    @Test
    void duplicatedMaterialIsMergedAndCarriesOwningProduct() throws Exception {
        stubDocs(bom("BOM-1", "CP001", "", 1, 2, 0), bom("BOM-2", "CP002", "", 1, 3, 0),
                inv("产成品", "CP001", "CP002"));
        List<Map<String, Object>> mats = service.materialsForProducts(List.of(
                new HashMap<>(Map.of("产品编码", "CP001", "数量", 2)),
                new HashMap<>(Map.of("产品编码", "CP002", "数量", 1))));
        assertEquals(1, mats.size(), "同一材料只留一行");
        assertEquals(7.0, Double.parseDouble(String.valueOf(mats.get(0).get("计划数量"))), 0.001,
                "2×2 + 1×3 = 7");
        assertEquals("CP001", mats.get(0).get("子件BOM"), "合并行要记着属于哪个产品");
    }

    private void assertNull(Object v) {
        org.junit.jupiter.api.Assertions.assertNull(v);
    }

    @Test
    void materialQuantitiesFollowProductQuantityOnSave() throws Exception {
        // 产品从 2 件改成 5 件：已有材料行的数量要跟着变，人工加的行不能被删
        stubDocs(bom("BOM-1", "CP001", "", 1, 2, 0), inv("产成品", "CP001"));
        List<Map<String, Object>> materials = new ArrayList<>();
        materials.add(new HashMap<>(Map.of("材料编码", "CL001", "材料名称", "45#圆钢", "计划数量", 4)));
        materials.add(new HashMap<>(Map.of("材料编码", "CL099", "材料名称", "人工加的料", "计划数量", 9)));
        Map<String, Object> detail = new HashMap<>();
        detail.put("products", List.of(new HashMap<>(Map.of("产品编码", "CP001", "产品名称", "CP001名称", "数量", 5))));
        detail.put("materials", materials);

        service.normalizeManuProducts(detail);

        @SuppressWarnings("unchecked")
        List<Map<String, Object>> out = (List<Map<String, Object>>) detail.get("materials");
        Map<String, Object> cl001 = out.stream().filter(m -> "CL001".equals(m.get("材料编码"))).findFirst().orElseThrow();
        assertEquals(10.0, Double.parseDouble(String.valueOf(cl001.get("计划数量"))), 0.001,
                "5 件 × 定额 2 = 10");
        assertTrue(out.stream().anyMatch(m -> "CL099".equals(m.get("材料编码"))), "人工加的材料行要保留");
    }

    @Test
    void sameMaterialOnTwoProcessesStaysOnTwoRows() throws Exception {
        // 允许多行：同一材料按工序拆开，各行额度独立（用户 2026-11-09 确认）
        FormData bomDoc = bom("BOM-9", "CP001", "", 1, 2, 0);
        Map<String, Object> child = new HashMap<>(Map.of(
                "父件编码", "CP001", "子件编码", "CL001", "子件名称", "45#圆钢", "默认BOM", true,
                "生产数量", 1, "定额数量", 2, "损耗率%", 0, "领料工序", "下料"));
        Map<String, Object> child2 = new HashMap<>(child);
        child2.put("领料工序", "车削");
        bomDoc.setDetailData(json.writeValueAsString(Map.of("children", List.of(child, child2))));
        stubDocs(bomDoc, inv("产成品", "CP001"));

        List<Map<String, Object>> mats = service.materialsForProducts(
                List.of(new HashMap<>(Map.of("产品编码", "CP001", "数量", 1))));

        assertEquals(2, mats.size(), "同一材料挂两道工序要保留两行");
        assertEquals("下料", mats.get(0).get("领料工序"));
        assertEquals("车削", mats.get(1).get("领料工序"));
        assertEquals(2.0, Double.parseDouble(String.valueOf(mats.get(0).get("计划数量"))), 0.001);
    }

    @Test
    void materialCarriesProcessFromBom() throws Exception {
        FormData bomDoc = bom("BOM-8", "CP001", "", 1, 3, 0);
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> children = (List<Map<String, Object>>) json.readValue(
                bomDoc.getDetailData(), Map.class).get("children");
        children.get(0).put("领料工序", "热处理");
        bomDoc.setDetailData(json.writeValueAsString(Map.of("children", children)));
        stubDocs(bomDoc, inv("产成品", "CP001"));

        List<Map<String, Object>> mats = service.materialsForProducts(
                List.of(new HashMap<>(Map.of("产品编码", "CP001", "数量", 1))));
        assertEquals("热处理", mats.get(0).get("领料工序"), "领料工序要按 BOM 带出，不能永远默认下料");
    }

    @Test
    void materialCarriesProcessCodeFromBom() throws Exception {
        FormData bomDoc = bom("BOM-7", "CP001", "", 1, 3, 0);
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> children = (List<Map<String, Object>>) json.readValue(
                bomDoc.getDetailData(), Map.class).get("children");
        children.get(0).put("领料工序", "车削");
        children.get(0).put("领料工序编码", "PX002");
        bomDoc.setDetailData(json.writeValueAsString(Map.of("children", children)));
        stubDocs(bomDoc, inv("产成品", "CP001"));

        List<Map<String, Object>> mats = service.materialsForProducts(
                List.of(new HashMap<>(Map.of("产品编码", "CP001", "数量", 1))));
        assertEquals("PX002", mats.get(0).get("领料工序编码"), "工序编码要跟着带到加工单，改名也不丢");
        assertEquals("车削", mats.get(0).get("领料工序"));
    }
}
