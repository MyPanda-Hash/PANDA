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
 * 按排产批次报工：强制先排产，累计报工不得超过批次数量；首件先检、再报工。
 *
 * <p>首检是「工序 × 加工单」级的门：同一道工序的第一个批次检过，后续批次直接放行，
 * 不再重复首检；首检没建单 / 没审 / 不合格时，任何批次都不许报工。
 */
@ExtendWith(MockitoExtension.class)
class PxServiceBatchReportTest {

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

    private FormData batch(String no, String procCode, double qty, String status) throws Exception {
        return doc("DISPATCH", no, status,
                Map.of("单据编号", no, "加工单号", "MO-1", "生产车间", "熔铸车间"),
                Map.of("items", List.of(Map.of(
                        "工序编码", procCode, "工序名称", "下料", "派工数量", qty,
                        "班组", "1班", "设备", "锯床-01"))));
    }

    private FormData report(String dispatchNo, double qty, double qualified) throws Exception {
        return doc("PROCESS_REPORT", "GX-1", "已审核",
                Map.of("单据编号", "GX-1", "加工单号", "MO-1"),
                Map.of("items", List.of(Map.of(
                        "排产单号", dispatchNo, "工序编码", "PX001",
                        "报工数量", qty, "合格数量", qualified))));
    }

    private FormData firstInspect(String procCode, String status, String verdict) throws Exception {
        return doc("FIRST_INSPECT", "SJ-1", status,
                Map.of("单据编号", "SJ-1", "加工单号", "MO-1"),
                Map.of("items", List.of(Map.of(
                        "工序编码", procCode, "工序名称", "下料", "检验判定", verdict))));
    }

    /** reportBatchBoard 的取库顺序固定为：汇报单 → 首检单 → 排产批次。 */
    private void givenBoard(List<FormData> reports, List<FormData> inspects, List<FormData> batches) {
        when(formMapper.selectList(any())).thenReturn(reports, inspects, batches);
    }

    @Test
    void batchBoardListsApprovedBatchesWithProgress() throws Exception {
        givenBoard(List.of(report("PG-1", 600, 580)), List.of(),
                List.of(batch("PG-1", "PX001", 2000, "已审核")));

        List<Map<String, Object>> rows = service.reportBatchBoard();
        assertEquals(1, rows.size());
        Map<String, Object> r = rows.get(0);
        assertEquals("PG-1", r.get("排产单号"));
        assertEquals(2000.0, ((Number) r.get("批次数量")).doubleValue(), 0.001);
        assertEquals(600.0, ((Number) r.get("已报数量")).doubleValue(), 0.001);
        assertEquals(1400.0, ((Number) r.get("剩余数量")).doubleValue(), 0.001);
    }

    /** 首检没建单：看板标「未建单」并置灰，原因指向先做首件检验。 */
    @Test
    void batchWithoutFirstInspectIsNotReportable() throws Exception {
        givenBoard(List.of(), List.of(), List.of(batch("PG-1", "PX001", 100, "已审核")));

        Map<String, Object> r = service.reportBatchBoard().get(0);
        assertEquals("未建单", r.get("首检状态"));
        assertEquals(false, r.get("首检是否通过"));
        assertEquals(false, r.get("可否报工"));
        assertTrue(String.valueOf(r.get("不可报工原因")).contains("首件"), String.valueOf(r.get("不可报工原因")));
    }

    @Test
    void batchIsReportableWhenProcessFirstInspectPassed() throws Exception {
        givenBoard(List.of(), List.of(firstInspect("PX001", "已审核", "合格")),
                List.of(batch("PG-1", "PX001", 100, "已审核")));

        Map<String, Object> r = service.reportBatchBoard().get(0);
        assertEquals("已通过", r.get("首检状态"));
        assertEquals("SJ-1", r.get("首检单号"));
        assertEquals(true, r.get("可否报工"));
        assertEquals("", r.get("不可报工原因"));
    }

