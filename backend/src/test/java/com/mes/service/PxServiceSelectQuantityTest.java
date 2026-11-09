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
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.when;

/**
 * 选单校验的数量识别。
 *
 * <p>工序汇报单的数量字段叫「报工数量」，加工单工序行的上限字段叫「计划数量」；
 * 校验器如果只认「数量」，选单进来的行会被当成 0 而报「执行数量必须大于 0」。
 */
@ExtendWith(MockitoExtension.class)
class PxServiceSelectQuantityTest {

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

    private FormData mo(int planQty) throws Exception {
        return doc("MANU_ORDER", "MO-1", "已审核", Map.of("单据编号", "MO-1"),
                Map.of("products", List.of(Map.of("产品编码", "CP001", "数量", planQty)),
                        "processes", List.of(Map.of("产品编码", "CP001", "工序编码", "PX001",
                                "工序名称", "下料", "计划数量", planQty)),
                        "materials", List.of()));
    }

    @Test
    void reportedQtyIgnoresDraftsAndCanExcludeSelf() throws Exception {
        FormData approved = doc("PROCESS_REPORT", "GX-1", "已审核",
                Map.of("单据编号", "GX-1", "加工单号", "MO-1"),
                Map.of("items", List.of(Map.of("工序编码", "PX001", "报工数量", 6))));
        FormData draft = doc("PROCESS_REPORT", "GX-2", "草稿",
                Map.of("单据编号", "GX-2", "加工单号", "MO-1"),
                Map.of("items", List.of(Map.of("工序编码", "PX001", "报工数量", 99))));
        FormData self = doc("PROCESS_REPORT", "GX-SELF", "已审核",
                Map.of("单据编号", "GX-SELF", "加工单号", "MO-1"),
                Map.of("items", List.of(Map.of("工序编码", "PX001", "报工数量", 4))));
        when(formMapper.selectList(any())).thenReturn(List.of(approved, draft, self));

        // 不排除时：已审核的 6 + 自身的 4 = 10；草稿的 99 不参与
        assertEquals(10.0, service.reportedQtyByProcess("MO-1").getOrDefault("PX001", 0.0), 0.001,
                "草稿不计数");
        // 排除自身：只剩 6
        assertEquals(6.0, service.reportedQtyByProcess("MO-1", "GX-SELF").getOrDefault("PX001", 0.0), 0.001,
                "排除自身后只剩其他已审核单据的 6");
    }

    @Test
    void selectRejectsFullyReportedProcess() throws Exception {
        FormData mo = mo(10);
        // 已报满 10
        FormData full = doc("PROCESS_REPORT", "GX-FULL", "已审核",
                Map.of("单据编号", "GX-FULL", "加工单号", "MO-1"),
                Map.of("items", List.of(Map.of("工序编码", "PX001", "报工数量", 10))));
        when(formMapper.selectOne(any())).thenReturn(mo);
        when(formMapper.selectList(any())).thenReturn(List.of(full));

        IllegalStateException e = org.junit.jupiter.api.Assertions.assertThrows(
                IllegalStateException.class,
                () -> service.validateSourceLinks("PROCESS_REPORT", "GX-NEW",
                        Map.of("items", List.of(Map.of(
                                "来源面板", "MANU_ORDER", "来源单号", "MO-1", "来源行号", 1,
                                "工序编码", "PX001", "报工数量", 5)))));
        org.junit.jupiter.api.Assertions.assertTrue(e.getMessage().contains("已全部报工"), e.getMessage());
    }
}
