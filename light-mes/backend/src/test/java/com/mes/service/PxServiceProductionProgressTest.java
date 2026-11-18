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
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.LocalDateTime;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.atLeastOnce;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 生产进度回写：报工 → 工序累计汇报/完工状态；领料 → 材料累计领用。
 *
 * <p>采用「按已审核单据重算」而不是「累加」：弃审、作废后重算即可自动纠正，
 * 不会像累加那样越滚越多。
 */
@ExtendWith(MockitoExtension.class)
class PxServiceProductionProgressTest {

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

    @Test
    void processReportRollsUpToOrderProcesses() throws Exception {
        FormData mo = doc("MANU_ORDER", "MO-1", "已审核",
                Map.of("单据编号", "MO-1"),
                Map.of(
                        "products", List.of(Map.of("产品编码", "CP001", "产品名称", "铝棒", "数量", 100)),
                        "processes", List.of(
                                Map.of("产品编码", "CP001", "工序编码", "PX001", "工序名称", "下料",
                                        "计划数量", 100, "累计汇报数量", 0, "工序完工状态", "未完工"),
                                Map.of("产品编码", "CP001", "工序编码", "PX002", "工序名称", "机加工",
                                        "计划数量", 100, "累计汇报数量", 0, "工序完工状态", "未完工")),
                        "materials", List.of(
                                Map.of("材料编码", "CL001", "计划数量", 100, "累计领用数量", 0))));

        // 两张已审核汇报单：下料共 100（完工），机加工 40（未完工）；另有一张草稿不计
        FormData r1 = doc("PROCESS_REPORT", "GX-1", "已审核",
                Map.of("单据编号", "GX-1", "加工单号", "MO-1"),
                Map.of("items", List.of(
                        Map.of("产品编码", "CP001", "工序编码", "PX001", "报工数量", 60),
                        Map.of("产品编码", "CP001", "工序编码", "PX002", "报工数量", 40))));
        FormData r2 = doc("PROCESS_REPORT", "GX-2", "已审核",
                Map.of("单据编号", "GX-2", "加工单号", "MO-1"),
                Map.of("items", List.of(Map.of("产品编码", "CP001", "工序编码", "PX001", "报工数量", 40))));
        FormData draft = doc("PROCESS_REPORT", "GX-3", "草稿",
                Map.of("单据编号", "GX-3", "加工单号", "MO-1"),
                Map.of("items", List.of(Map.of("产品编码", "CP001", "工序编码", "PX002", "报工数量", 999))));

        when(formMapper.selectOne(any())).thenReturn(mo);
        when(formMapper.selectList(any())).thenReturn(List.of(r1, r2, draft));

        service.syncProductionProgress("MO-1");

        verify(formMapper, atLeastOnce()).updateById(any(FormData.class));
        // 校验写回的明细
        Map<?, ?> detail = json.readValue(mo.getDetailData(), Map.class);
        List<?> procs = (List<?>) detail.get("processes");
        Map<?, ?> px1 = (Map<?, ?>) procs.get(0);
        Map<?, ?> px2 = (Map<?, ?>) procs.get(1);
        assertEquals(100.0, ((Number) px1.get("累计汇报数量")).doubleValue(), 0.001, "下料累计 60+40");
        assertEquals("完工", px1.get("工序完工状态"));
        assertEquals(40.0, ((Number) px2.get("累计汇报数量")).doubleValue(), 0.001, "机加工只算已审核的 40");
        assertEquals("未完工", px2.get("工序完工状态"), "未达计划量不能算完工");
    }

    @Test
    void materialOutRollsUpToOrderMaterials() throws Exception {
        FormData mo = doc("MANU_ORDER", "MO-2", "已审核",
                Map.of("单据编号", "MO-2"),
                Map.of(
                        "products", List.of(Map.of("产品编码", "CP002", "数量", 10)),
                        "processes", List.of(),
                        "materials", List.of(
                                Map.of("材料编码", "CL001", "计划数量", 20, "累计领用数量", 0),
                                Map.of("材料编码", "CL004", "计划数量", 5, "累计领用数量", 0))));

        FormData out1 = doc("MATERIAL_OUT", "CL-1", "已审核",
                Map.of("单据编号", "CL-1", "加工单号", "MO-2"),
                Map.of("items", List.of(
                        Map.of("加工单号", "MO-2", "材料名称", "铝锭", "材料编码", "CL001", "数量", 12))));
        FormData out2 = doc("MATERIAL_OUT", "CL-2", "已审核",
                Map.of("单据编号", "CL-2", "加工单号", "MO-2"),
                Map.of("items", List.of(
                        Map.of("加工单号", "MO-2", "材料名称", "切削液", "材料编码", "CL004", "数量", 2),
                        Map.of("加工单号", "MO-2", "材料名称", "铝锭", "材料编码", "CL001", "数量", 3))));
        FormData cancelled = doc("MATERIAL_OUT", "CL-3", "已中止",
                Map.of("单据编号", "CL-3"),
                Map.of("items", List.of(Map.of("加工单号", "MO-2", "材料编码", "CL001", "数量", 999))));

        when(formMapper.selectOne(any())).thenReturn(mo);
        when(formMapper.selectList(any())).thenReturn(List.of(out1, out2, cancelled));

        service.syncProductionProgress("MO-2");

        Map<?, ?> detail = json.readValue(mo.getDetailData(), Map.class);
        List<?> mats = (List<?>) detail.get("materials");
        assertEquals(15.0, ((Number) ((Map<?, ?>) mats.get(0)).get("累计领用数量")).doubleValue(), 0.001, "12+3");
        assertEquals(2.0, ((Number) ((Map<?, ?>) mats.get(1)).get("累计领用数量")).doubleValue(), 0.001);
    }

