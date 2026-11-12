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
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 选单取「来源明细哪一页」必须按目标单据选对：
 * 加工单同时有 产成品 / 材料 / 工序 三页明细，取错页会出现
 * 「可执行 2.0（产成品数量）、本次 4.2（材料需求量）」这种超量误报。
 */
@ExtendWith(MockitoExtension.class)
class PxServiceSourceRowsTest {

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

    private FormData manuOrder() throws Exception {
        FormData d = new FormData();
        d.setPanelCode("MANU_ORDER");
        d.setFormNo("MO-1");
        d.setStatus("已审核");
        d.setData(json.writeValueAsString(new HashMap<>(Map.of("单据编号", "MO-1"))));
        Map<String, Object> detail = new HashMap<>();
        detail.put("products", List.of(new HashMap<>(Map.of("产品编码", "CP004", "数量", 2))));
        detail.put("materials", List.of(new HashMap<>(Map.of("材料编码", "CL001", "数量", 4.2))));
        detail.put("processes", List.of(new HashMap<>(Map.of("工序编码", "PX001", "计划数量", 2))));
        d.setDetailData(json.writeValueAsString(detail));
        d.setCreateTime(LocalDateTime.now());
        return d;
    }

    @Test
    void materialReqTakesMaterialsNotProducts() throws Exception {
        List<Map<String, Object>> rows = service.sourceRowsFor(manuOrder(), "MATERIAL_REQ", "MANU_ORDER");
        assertEquals(1, rows.size());
        assertTrue(rows.get(0).containsKey("材料编码"), "领料申请单要取材料明细，不能取产成品行");
        assertEquals(4.2, Double.parseDouble(String.valueOf(rows.get(0).get("数量"))), 0.001);
    }

    @Test
    void materialOutStillTakesMaterials() throws Exception {
        List<Map<String, Object>> rows = service.sourceRowsFor(manuOrder(), "MATERIAL_OUT", "MANU_ORDER");
        assertTrue(rows.get(0).containsKey("材料编码"));
    }

    @Test
    void processReportTakesProcesses() throws Exception {
        List<Map<String, Object>> rows = service.sourceRowsFor(manuOrder(), "PROCESS_REPORT", "MANU_ORDER");
        assertTrue(rows.get(0).containsKey("工序编码"));
    }

    @Test
    void finishInTakesProducts() throws Exception {
        List<Map<String, Object>> rows = service.sourceRowsFor(manuOrder(), "FINISH_IN", "MANU_ORDER");
        assertTrue(rows.get(0).containsKey("产品编码"));
    }

    @Test
    void zeroQuantityReqItemsAreDropped() throws Exception {
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", List.of(
                new HashMap<>(Map.of("材料编码", "CL001", "数量", 4.2)),
                new HashMap<>(Map.of("材料编码", "CL005", "数量", 0)),
                new HashMap<>(Map.of("材料编码", "CL004"))));
        service.dropZeroQuantityReqItems(detail);
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> kept = (List<Map<String, Object>>) detail.get("items");
        assertEquals(1, kept.size(), "只留数量大于 0 的行，否则整张单报「执行数量必须大于 0」");
        assertEquals("CL001", kept.get(0).get("材料编码"));
    }
}
