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

import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;

/**
 * 领料超领拦截。
 *
 * <p>规则：同一加工单同一材料的「已审核领用量 + 本次领用量」不得超过加工单的需用量（计划数量）。
 * 重存单据时要把自己排除掉，否则自己的老版本会被重复计入。
 */
@ExtendWith(MockitoExtension.class)
class PxServiceMaterialIssueTest {

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

    private FormData mo() throws Exception {
        return doc("MANU_ORDER", "MO-1", "已审核", Map.of("单据编号", "MO-1"),
                Map.of(
                        "products", List.of(Map.of("产品编码", "CP001", "数量", 10)),
                        "processes", List.of(),
                        "materials", List.of(
                                Map.of("材料编码", "CL001", "材料名称", "6063铝棒", "计划数量", 24.2),
                                Map.of("材料编码", "CL005", "材料名称", "包装木箱", "计划数量", 0.4))));
    }

    private FormData out(String no, String status, String matCode, double qty) throws Exception {
        return doc("MATERIAL_OUT", no, status,
                Map.of("单据编号", no),
                Map.of("items", List.of(Map.of(
                        "加工单号", "MO-1", "材料编码", matCode, "材料名称", "6063铝棒", "数量", qty))));
    }

    @Test
    void allowsIssueWithinPlan() throws Exception {
        when(formMapper.selectOne(any())).thenReturn(mo());
        when(formMapper.selectList(any())).thenReturn(List.of(out("CL-1", "已审核", "CL001", 10)));

        // 已领 10 + 本次 14.2 = 24.2，刚好等于计划，允许
        service.validateMaterialIssue(
                List.of(Map.of("加工单号", "MO-1", "材料编码", "CL001", "材料名称", "6063铝棒", "数量", 14.2)),
                "CL-NEW");
    }

    @Test
    void rejectsWhenOverPlan() throws Exception {
        when(formMapper.selectOne(any())).thenReturn(mo());
        when(formMapper.selectList(any())).thenReturn(List.of(out("CL-1", "已审核", "CL001", 20)));

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                service.validateMaterialIssue(
                        List.of(Map.of("加工单号", "MO-1", "材料编码", "CL001", "材料名称", "6063铝棒", "数量", 10)),
                        "CL-NEW"));
        assertTrue(e.getMessage().contains("超出"), e.getMessage());
        assertTrue(e.getMessage().contains("24.2"), "要说清计划量：" + e.getMessage());
        assertTrue(e.getMessage().contains("4.2"), "要说清剩余可领：" + e.getMessage());
    }

    @Test
    void ignoresDraftAndCancelledDocs() throws Exception {
        when(formMapper.selectOne(any())).thenReturn(mo());
        when(formMapper.selectList(any())).thenReturn(List.of(
                out("CL-1", "草稿", "CL001", 20),
                out("CL-2", "已中止", "CL001", 20)));

        // 草稿与中止都不占额度，本次 24.2 应放行
        service.validateMaterialIssue(
                List.of(Map.of("加工单号", "MO-1", "材料编码", "CL001", "数量", 24.2)), "CL-NEW");
    }

    @Test
    void excludesItselfWhenResaving() throws Exception {
        when(formMapper.selectOne(any())).thenReturn(mo());
        when(formMapper.selectList(any())).thenReturn(List.of(out("CL-SELF", "已审核", "CL001", 24.2)));

        // 重存同一张已审核单据：不能把自己的 24.2 重复算进去
        service.validateMaterialIssue(
                List.of(Map.of("加工单号", "MO-1", "材料编码", "CL001", "数量", 24.2)), "CL-SELF");
    }

    @Test
    void rejectsMaterialNotInPlan() throws Exception {
        when(formMapper.selectOne(any())).thenReturn(mo());
        // 未列入需用明细时在查历史领料之前就应拒绝，无需 stub selectList

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                service.validateMaterialIssue(
                        List.of(Map.of("加工单号", "MO-1", "材料编码", "CL999", "材料名称", "未列入材料", "数量", 1)),
                        "CL-NEW"));
        assertTrue(e.getMessage().contains("未列入"), e.getMessage());
    }

    @Test
    void skipsLinesWithoutOrderOrCode() throws Exception {
        // 无加工单号（直接领料）不受加工单额度约束
        service.validateMaterialIssue(List.of(Map.of("材料编码", "CL001", "数量", 999)), "CL-NEW");
    }
}