    /** 首次报工记开工日期；工序全完工且产成品全部入库才记完工日期。 */
    @Test
    void stampsStartAndFinishDates() throws Exception {
        FormData mo = doc("MANU_ORDER", "MO-3", "已审核",
                Map.of("单据编号", "MO-3"),
                Map.of(
                        "products", List.of(Map.of("产品编码", "CP009", "数量", 5, "累计入库数量", 5)),
                        "processes", List.of(Map.of("产品编码", "CP009", "工序编码", "PX001",
                                "计划数量", 5, "累计汇报数量", 0, "工序完工状态", "未完工")),
                        "materials", List.of()));
        FormData rp = doc("PROCESS_REPORT", "GX-9", "已审核",
                Map.of("单据编号", "GX-9", "加工单号", "MO-3"),
                Map.of("items", List.of(Map.of("产品编码", "CP009", "工序编码", "PX001", "报工数量", 5))));

        when(formMapper.selectOne(any())).thenReturn(mo);
        when(formMapper.selectList(any())).thenReturn(List.of(rp));

        service.syncProductionProgress("MO-3");

        Map<?, ?> head = json.readValue(mo.getData(), Map.class);
        assertEquals(java.time.LocalDate.now().toString(), head.get("开工日期"), "首次报工应记开工日期");
        assertEquals(java.time.LocalDate.now().toString(), head.get("完工日期"), "工序完工且已全部入库应记完工日期");
    }

    /** 只报了一部分工，不能记完工日期。 */
    @Test
    void doesNotStampFinishWhenNotDone() throws Exception {
        FormData mo = doc("MANU_ORDER", "MO-4", "已审核",
                Map.of("单据编号", "MO-4"),
                Map.of(
                        "products", List.of(Map.of("产品编码", "CP010", "数量", 10, "累计入库数量", 0)),
                        "processes", List.of(Map.of("产品编码", "CP010", "工序编码", "PX001",
                                "计划数量", 10, "累计汇报数量", 0, "工序完工状态", "未完工")),
                        "materials", List.of()));
        FormData rp = doc("PROCESS_REPORT", "GX-10", "已审核",
                Map.of("单据编号", "GX-10", "加工单号", "MO-4"),
                Map.of("items", List.of(Map.of("产品编码", "CP010", "工序编码", "PX001", "报工数量", 4))));

        when(formMapper.selectOne(any())).thenReturn(mo);
        when(formMapper.selectList(any())).thenReturn(List.of(rp));

        service.syncProductionProgress("MO-4");

        Map<?, ?> head = json.readValue(mo.getData(), Map.class);
        assertEquals(java.time.LocalDate.now().toString(), head.get("开工日期"));
        org.junit.jupiter.api.Assertions.assertNull(head.get("完工日期"), "没做完不能记完工");
    }

    @Test
    void ignoresUnknownOrder() throws Exception {
        when(formMapper.selectOne(any())).thenReturn(null);
        service.syncProductionProgress("NOT-EXIST");
        verify(formMapper, org.mockito.Mockito.never()).updateById(any(FormData.class));
    }

    /**
     * 完工套数回写：产成品的「累计汇报套数(工序单位)」要等于末道工序的合格累计。
     *
     * <p>首件报检单/成品报检单审核时用这个字段当「累计完工量」上限；
     * 之前没人写它，报检数量永远大于 0 的完工量，首检单一审核就被拦死。
     */
    @Test
    void productFinishedSetsRollUpFromLastProcessQualified() throws Exception {
        FormData mo = doc("MANU_ORDER", "MO-5", "已审核",
                Map.of("单据编号", "MO-5"),
                Map.of(
                        "products", List.of(Map.of("产品编码", "CP011", "数量", 100, "累计汇报套数(工序单位)", 0)),
                        "processes", List.of(
                                Map.of("产品编码", "CP011", "工序编码", "PX001", "工序名称", "下料",
                                        "加工顺序", 1, "计划数量", 100, "累计汇报数量", 0, "工序完工状态", "未完工"),
                                Map.of("产品编码", "CP011", "工序编码", "PX002", "工序名称", "机加工",
                                        "加工顺序", 2, "计划数量", 100, "累计汇报数量", 0, "工序完工状态", "未完工")),
                        "materials", List.of()));

        FormData cut = doc("PROCESS_REPORT", "GX-51", "已审核",
                Map.of("单据编号", "GX-51", "加工单号", "MO-5"),
                Map.of("items", List.of(Map.of("产品编码", "CP011", "工序编码", "PX001",
                        "报工数量", 100, "合格数量", 100, "不合格数量", 0))));
        // 末道报了 100，其中 2 件不合格：能入库的是 98
        FormData machine = doc("PROCESS_REPORT", "GX-52", "已审核",
                Map.of("单据编号", "GX-52", "加工单号", "MO-5"),
                Map.of("items", List.of(Map.of("产品编码", "CP011", "工序编码", "PX002",
                        "报工数量", 100, "合格数量", 98, "不合格数量", 2))));

        when(formMapper.selectOne(any())).thenReturn(mo);
        when(formMapper.selectList(any())).thenReturn(List.of(cut, machine));

        service.syncProductionProgress("MO-5");

        Map<?, ?> detail = json.readValue(mo.getDetailData(), Map.class);
        Map<?, ?> product = (Map<?, ?>) ((List<?>) detail.get("products")).get(0);
        assertEquals(98.0, ((Number) product.get("累计汇报套数(工序单位)")).doubleValue(), 0.001,
                "完工套数按末道工序合格累计，不合格品不能算完工");
    }

