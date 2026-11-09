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
 * 工序汇报单弃审前置：加工单已完工（产成品已入库）时不允许再撤报工。
 *
 * <p>实测踩到：MO 入库 20 件后，把检验批的汇报单弃审又审核，库存与完工判定已经
 * 各算过一次；如果不拦住，车间能一边拿着入库单一边把报工全部撤掉。
 */
@ExtendWith(MockitoExtension.class)
class PxServiceReportUnauditGuardTest {

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

    private FormData doc(String panel, String no, String status, Map<String, Object> head, Map<String, Object> detail)
            throws Exception {
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

    @Test
    void rejectsUnauditWhenOrderAlreadyFinished() throws Exception {
        FormData report = doc("PROCESS_REPORT", "GX-20261106-008", "已审核",
                Map.of("单据编号", "GX-20261106-008", "加工单号", "MO-20261106-002"),
                Map.of("items", List.of(Map.of("加工单号", "MO-20261106-002", "工序编码", "PX007",
                        "报工数量", 10, "合格数量", 10))));
        FormData mo = doc("MANU_ORDER", "MO-20261106-002", "已完工",
                Map.of("单据编号", "MO-20261106-002"),
                Map.of("products", List.of(Map.of("产品编码", "CP004", "数量", 20, "累计入库数量", 20)),
                        "processes", List.of()));

        when(formMapper.selectOne(any())).thenReturn(report, mo);
        when(formMapper.selectList(any())).thenReturn(List.of());

        IllegalStateException ex = assertThrows(IllegalStateException.class,
                () -> service.callButton("PROCESS_REPORT", "弃审",
                        Map.of("编号", "GX-20261106-008"), Map.of("code", "GX-20261106-008")));
        assertTrue(ex.getMessage().contains("已完工"), ex.getMessage());
        assertEquals("已审核", report.getStatus(), "被拦下后单据状态不能变");
    }
}