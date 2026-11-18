package com.mes.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.entity.PanelConfig;
import com.mes.mapper.FormApprovalMapper;
import com.mes.mapper.FormDataMapper;
import com.mes.mapper.PanelConfigMapper;
import com.mes.panel.PanelActionRegistry;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 首件检验按工序走：一道工序一张首检单，首检是报工门不是入库门。
 *
 * <p>改造前：一个加工单只生成一张首检单，明细把全部工序一次塞进去，报检数量 = 计划数量，
 * 且首检在报工之后才生成、只在入库时校验。这样首检拦不住批量生产，单据统计口径也不真实。
 *
 * <p>改造后：工序首次报工就生成该工序的首检单（样本量=首件数量），首检未审核则该工序
 * 不能继续报工、下道工序不能开工；入库端保留兜底门（每道工序都要有已审核首检单）。
 */
@ExtendWith(MockitoExtension.class)
class PxServiceFirstInspectPerProcessTest {

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

    private PanelConfig config(String code) {
        PanelConfig cfg = new PanelConfig();
        cfg.setPanelCode(code);
        cfg.setPanelName(code);
        cfg.setConfig("{\"metadata\":{\"panelCode\":\"" + code
                + "\",\"autoCodeField\":\"单据编号\",\"panelButtons\":[],\"buttonGroups\":[],\"panelPageDto\":{}},"
                + "\"dataSchema\":{\"fields\":[]},\"detail\":{\"tabs\":[]}}");
        return cfg;
    }

    private FormData document(String panel, String no, String status,
                              Map<String, Object> head, Map<String, Object> detail) throws Exception {
        FormData row = new FormData();
        row.setId(1L);
        row.setPanelCode(panel);
        row.setFormNo(no);
        row.setStatus(status);
        row.setData(json.writeValueAsString(head));
        row.setDetailData(json.writeValueAsString(detail));
        row.setCreateTime(LocalDateTime.now());
        return row;
    }

    /** 加工单：两道工序（车削 10 件 / 钻孔 10 件）。 */
    private FormData mo() throws Exception {
        return document("MANU_ORDER", "MO-1", "已审核",
                Map.of("单据编号", "MO-1", "生产车间", "熔铸车间"),
                Map.of(
                        "products", List.of(Map.of("产品编码", "CP002", "产品名称", "轴套 C",
                                "规格型号", "ZT-C-30", "生产单位", "件", "数量", 10)),
                        "materials", List.of(),
                        "processes", List.of(
                                Map.of("工序编码", "PX002", "工序名称", "车削", "加工顺序", 1, "计划数量", 10),
                                Map.of("工序编码", "PX004", "工序名称", "钻孔", "加工顺序", 2, "计划数量", 10))));
    }

    private FormData report(String procCode, String procName, double qty, double qualified, double rejected) throws Exception {
        return document("PROCESS_REPORT", "GX-" + procCode, "已审核",
                Map.of("单据编号", "GX-" + procCode, "加工单号", "MO-1"),
                Map.of("items", List.of(Map.of("工序编码", procCode, "工序名称", procName,
                        "报工数量", qty, "合格数量", qualified, "不合格数量", rejected))));
    }

    /** 首检单：一工序一行（新形态）或一工序一单。 */
    private FormData firstInspect(String procCode, String procName, String status, double qty) throws Exception {
        return document("FIRST_INSPECT", "SJ-" + procCode, status,
                Map.of("单据编号", "SJ-" + procCode, "加工单号", "MO-1", "业务类型", "首件报检"),
                Map.of("items", List.of(Map.of("工序编码", procCode, "工序名称", procName,
                        "加工单号", "MO-1", "报检数量", qty))));
    }


    /** 首检单带判定：status 已审核 + 检验判定。 */
    private FormData judgedInspect(String procCode, String procName, String verdict, double qty) throws Exception {
        return document("FIRST_INSPECT", "SJ-" + procCode, "已审核",
                Map.of("单据编号", "SJ-" + procCode, "加工单号", "MO-1", "业务类型", "首件报检"),
                Map.of("items", List.of(Map.of("工序编码", procCode, "工序名称", procName,
                        "加工单号", "MO-1", "报检数量", qty, "检验判定", verdict))));
    }