    /** 报工开工 → 加工单状态推进到「生产中」，不再是停在已审核。 */
    @Test
    void orderBecomesInProductionAfterFirstReport() throws Exception {
        FormData mo = doc("MANU_ORDER", "MO-6", "已审核",
                Map.of("单据编号", "MO-6"),
                Map.of(
                        "products", List.of(Map.of("产品编码", "CP012", "数量", 10, "累计入库数量", 0)),
                        "processes", List.of(Map.of("产品编码", "CP012", "工序编码", "PX001", "加工顺序", 1,
                                "计划数量", 10, "累计汇报数量", 0, "工序完工状态", "未完工")),
                        "materials", List.of()));
        FormData rp = doc("PROCESS_REPORT", "GX-61", "已审核",
                Map.of("单据编号", "GX-61", "加工单号", "MO-6"),
                Map.of("items", List.of(Map.of("产品编码", "CP012", "工序编码", "PX001",
                        "报工数量", 4, "合格数量", 4, "不合格数量", 0))));

        when(formMapper.selectOne(any())).thenReturn(mo);
        when(formMapper.selectList(any())).thenReturn(List.of(rp));

        service.syncProductionProgress("MO-6");

        assertEquals("生产中", mo.getStatus(), "开过工就该显示生产中");
    }

    /** 工序全完工 + 产成品全部入库 → 加工单「已完工」。 */
    @Test
    void orderBecomesFinishedWhenAllProcessesDoneAndAllReceived() throws Exception {
        FormData mo = doc("MANU_ORDER", "MO-7", "生产中",
                Map.of("单据编号", "MO-7"),
                Map.of(
                        "products", List.of(Map.of("产品编码", "CP013", "数量", 10, "累计入库数量", 10)),
                        "processes", List.of(Map.of("产品编码", "CP013", "工序编码", "PX001", "加工顺序", 1,
                                "计划数量", 10, "累计汇报数量", 0, "工序完工状态", "未完工")),
                        "materials", List.of()));
        FormData rp = doc("PROCESS_REPORT", "GX-71", "已审核",
                Map.of("单据编号", "GX-71", "加工单号", "MO-7"),
                Map.of("items", List.of(Map.of("产品编码", "CP013", "工序编码", "PX001",
                        "报工数量", 10, "合格数量", 10, "不合格数量", 0))));

        when(formMapper.selectOne(any())).thenReturn(mo);
        when(formMapper.selectList(any())).thenReturn(List.of(rp));

        service.syncProductionProgress("MO-7");

        assertEquals("已完工", mo.getStatus());
    }

    /**
     * 第一道工序一排产就回填开工日期（只读字段，由系统写；已有值不覆盖）。
     */
    @Test
    void firstPlanBatchFillsStartDate() throws Exception {
        FormData mo = doc("MANU_ORDER", "MO-PLAN-1", "已审核",
                new HashMap<>(Map.of("单据编号", "MO-PLAN-1")),
                new HashMap<>(Map.of("processes", List.of())));

        assertTrue(service.fillStartDateIfEmpty(mo, "2026-11-10"));
        Map<?, ?> head = json.readValue(mo.getData(), Map.class);
        assertEquals("2026-11-10", head.get("开工日期"), "排产首个批次时用批次预开工日回填");
    }

    @Test
    void startDateIsNotOverwritten() throws Exception {
        FormData mo = doc("MANU_ORDER", "MO-PLAN-2", "生产中",
                new HashMap<>(Map.of("单据编号", "MO-PLAN-2", "开工日期", "2026-11-01")),
                new HashMap<>(Map.of("processes", List.of())));

        assertFalse(service.fillStartDateIfEmpty(mo, "2026-11-10"), "已有开工日期不再改");
        Map<?, ?> head = json.readValue(mo.getData(), Map.class);
        assertEquals("2026-11-01", head.get("开工日期"));
    }
}
