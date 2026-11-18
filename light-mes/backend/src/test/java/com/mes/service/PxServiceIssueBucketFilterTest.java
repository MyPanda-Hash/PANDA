package com.mes.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormApproval;
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
import static org.mockito.Mockito.when;

/**
 * 成品报检单列表的「入库单」筛选：按【加工单号】找到同一张加工单上的产成品入库单，
 * 折算成筛选桶（未转入库单 / 入库单审批中 / 入库单已审批 / 入库单已驳回）后按桶过滤。
 *
 * <p>入库单是从加工单生成的，两者没有单据级外键，只能靠加工单号对齐；
 * 驳回后再提交审批，桶会从「入库单已驳回」变回「入库单审批中」。
 */
@ExtendWith(MockitoExtension.class)
class PxServiceIssueBucketFilterTest {

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

    private FormData doc(String panel, String no, String status,
                         Map<String, Object> head, Map<String, Object> detail) throws Exception {
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

    private FormData inspect(String no, String mo) throws Exception {
        return doc("FINISH_INSPECT", no, "已审核",
                Map.of("编号", no, "加工单号", mo),
                Map.of("items", List.of()));
    }

    private FormData finishIn(String no, String mo, String status) throws Exception {
        return doc("FINISH_IN", no, status,
                Map.of("单据编号", no, "加工单号", mo, "来源单号", mo),
                Map.of("items", List.of()));
    }

    /** queryFormDataList 的取库顺序：先本面板单据，再 FINISH_IN 索引。 */
    private void given(List<FormData> inspects, List<FormData> finishIns) {
        when(formMapper.selectList(any())).thenReturn(inspects, finishIns);
        when(approvalMapper.selectList(any())).thenReturn(List.of());
    }

    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> query(Map<String, Object> condition) {
        Map<String, Object> res = service.queryFormDataList("FINISH_INSPECT", "", condition, 1, 200);
        return (List<Map<String, Object>>) res.get("list");
    }

    @Test
    void listCarriesIssueBucket() throws Exception {
        given(List.of(inspect("BJ-1", "MO-1"), inspect("BJ-2", "MO-2")),
                List.of(finishIn("FI-1", "MO-1", "已审核")));
        List<Map<String, Object>> rows = query(null);
        assertEquals(2, rows.size());
        assertEquals("入库单已审批", rows.get(0).get("入库单状态"));
        assertEquals("未转入库单", rows.get(1).get("入库单状态"));
    }

    @Test
    void filtersNotTransferred() throws Exception {
        given(List.of(inspect("BJ-1", "MO-1"), inspect("BJ-2", "MO-2")),
                List.of(finishIn("FI-1", "MO-1", "审批中")));
        List<Map<String, Object>> rows = query(Map.of("入库单状态", "未转入库单"));
        assertEquals(1, rows.size());
        assertEquals("BJ-2", rows.get(0).get("编号"));
    }

    @Test
    void filtersPendingApproval() throws Exception {
        given(List.of(inspect("BJ-1", "MO-1"), inspect("BJ-2", "MO-2")),
                List.of(finishIn("FI-1", "MO-1", "审批中")));
        List<Map<String, Object>> rows = query(Map.of("入库单状态", "入库单审批中"));
        assertEquals(1, rows.size());
        assertEquals("BJ-1", rows.get(0).get("编号"));
    }

    /** 「转入库单」= 除未转以外的全部（不是包含匹配，未转入库单不能被算进来）。 */
    @Test
    void transferredCoversEveryStateExceptNone() throws Exception {
        given(List.of(inspect("BJ-1", "MO-1"), inspect("BJ-2", "MO-2")),
                List.of(finishIn("FI-1", "MO-1", "已审核")));
        List<Map<String, Object>> rows = query(Map.of("入库单状态", "转入库单"));
        assertEquals(1, rows.size());
        assertEquals("BJ-1", rows.get(0).get("编号"));
    }

    /** 驳回后重新提交：桶从「入库单已驳回」变回「入库单审批中」，筛选跟着变。 */
    @Test
    void rejectedThenResubmittedSwitchesBucket() throws Exception {
        FormData draft = finishIn("FI-1", "MO-1", "草稿");
        FormApproval reject = new FormApproval();
        reject.setPanelCode("FINISH_IN");
        reject.setFormNo("FI-1");
        reject.setAction("REJECT");
        when(formMapper.selectList(any())).thenReturn(List.of(inspect("BJ-1", "MO-1")), List.of(draft));
        when(approvalMapper.selectList(any())).thenReturn(List.of(reject));
        assertEquals(1, query(Map.of("入库单状态", "入库单已驳回")).size());

        FormData pending = finishIn("FI-1", "MO-1", "审批中");
        when(formMapper.selectList(any())).thenReturn(List.of(inspect("BJ-1", "MO-1")), List.of(pending));
        assertEquals(1, query(Map.of("入库单状态", "入库单审批中")).size());
        assertEquals(0, query(Map.of("入库单状态", "入库单已驳回")).size());
    }
}