    /** 首检单带判定 + 处置（处置方式/处置数量）。 */
    private FormData disposedInspect(String procCode, String procName, String verdict, String disposition,
                                     double disposeQty) throws Exception {
        return document("FIRST_INSPECT", "SJ-" + procCode, "已审核",
                Map.of("单据编号", "SJ-" + procCode, "加工单号", "MO-1", "业务类型", "首件报检"),
                Map.of("items", List.of(Map.of("工序编码", procCode, "工序名称", procName,
                        "存货编码", "CP002", "加工单号", "MO-1", "报检数量", 1,
                        "检验判定", verdict, "处置方式", disposition, "处置数量", disposeQty))));
    }

    private List<FormData> inserted(String panel) {
        ArgumentCaptor<FormData> captor = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper, org.mockito.Mockito.atLeast(0)).insert(captor.capture());
        List<FormData> out = new ArrayList<>();
        for (FormData d : captor.getAllValues()) {
            if (panel.equals(d.getPanelCode())) out.add(d);
        }
        return out;
    }

    @Test
    void firstReportIsCappedToFirstArticleAndCreatesInspectionForThatProcess() throws Exception {
        when(panelMapper.selectOne(any())).thenReturn(config("PROCESS_REPORT"), config("FIRST_INSPECT"));
        when(formMapper.selectOne(any())).thenReturn(mo());
        when(formMapper.selectList(any())).thenReturn(List.of(materialOut("已审核", 50)));

        service.callButton("MANU_ORDER", "生成工序汇报单（自制汇报）", Map.of("编号", "MO-1"), Map.of());

        List<FormData> reports = inserted("PROCESS_REPORT");
        assertEquals(1, reports.size());
        Map<?, ?> reportDetail = json.readValue(reports.get(0).getDetailData(), Map.class);
        Map<?, ?> reportRow = (Map<?, ?>) ((List<?>) reportDetail.get("items")).get(0);
        assertEquals(1.0, ((Number) reportRow.get("报工数量")).doubleValue(), 0.001,
                "首件未检只能报首件数量，不能一次报满 10 件");
        assertEquals("PX002", reportRow.get("工序编码"));

        List<FormData> inspects = inserted("FIRST_INSPECT");
        assertEquals(1, inspects.size(), "一道工序应当且只应当生成一张首检单");
        Map<?, ?> head = json.readValue(inspects.get(0).getData(), Map.class);
        assertEquals("MO-1", head.get("加工单号"));
        Map<?, ?> inspectDetail = json.readValue(inspects.get(0).getDetailData(), Map.class);
        List<?> rows = (List<?>) inspectDetail.get("items");
        assertEquals(1, rows.size(), "首检单只带本工序一行，不再把三道工序塞一张单");
        Map<?, ?> row = (Map<?, ?>) rows.get(0);
        assertEquals("PX002", row.get("工序编码"));
        assertEquals(1.0, ((Number) row.get("报检数量")).doubleValue(), 0.001,
                "报检数量是首件样本量，不是计划数量");
    }

    @Test
    void furtherReportBlockedWhileInspectionPending() throws Exception {
        lenient().when(panelMapper.selectOne(any())).thenReturn(config("PROCESS_REPORT"));
        when(formMapper.selectOne(any())).thenReturn(mo());
        when(formMapper.selectList(any())).thenAnswer(inv -> {
            // 第一次查：工序汇报；第二次：首检单
            return List.of(materialOut("已审核", 50), report("PX002", "车削", 1, 1, 0),
                    firstInspect("PX002", "车削", "草稿", 1));
        });

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                service.callButton("MANU_ORDER", "生成工序汇报单（自制汇报）", Map.of("编号", "MO-1"), Map.of()));

        assertTrue(e.getMessage().contains("首件"), e.getMessage());
        verify(formMapper, never()).insert(any(FormData.class));
    }

    @Test
    void reportContinuesWithRemainingAfterInspectionApproved() throws Exception {
        when(panelMapper.selectOne(any())).thenReturn(config("PROCESS_REPORT"));
        when(formMapper.selectOne(any())).thenReturn(mo());
        when(formMapper.selectList(any())).thenReturn(List.of(
                materialOut("已审核", 50),
                report("PX002", "车削", 1, 1, 0),
                firstInspect("PX002", "车削", "已审核", 1)));

        service.callButton("MANU_ORDER", "生成工序汇报单（自制汇报）", Map.of("编号", "MO-1"), Map.of());

        List<FormData> reports = inserted("PROCESS_REPORT");
        assertEquals(1, reports.size());
        Map<?, ?> detail = json.readValue(reports.get(0).getDetailData(), Map.class);
        Map<?, ?> row = (Map<?, ?>) ((List<?>) detail.get("items")).get(0);
        assertEquals(9.0, ((Number) row.get("报工数量")).doubleValue(), 0.001,
                "首检通过后按剩余量报工");
        assertTrue(inserted("FIRST_INSPECT").isEmpty(), "已有首检单不再重复生成");
    }

    @Test
    void nextProcessBlockedWhilePreviousInspectionPending() throws Exception {
        lenient().when(panelMapper.selectOne(any())).thenReturn(config("PROCESS_REPORT"));
        when(formMapper.selectOne(any())).thenReturn(mo());
        when(formMapper.selectList(any())).thenReturn(
                List.of(materialOut("已审核", 50), report("PX002", "车削", 1, 1, 0)),
                List.of(firstInspect("PX002", "车削", "草稿", 1)));

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                service.callButton("MANU_ORDER", "生成工序汇报单（自制汇报）", Map.of("编号", "MO-1"), Map.of()));

        assertTrue(e.getMessage().contains("车削"), e.getMessage());
        assertTrue(e.getMessage().contains("首件"), e.getMessage());
    }

    @Test
    void eachProcessGetsItsOwnInspection() throws Exception {
        when(panelMapper.selectOne(any())).thenReturn(config("PROCESS_REPORT"), config("FIRST_INSPECT"));
        when(formMapper.selectOne(any())).thenReturn(mo());
        when(formMapper.selectList(any())).thenReturn(List.of(
                materialOut("已审核", 50),
                report("PX002", "车削", 10, 10, 0),
                firstInspect("PX002", "车削", "已审核", 1)));

        service.callButton("MANU_ORDER", "生成工序汇报单（自制汇报）", Map.of("编号", "MO-1"), Map.of());

        List<FormData> inspects = inserted("FIRST_INSPECT");
        assertEquals(1, inspects.size(), "车削的首检出过了，这次是钻孔自己的首检单");
        Map<?, ?> detail = json.readValue(inspects.get(0).getDetailData(), Map.class);
        Map<?, ?> row = (Map<?, ?>) ((List<?>) detail.get("items")).get(0);
        assertEquals("PX004", row.get("工序编码"));
    }

    /**
     * 门限：同一道工序只要有一张有效首检单（已审核且合格），后续再报工**不能再建第二张**。
     * 一手一工一单是硬约束，重复建单会让追踪里一道工序挂出一串首检单。
     */
    @Test
    void sameProcessNeverGetsSecondInspection() throws Exception {
        when(panelMapper.selectOne(any())).thenReturn(config("PROCESS_REPORT"), config("FIRST_INSPECT"));
        when(formMapper.selectOne(any())).thenReturn(mo());
        when(formMapper.selectList(any())).thenReturn(List.of(
                materialOut("已审核", 50),
                report("PX002", "车削", 4, 4, 0),
                firstInspect("PX002", "车削", "已审核", 1)));

        service.callButton("MANU_ORDER", "生成工序汇报单（自制汇报）", Map.of("编号", "MO-1"), Map.of());

        assertEquals(0, inserted("FIRST_INSPECT").size(),
                "车削首检已通过，再报车削不该新建首检单");
    }

    @Test
    void receivingRequiresEveryProcessInspectionApproved() throws Exception {
        lenient().when(panelMapper.selectOne(any())).thenReturn(config("FINISH_IN"));
        when(formMapper.selectOne(any())).thenReturn(mo());
        when(formMapper.selectList(any())).thenReturn(List.of(
                report("PX002", "车削", 10, 10, 0),
                report("PX004", "钻孔", 10, 10, 0),
                firstInspect("PX002", "车削", "已审核", 1),
                firstInspect("PX004", "钻孔", "草稿", 1)));

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                service.callButton("MANU_ORDER", "生成产成品入库单", Map.of("编号", "MO-1"), Map.of()));

        assertTrue(e.getMessage().contains("钻孔"), e.getMessage());
        assertTrue(e.getMessage().contains("首件"), e.getMessage());
        verify(formMapper, never()).insert(any(FormData.class));
    }

    /**
     * 委外回厂不再借道首件报检：2026-11-13 起回厂检验是独立的「委外检验单」，
     * 没有它（或没审完、判定不合格）一律不许生成委外入库单，也不再补建首检单。
     */
    @Test
    void outsourcedInboundRequiresOutsourceInspectionNotFirstInspect() throws Exception {
        FormData oo = document("OUTSOURCE_ORDER", "WW-1", "已审核",
                Map.of("单据编号", "WW-1", "委外供应商", "宁波精工", "生产车间", "熔铸车间"),
                Map.of("products", List.of(Map.of("产品编码", "CP003", "产品名称", "法兰 D",
                        "计量单位", "件", "数量", 20, "委外单价", 8)),
                        "materials", List.of()));
        lenient().when(panelMapper.selectOne(any())).thenReturn(config("FIRST_INSPECT"));
        when(formMapper.selectOne(any())).thenReturn(oo);
        when(formMapper.selectList(any())).thenReturn(List.of());

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                service.callButton("OUTSOURCE_ORDER", "生成委外入库单", Map.of("编号", "WW-1"), Map.of()));

        assertTrue(e.getMessage().contains("委外检验单"), e.getMessage());
        assertTrue(inserted("FIRST_INSPECT").isEmpty(), "委外回厂不该再补首件报检单");
    }

    /** 首检单按本工序的报工量设上限：末道还没报（成品完工量 0）不该把车削的首检拦下来。 */
    @Test
    void firstArticleQtyIsMeasuredAgainstItsOwnProcess() throws Exception {
        FormData inspect = firstInspect("PX002", "车削", "草稿", 1);
        when(formMapper.selectList(any())).thenReturn(List.of(mo(), report("PX002", "车削", 1, 1, 0)));

        service.validateReportAgainstUpstream("FIRST_INSPECT", inspect,
                List.of(Map.of("存货编码", "CP002", "工序编码", "PX002", "工序名称", "车削", "报检数量", 1)));
    }

    @Test
    void firstArticleQtyCannotExceedProcessReport() throws Exception {
        FormData inspect = firstInspect("PX002", "车削", "草稿", 5);
        when(formMapper.selectList(any())).thenReturn(List.of(mo(), report("PX002", "车削", 1, 1, 0)));

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                service.validateReportAgainstUpstream("FIRST_INSPECT", inspect,
                        List.of(Map.of("存货编码", "CP002", "工序编码", "PX002", "工序名称", "车削", "报检数量", 5))));

        assertTrue(e.getMessage().contains("报工"), e.getMessage());
        assertTrue(e.getMessage().contains("车削"), e.getMessage());
    }

    /** 兜底补建只补「已经开工报工」的工序：还没报工的工序不该先有首检单。 */
    @Test
    void backfillOnlyCreatesInspectionForStartedProcesses() throws Exception {
        lenient().when(panelMapper.selectOne(any())).thenReturn(config("FIRST_INSPECT"));
        when(formMapper.selectOne(any())).thenReturn(mo());
        when(formMapper.selectList(any())).thenReturn(List.of(report("PX002", "车削", 1, 1, 0)));

        service.advanceCompletion("MO-1");

        List<FormData> inspects = inserted("FIRST_INSPECT");
        assertEquals(1, inspects.size(), "只有车削报过工，只补它一张");
        Map<?, ?> detail = json.readValue(inspects.get(0).getDetailData(), Map.class);
        Map<?, ?> row = (Map<?, ?>) ((List<?>) detail.get("items")).get(0);
        assertEquals("PX002", row.get("工序编码"));
    }

    /** 判定必填：没填检验判定不许提交审批。 */
    @Test
    void submitRequiresInspectionVerdict() throws Exception {
        lenient().when(panelMapper.selectOne(any())).thenReturn(config("FIRST_INSPECT"));
        FormData draft = document("FIRST_INSPECT", "SJ-1", "草稿",
                Map.of("单据编号", "SJ-1", "加工单号", "MO-1", "业务类型", "首件报检"),
                Map.of("items", List.of(Map.of("工序编码", "PX002", "工序名称", "车削",
                        "加工单号", "MO-1", "报检数量", 1))));
        when(formMapper.selectOne(any())).thenReturn(draft);
        lenient().when(formMapper.selectList(any())).thenReturn(List.of(mo(), report("PX002", "车削", 1, 1, 0)));

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                service.callButton("FIRST_INSPECT", "审核", Map.of("编号", "SJ-1"), Map.of()));

        assertTrue(e.getMessage().contains("检验判定"), e.getMessage());
    }

    /** 判定不合格：该工序不能继续报工，并要求重新首检。 */
    @Test
    void failedInspectionBlocksFurtherReporting() throws Exception {
        lenient().when(panelMapper.selectOne(any())).thenReturn(config("FIRST_INSPECT"));
        when(formMapper.selectOne(any())).thenReturn(mo());
        when(formMapper.selectList(any())).thenReturn(
                List.of(materialOut("已审核", 50), report("PX002", "车削", 1, 1, 0)),
                List.of(judgedInspect("PX002", "车削", "不合格", 1)));

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                service.callButton("MANU_ORDER", "生成工序汇报单（自制汇报）", Map.of("编号", "MO-1"), Map.of()));

        assertTrue(e.getMessage().contains("不合格"), e.getMessage());
        assertEquals(1, inserted("FIRST_INSPECT").size(), "不合格后应重新开一张首检单给现场重检");
    }

    /** 判定合格后按剩余量报工（回归）。 */
    @Test
    void passedInspectionAllowsRemainingReport() throws Exception {
        lenient().when(panelMapper.selectOne(any())).thenReturn(config("PROCESS_REPORT"));
        when(formMapper.selectOne(any())).thenReturn(mo());
        when(formMapper.selectList(any())).thenReturn(List.of(
                materialOut("已审核", 50),
                report("PX002", "车削", 1, 1, 0),
                judgedInspect("PX002", "车削", "合格", 1)));

        service.callButton("MANU_ORDER", "生成工序汇报单（自制汇报）", Map.of("编号", "MO-1"), Map.of());

        Map<?, ?> detail = json.readValue(inserted("PROCESS_REPORT").get(0).getDetailData(), Map.class);
        Map<?, ?> row = (Map<?, ?>) ((List<?>) detail.get("items")).get(0);
        assertEquals(9.0, ((Number) row.get("报工数量")).doubleValue(), 0.001);
    }

    /** 判定不合格不能让货入库。 */
    @Test
    void receivingRequiresPassedVerdict() throws Exception {
        lenient().when(panelMapper.selectOne(any())).thenReturn(config("FINISH_IN"));
        when(formMapper.selectOne(any())).thenReturn(mo());
        when(formMapper.selectList(any())).thenReturn(List.of(
                report("PX002", "车削", 10, 10, 0),
                report("PX004", "钻孔", 10, 10, 0),
                judgedInspect("PX002", "车削", "合格", 1),
                judgedInspect("PX004", "钻孔", "不合格", 1)));

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                service.callButton("MANU_ORDER", "生成产成品入库单", Map.of("编号", "MO-1"), Map.of()));

        assertTrue(e.getMessage().contains("不合格"), e.getMessage());
        verify(formMapper, never()).insert(any(FormData.class));
    }

    /** 填了判定就能提交：状态进「审批中」，不再是一步审核直通。 */
    @Test
    void verdictFilledSubmissionMovesToPending() throws Exception {
        lenient().when(panelMapper.selectOne(any())).thenReturn(config("FIRST_INSPECT"));
        FormData draft = document("FIRST_INSPECT", "SJ-1", "草稿",
                Map.of("单据编号", "SJ-1", "加工单号", "MO-1", "业务类型", "首件报检"),
                Map.of("items", List.of(Map.of("工序编码", "PX002", "工序名称", "车削",
                        "加工单号", "MO-1", "报检数量", 1, "检验判定", "合格"))));
        when(formMapper.selectOne(any())).thenReturn(draft);
        when(formMapper.selectList(any())).thenReturn(List.of(mo(), report("PX002", "车削", 1, 1, 0)));

        Map<String, Object> out = service.callButton("FIRST_INSPECT", "审核", Map.of("编号", "SJ-1"), Map.of());

        assertEquals("审批中", out.get("单据状态"));
        assertEquals("审批中", draft.getStatus());
    }

    /** 判定不合格必须写处置方式：不写就提交不了。 */
    @Test
    void failedInspectionRequiresDisposition() throws Exception {
        lenient().when(panelMapper.selectOne(any())).thenReturn(config("FIRST_INSPECT"));
        FormData draft = document("FIRST_INSPECT", "SJ-1", "草稿",
                Map.of("单据编号", "SJ-1", "加工单号", "MO-1", "业务类型", "首件报检"),
                Map.of("items", List.of(Map.of("工序编码", "PX002", "工序名称", "车削",
                        "加工单号", "MO-1", "报检数量", 1, "检验判定", "不合格"))));
        when(formMapper.selectOne(any())).thenReturn(draft);
        lenient().when(formMapper.selectList(any())).thenReturn(List.of(mo(), report("PX002", "车削", 1, 1, 0)));

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                service.callButton("FIRST_INSPECT", "审核", Map.of("编号", "SJ-1"), Map.of()));

        assertTrue(e.getMessage().contains("处置方式"), e.getMessage());
    }

    /** 处置返工：自动开一张重新首检单，并把原单号带上（联查能串起来）。 */
    @Test
    void reworkDispositionCreatesReinspectionWithSource() throws Exception {
        lenient().when(panelMapper.selectOne(any())).thenReturn(config("FIRST_INSPECT"));
        when(formMapper.selectOne(any())).thenReturn(mo());
        when(formMapper.selectList(any())).thenReturn(
                List.of(materialOut("已审核", 50), report("PX002", "车削", 1, 1, 0)),
                List.of(disposedInspect("PX002", "车削", "不合格", "返工", 10)));

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                service.callButton("MANU_ORDER", "生成工序汇报单（自制汇报）", Map.of("编号", "MO-1"), Map.of()));

        assertTrue(e.getMessage().contains("重新首检"), e.getMessage());
        List<FormData> inspects = inserted("FIRST_INSPECT");
        assertEquals(1, inspects.size());
        Map<?, ?> detail = json.readValue(inspects.get(0).getDetailData(), Map.class);
        Map<?, ?> row = (Map<?, ?>) ((List<?>) detail.get("items")).get(0);
        assertEquals("SJ-PX002", row.get("原首检单号"));
    }

    /** 处置报废：不再自动重检，提示走返工/补投，避免把报废件当合格品接着做。 */
    @Test
    void scrapDispositionBlocksReportingWithoutReinspection() throws Exception {
        lenient().when(panelMapper.selectOne(any())).thenReturn(config("FIRST_INSPECT"));
        when(formMapper.selectOne(any())).thenReturn(mo());
        when(formMapper.selectList(any())).thenReturn(
                List.of(materialOut("已审核", 50), report("PX002", "车削", 1, 1, 0)),
                List.of(disposedInspect("PX002", "车削", "不合格", "报废", 10)));

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                service.callButton("MANU_ORDER", "生成工序汇报单（自制汇报）", Map.of("编号", "MO-1"), Map.of()));

        assertTrue(e.getMessage().contains("报废"), e.getMessage());
        assertTrue(inserted("FIRST_INSPECT").isEmpty(), "报废不自动重检");
    }

    /** 报废数量要从未入库量里扣掉：100 计划 − 30 已入 − 5 报废 = 65。 */
    @Test
    void scrapReducesReceivableQuantity() throws Exception {
        FormData bigMo = document("MANU_ORDER", "MO-1", "已审核",
                Map.of("单据编号", "MO-1", "生产车间", "熔铸车间"),
                Map.of("products", List.of(Map.of("产品编码", "CP002", "产品名称", "轴套 C",
                        "生产单位", "件", "数量", 100, "累计入库数量", 30)), "processes", List.of()));
        when(formMapper.selectOne(any())).thenReturn(bigMo);
        when(formMapper.selectList(any())).thenReturn(List.of(
                disposedInspect("PX002", "车削", "不合格", "报废", 5)));

        List<Map<String, Object>> rows = service.finishInPreview("MO-1");

        assertEquals(5.0, ((Number) rows.get(0).get("报废数量")).doubleValue(), 0.001);
        assertEquals(65.0, ((Number) rows.get(0).get("剩余入库数量")).doubleValue(), 0.001);
    }

    // ───────── P0 门禁：成品报检验首检 / 报工验领料 / 返工单数量 ─────────

    private FormData materialOut(String status, double qty) throws Exception {
        return document("MATERIAL_OUT", "CL-1", status,
                Map.of("单据编号", "CL-1", "加工单号", "MO-1", "仓库", "原料仓"),
                Map.of("items", List.of(Map.of("加工单号", "MO-1", "材料编码", "M-1",
                        "材料名称", "铝锭", "数量", qty))));
    }

    /** 成品报检必须等全部工序首检通过：有一道不合格就不许报完工检验。 */
    @Test
    void finishInspectRequiresPassedFirstInspections() throws Exception {
        lenient().when(panelMapper.selectOne(any())).thenReturn(config("FINISH_INSPECT"));
        when(formMapper.selectOne(any())).thenReturn(mo());
        when(formMapper.selectList(any())).thenReturn(List.of(
                report("PX002", "车削", 10, 10, 0),
                report("PX004", "钻孔", 10, 10, 0),
                judgedInspect("PX002", "车削", "合格", 1),
                disposedInspect("PX004", "钻孔", "不合格", "返工", 2)));

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                service.callButton("MANU_ORDER", "生成成品报检单", Map.of("编号", "MO-1"), Map.of()));

        assertTrue(e.getMessage().contains("首检"), e.getMessage());
        assertTrue(e.getMessage().contains("钻孔"), e.getMessage());
        verify(formMapper, never()).insert(any(FormData.class));
    }

    /** 没领料不许报工：防止用"黑料"生产。 */
    @Test
    void reportingRequiresMaterialIssue() throws Exception {
        lenient().when(panelMapper.selectOne(any())).thenReturn(config("PROCESS_REPORT"));
        // 有需要人工领料的材料（不倒冲），才会要求先出库
        FormData moWithMaterial = document("MANU_ORDER", "MO-1", "已审核",
                Map.of("单据编号", "MO-1", "生产车间", "熔铸车间"),
                Map.of("products", List.of(Map.of("产品编码", "CP002", "产品名称", "轴套 C", "数量", 10)),
                        "materials", List.of(Map.of("材料编码", "CL001", "材料名称", "45#圆钢",
                                "材料倒冲方式", "不倒冲", "计划数量", 6.18, "累计领用数量", 0)),
                        "processes", List.of(Map.of("工序编码", "PX002", "工序名称", "车削", "计划数量", 10))));
        when(formMapper.selectOne(any())).thenReturn(moWithMaterial);
        when(formMapper.selectList(any())).thenReturn(List.of());

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                service.callButton("MANU_ORDER", "生成工序汇报单（自制汇报）", Map.of("编号", "MO-1"), Map.of()));

        assertTrue(e.getMessage().contains("材料出库"), e.getMessage());
        verify(formMapper, never()).insert(any(FormData.class));
    }

    /** 领料出库审核过就可以报工。 */
    @Test
    void reportingAllowedWithApprovedMaterialIssue() throws Exception {
        lenient().when(panelMapper.selectOne(any())).thenReturn(config("PROCESS_REPORT"), config("FIRST_INSPECT"));
        when(formMapper.selectOne(any())).thenReturn(mo());
        when(formMapper.selectList(any())).thenReturn(List.of(materialOut("已审核", 10)));

        service.callButton("MANU_ORDER", "生成工序汇报单（自制汇报）", Map.of("编号", "MO-1"), Map.of());

        assertEquals(1, inserted("PROCESS_REPORT").size());
    }

    /** 返工单数量取首检处置的报废数量，不是任何"累计报废入库数量"字段。 */
    @Test
    void reworkOrderQuantityComesFromScrapDisposition() throws Exception {
        lenient().when(panelMapper.selectOne(any())).thenReturn(config("MANU_ORDER"));
        when(formMapper.selectOne(any())).thenReturn(mo());
        when(formMapper.selectList(any())).thenReturn(List.of(
                disposedInspect("PX002", "车削", "不合格", "报废", 3)));

        service.callButton("MANU_ORDER", "生成返工生产加工单（按累计报废入库数量）",
                Map.of("编号", "MO-1"), Map.of());

        List<FormData> orders = inserted("MANU_ORDER");
        assertEquals(1, orders.size());
        Map<?, ?> detail = json.readValue(orders.get(0).getDetailData(), Map.class);
        Map<?, ?> product = (Map<?, ?>) ((List<?>) detail.get("products")).get(0);
        assertEquals(3.0, ((Number) product.get("数量")).doubleValue(), 0.001);
    }

    /** 没有报废数量就别开返工单。 */
    @Test
    void reworkOrderRefusedWithoutScrap() throws Exception {
        when(formMapper.selectOne(any())).thenReturn(mo());
        when(formMapper.selectList(any())).thenReturn(List.of());

        IllegalStateException e = assertThrows(IllegalStateException.class, () ->
                service.callButton("MANU_ORDER", "生成返工生产加工单（按累计报废入库数量）",
                        Map.of("编号", "MO-1"), Map.of()));

        assertTrue(e.getMessage().contains("报废"), e.getMessage());
    }
}
