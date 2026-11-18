package com.mes.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormApproval;
import com.mes.entity.FormData;
import com.mes.entity.PanelConfig;
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

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 采购退货审批的库存方向：
 * 检验退料货物从未入库，审批通过不能产生 OUT 事务；库存退料才按采购退货出库。
 */
@ExtendWith(MockitoExtension.class)
class PxServicePurchaseReturnStockTest {

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

    @Test
    void inspectionReturnDoesNotPostStockTransaction() throws Exception {
        FormData doc = document("CT-001", Map.of("单据编号", "CT-001", "供应商", "华东铝业",
                "检验单号", "JY-001", "退货原因", "检验不合格退回"));
        stubApproval(doc);

        service.callButton("PURCHASE_RETURN", "审批通过",
                Map.of("编号", "CT-001", "审批意见", "同意"), Map.of());

        verify(stockTxnService, never()).post(eq("PURCHASE_RETURN"), anyString(), any(), any());
    }

    @Test
    void stockReturnPostsOutboundStockTransaction() throws Exception {
        FormData doc = document("CT-002", Map.of("单据编号", "CT-002", "供应商", "华东铝业",
                "入库单号", "RK-001", "退货原因", "库存退料"));
        stubApproval(doc);

        service.callButton("PURCHASE_RETURN", "审批通过",
                Map.of("编号", "CT-002", "审批意见", "同意"), Map.of());

        verify(stockTxnService).post(eq("PURCHASE_RETURN"), eq("CT-002"), any(), any());
    }

    private void stubApproval(FormData doc) throws Exception {
        when(formMapper.selectOne(any())).thenReturn(doc);
        when(formMapper.selectList(any())).thenReturn(List.of());
        when(panelMapper.selectOne(any())).thenReturn(returnConfig());
        when(approvalMapper.selectOne(any())).thenReturn(submission());
    }

    private FormApproval submission() {
        FormApproval record = new FormApproval();
        record.setPanelCode("PURCHASE_RETURN");
        record.setFormNo("CT-001");
        record.setAction("SUBMIT");
        record.setResult("PENDING");
        return record;
    }

    private FormData document(String no, Map<String, Object> head) throws Exception {
        FormData row = new FormData();
        row.setId(1L);
        row.setPanelCode("PURCHASE_RETURN");
        row.setFormNo(no);
        row.setStatus("审批中");
        row.setData(json.writeValueAsString(head));
        row.setDetailData(json.writeValueAsString(Map.of("items", List.of(Map.of(
                "存货编码", "CL001", "存货名称", "铝棒", "计量单位", "件",
                "数量", 2, "实收数量", 2, "单价", 10, "含税金额", 22.6)))));
        row.setCreateTime(LocalDateTime.now());
        return row;
    }

    private PanelConfig returnConfig() throws Exception {
        PanelConfig config = new PanelConfig();
        config.setPanelCode("PURCHASE_RETURN");
        config.setPanelName("采购退货单");
        config.setCategory("单据");
        config.setConfig(json.writeValueAsString(Map.of(
                "metadata", Map.of(
                        "panelCode", "PURCHASE_RETURN", "panelName", "采购退货单", "panelCategory", "单据",
                        "autoCodeField", "单据编号",
                        "panelState", Map.of("dataName", "单据状态"),
                        "buttonGroups", List.of(), "panelButtons", List.of(),
                        "panelPageDto", Map.of("tablePages", List.of(), "formPages", List.of())),
                "dataSchema", Map.of("type", "object", "fields", List.of(
                        Map.of("dataName", "单据日期", "dataType", "日期"),
                        Map.of("dataName", "单据编号", "dataType", "文本", "autoCode", true))),
                "detail", Map.of("tabs", List.of(Map.of("key", "items", "fields", List.of()))))));
        return config;
    }
}
