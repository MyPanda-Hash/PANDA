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

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.when;

/**
 * 可用量校验：库存不够不许领，不能把库存领成负数。
 */
@ExtendWith(MockitoExtension.class)
class PxServiceStockGuardTest {

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
                        "processes", List.of(Map.of("工序编码", "PX001", "工序名称", "下料")),
                        "materials", List.of(Map.of("材料编码", "CL001", "材料名称", "圆钢",
                                "计划数量", 20, "预出仓库", "原料仓"))));
    }

    private FormData out(String qty) throws Exception {
        return doc("MATERIAL_OUT", "CL-1", "草稿", Map.of("单据编号", "CL-1"),
                Map.of("items", List.of(Map.of(
                        "加工单号", "MO-1", "材料编码", "CL001", "材料名称", "圆钢",
                        "仓库", "原料仓", "数量", Double.parseDouble(qty)))));
    }

    @Test
    void rejectsWhenStockInsufficient() throws Exception {
        when(stockTxnService.quantityOf(anyString(), anyString(), anyString()))
                .thenReturn(new BigDecimal("5"));
        when(formMapper.selectOne(any())).thenReturn(mo());
        when(formMapper.selectList(any())).thenReturn(List.of());

        IllegalStateException e = assertThrows(IllegalStateException.class,
                () -> service.validateMaterialIssue(
                        List.of(Map.of("加工单号", "MO-1", "材料编码", "CL001",
                                "材料名称", "圆钢", "仓库", "原料仓", "数量", 10)), null));
        assertTrue(e.getMessage().contains("可用量不足"), e.getMessage());
        assertTrue(e.getMessage().contains("5"), "要说清现存多少：" + e.getMessage());
    }

    @Test
    void allowsWhenStockEnough() throws Exception {
        when(stockTxnService.quantityOf(anyString(), anyString(), anyString()))
                .thenReturn(new BigDecimal("100"));
        when(formMapper.selectOne(any())).thenReturn(mo());
        when(formMapper.selectList(any())).thenReturn(List.of());
        // 不抛异常即通过
        service.validateMaterialIssue(
                List.of(Map.of("加工单号", "MO-1", "材料编码", "CL001",
                        "材料名称", "圆钢", "仓库", "原料仓", "数量", 10)), null);
    }
}
