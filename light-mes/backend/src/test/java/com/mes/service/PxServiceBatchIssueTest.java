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
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;

/**
 * 按排产批次领料。
 *
 * <p>两条上限：
 * <ol>
 *   <li>批次分摊上限 = 整单需用量 ×（批次数量 / 加工单数量）——挂了批次就只能领这么多；</li>
 *   <li>整单上限 = 整单需用量——所有来源合计不得超过。</li>
 * </ol>
 * 共用物料可以不挂批次（整单领），但仍受整单上限约束，并记录领料工序与领料人。
 */
@ExtendWith(MockitoExtension.class)
class PxServiceBatchIssueTest {

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

    private FormData doc(String panel, String no, String status, Map<String, Object> head, Map<String, Object> detail) throws Exception {
        FormData d = new FormData();
        d.setId(1L);
        d.setPanelCode(panel);
        d.setFormNo(no);
        d.setStatus(status);
        d.setData(json.writeValueAsString(head));
        d.setDetailData(json.writeValueAsString(detail));
        d.setCreateTime(LocalDateTime.now());
        return d;
    }

    /** 加工单 100 件，材料 CL001 需用 200kg（单件 2kg）。 */
    private FormData mo() throws Exception {
        return doc("MANU_ORDER", "MO-1", "已审核",
                Map.of("单据编号", "MO-1"),
                Map.of(
                        "products", List.of(Map.of("产品编码", "CP001", "数量", 100)),
                        "processes", List.of(),
                        "materials", List.of(Map.of(
                                "材料编码", "CL001", "材料名称", "6063铝棒",
                                "计划数量", 200, "需用数量", 200))));
    }

    /** 批次 40 件 → 该批材料分摊 = 200 × 40/100 = 80。 */
    private FormData batch(double qty) throws Exception {
        return doc("DISPATCH", "PG-1", "已审核",
                Map.of("单据编号", "PG-1", "加工单号", "MO-1"),
                Map.of("items", List.of(Map.of("工序编码", "PX001", "派工数量", qty))));
    }

    private FormData issue(String dispatchNo, double qty, String status) throws Exception {
        return doc("MATERIAL_OUT", "CL-1", status,
                Map.of("单据编号", "CL-1"),
                Map.of("items", List.of(Map.of(
                        "加工单号", "MO-1", "材料编码", "CL001", "排产单号", dispatchNo, "数量", qty))));
    }

    @Test
    void batchShareIsProportionalToBatchQuantity() throws Exception {
        when(formMapper.selectOne(any())).thenReturn(mo(), batch(40));
        when(formMapper.selectList(any())).thenReturn(List.of());

        // 批次 40/100 → 分摊 80，领 80 可以
        service.validateBatchIssue("MO-1", "PG-1", "CL001", 80);
    }

    @Test
    void overBatchShareIsRejected() throws Exception {
        when(formMapper.selectOne(any())).thenReturn(mo(), batch(40));
        when(formMapper.selectList(any())).thenReturn(List.of());

        IllegalStateException e = assertThrows(IllegalStateException.class,
                () -> service.validateBatchIssue("MO-1", "PG-1", "CL001", 100));
        assertTrue(e.getMessage().contains("批次"), e.getMessage());
        assertTrue(e.getMessage().contains("80"), "要说明该批次能领多少：" + e.getMessage());
    }

    @Test
    void cumulativeBatchIssueCountsApprovedOnly() throws Exception {
        when(formMapper.selectOne(any())).thenReturn(mo(), batch(40));
        // 该批次已领 50（已审核），再领 40 → 合计 90 > 80
        when(formMapper.selectList(any())).thenReturn(List.of(issue("PG-1", 50, "已审核")));

        IllegalStateException e = assertThrows(IllegalStateException.class,
                () -> service.validateBatchIssue("MO-1", "PG-1", "CL001", 40, "CL-NEW"));
        assertTrue(e.getMessage().contains("80"), e.getMessage());
    }