    /** 一工序一单：同工序的第二个批次不用再首检，直接可报工。 */
    @Test
    void secondBatchOfSameProcessNeedsNoNewFirstInspect() throws Exception {
        givenBoard(List.of(), List.of(firstInspect("PX001", "已审核", "合格")),
                List.of(batch("PG-1", "PX001", 100, "已审核"), batch("PG-2", "PX001", 50, "已审核")));

        List<Map<String, Object>> rows = service.reportBatchBoard();
        assertEquals(2, rows.size());
        for (Map<String, Object> r : rows) {
            assertEquals(true, r.get("可否报工"), String.valueOf(r.get("排产单号")));
            assertEquals("SJ-1", r.get("首检单号"));
        }
    }

    @Test
    void draftBatchIsNotReportable() throws Exception {
        givenBoard(List.of(), List.of(), List.of(batch("PG-9", "PX001", 100, "草稿")));
        assertTrue(service.reportBatchBoard().isEmpty(), "草稿排产不能报工（强制先排产且要审批）");
    }

    @Test
    void overBatchQuantityIsRejected() throws Exception {
        givenBoard(List.of(report("PG-1", 90, 90)), List.of(firstInspect("PX001", "已审核", "合格")),
                List.of(batch("PG-1", "PX001", 100, "已审核")));

        IllegalStateException e = assertThrows(IllegalStateException.class,
                () -> service.validateBatchReport("PG-1", 20, 18));
        assertTrue(e.getMessage().contains("剩余") || e.getMessage().contains("超过"), e.getMessage());
    }

    /** 首检没过：报工直接被拒，报错里说清是首件的事。 */
    @Test
    void reportBlockedUntilFirstInspectPassed() throws Exception {
        givenBoard(List.of(), List.of(), List.of(batch("PG-1", "PX001", 100, "已审核")));

        IllegalStateException e = assertThrows(IllegalStateException.class,
                () -> service.validateBatchReport("PG-1", 1, 0));
        assertTrue(e.getMessage().contains("首件"), e.getMessage());
    }

    /** 首检单建了但没审：同样拦住报工。 */
    @Test
    void reportBlockedWhileFirstInspectPending() throws Exception {
        givenBoard(List.of(), List.of(firstInspect("PX001", "审批中", "")),
                List.of(batch("PG-1", "PX001", 100, "已审核")));

        IllegalStateException e = assertThrows(IllegalStateException.class,
                () -> service.validateBatchReport("PG-1", 1, 0));
        assertTrue(e.getMessage().contains("SJ-1"), e.getMessage());
    }

    /** 报工数量由 合格+不合格 组成，全为 0 时没有可报的量，必须拒绝。 */
    @Test
    void rejectsZeroReport() {
        IllegalArgumentException e = assertThrows(IllegalArgumentException.class,
                () -> service.validateBatchReport("PG-1", 0, 0));
        assertTrue(e.getMessage().contains("大于 0"), e.getMessage());
    }

    @Test
    void canReportWithinRemaining() throws Exception {
        givenBoard(List.of(report("PG-1", 90, 90)), List.of(firstInspect("PX001", "已审核", "合格")),
                List.of(batch("PG-1", "PX001", 100, "已审核")));

        double qty = service.validateBatchReport("PG-1", 9, 1);
        assertEquals(10.0, qty, 0.001, "报工数量 = 合格 + 不合格");
    }

    /** 首检已通过时再点「生成首件报检单」不重复建单，直接把原单号返回来。 */
    @Test
    void reusesExistingFirstInspectForBatch() throws Exception {
        givenBoard(List.of(), List.of(firstInspect("PX001", "已审核", "合格")),
                List.of(batch("PG-1", "PX001", 100, "已审核")));

        Map<String, Object> res = service.createFirstInspectByBatch("PG-1");
        assertEquals(true, res.get("已存在"));
        assertEquals("SJ-1", res.get("编号"));
        assertEquals("已通过", res.get("首检状态"));
    }
}