    @Test
    void sharedMaterialCanBeIssuedWithoutBatch() throws Exception {
        when(formMapper.selectOne(any())).thenReturn(mo());
        when(formMapper.selectList(any())).thenReturn(List.of());
        // 不挂批次（共用料整单领），受整单上限 200 约束
        service.validateMaterialIssue(
                List.of(Map.of("加工单号", "MO-1", "材料编码", "CL001", "材料名称", "6063铝棒",
                        "数量", 150, "领料工序", "下料", "领料人", "王强")), "CL-NEW");
    }

    @Test
    void sharedIssueStillRespectsOrderTotal() throws Exception {
        when(formMapper.selectOne(any())).thenReturn(mo());
        when(formMapper.selectList(any())).thenReturn(List.of(issue("", 180, "已审核")));

        IllegalStateException e = assertThrows(IllegalStateException.class,
                () -> service.validateMaterialIssue(
                        List.of(Map.of("加工单号", "MO-1", "材料编码", "CL001", "数量", 50)), "CL-NEW"));
        assertTrue(e.getMessage().contains("超出"), e.getMessage());
    }

    /** 材料只挂在它的领料工序批次上：三道路序不会各算一遍同一种材料。 */
    @Test
    void materialOnlyAppearsOnItsOwningProcessBatch() throws Exception {
        FormData mo = doc("MANU_ORDER", "MO-9", "已审核",
                Map.of("单据编号", "MO-9"),
                Map.of(
                        "products", List.of(Map.of("产品编码", "CP001", "数量", 100)),
                        "processes", List.of(
                                Map.of("工序编码", "PX001", "工序名称", "下料"),
                                Map.of("工序编码", "PX002", "工序名称", "机加工")),
                        "materials", List.of(Map.of(
                                "材料编码", "CL001", "材料名称", "圆钢", "领料工序", "下料",
                                "计划数量", 200))));
        FormData b1 = doc("DISPATCH", "PG-A", "已审核", Map.of("单据编号", "PG-A", "加工单号", "MO-9"),
                Map.of("items", List.of(Map.of("工序编码", "PX001", "工序名称", "下料", "派工数量", 100))));
        FormData b2 = doc("DISPATCH", "PG-B", "已审核", Map.of("单据编号", "PG-B", "加工单号", "MO-9"),
                Map.of("items", List.of(Map.of("工序编码", "PX002", "工序名称", "机加工", "派工数量", 100))));

        when(formMapper.selectOne(any())).thenReturn(mo);
        when(formMapper.selectList(any())).thenReturn(List.of(), List.of(b2, b1));

        List<Map<String, Object>> rows = service.materialIssueBoard("MO-9");
        assertEquals(1, rows.size(), "只有「下料」批次会出现该材料");
        assertEquals("PG-A", rows.get(0).get("排产单号"));
        assertEquals(200.0, ((Number) rows.get(0).get("批次需用量")).doubleValue(), 0.001);
    }

    @Test
    void boardShowsMaterialDemandPerBatch() throws Exception {
        when(formMapper.selectOne(any())).thenReturn(mo(), batch(40));
        when(formMapper.selectList(any())).thenReturn(
                List.of(issue("PG-1", 30, "已审核")),   // 先查已审核领料
                List.of(batch(40)));                    // 再查排产批次

        List<Map<String, Object>> rows = service.materialIssueBoard("MO-1");
        assertEquals(1, rows.size());
        Map<String, Object> r = rows.get(0);
        assertEquals("PG-1", r.get("排产单号"));
        assertEquals("CL001", r.get("材料编码"));
        assertEquals(80.0, ((Number) r.get("批次需用量")).doubleValue(), 0.001);
        assertEquals(30.0, ((Number) r.get("已领数量")).doubleValue(), 0.001);
        assertEquals(50.0, ((Number) r.get("剩余可领")).doubleValue(), 0.001);
    }
}
