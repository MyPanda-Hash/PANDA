package com.mes.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.entity.PanelConfig;
import com.mes.mapper.FormApprovalMapper;
import com.mes.mapper.FormDataMapper;
import com.mes.mapper.PanelConfigMapper;
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
import static org.mockito.Mockito.lenient;

/**
 * 工序报检单的上游对齐校验。
 *
 * 工序报检的上游是「工序汇报单」，不是加工单：
 * 报检数量必须按工序逐项比对报工数量，超量说明报了还没做出来的货。
 *
 * 实际缺陷参考：GX-2026-11-0201 的 PX001/PX003 各报工 340，
 * 若报检单对同一工序报 500，就应该在审核时被拦下。
 */
@ExtendWith(MockitoExtension.class)
class PxServiceProcessInspectTest {

    @Mock private PanelConfigMapper panelMapper;
    @Mock private FormDataMapper formMapper;
    @Mock private FormApprovalMapper approvalMapper;
    @Mock private ReportQueryService reportQueryService;

    private final ObjectMapper json = new ObjectMapper();
    private PxService service;

    @BeforeEach
    void setUp() {
        service = new PxService(panelMapper, formMapper, approvalMapper, reportQueryService);
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

    private PanelConfig config(String code, String name) {
        PanelConfig cfg = new PanelConfig();
        cfg.setPanelCode(code);
        cfg.setPanelName(name);
        cfg.setConfig("{\"metadata\":{\"panelCode\":\"" + code
                + "\",\"autoCodeField\":\"单据编号\",\"panelButtons\":[],\"buttonGroups\":[],\"panelPageDto\":{}},"
                + "\"dataSchema\":{\"fields\":[]},\"detail\":{\"tabs\":[]}}");
        return cfg;
    }

    /** 报检数量超过同一工序的报工数量，必须拦下。 */
    @Test
    void rejectsProcessInspectBeyondReportedQuantity() throws Exception {
        FormData apply = document("PROCESS_INSPECT_APPLY", "GB-001", "草稿",
                Map.of("单据编号", "GB-001", "业务类型", "工序报检",
                        "加工单号", "MO-001", "来源单号", "GX-001"),
                Map.of("items", List.of(Map.of(
                        "存货编码", "CP001", "存货名称", "铝棒 Φ80",
                        "工序编码", "PX001", "工序名称", "下料",
                        "报检数量", 500, "累计检验数量", 0))));
        lenient().when(formMapper.selectOne(any())).thenReturn(apply);
        lenient().when(panelMapper.selectOne(any())).thenReturn(
                config("PROCESS_INSPECT_APPLY", "工序报检单"));

        // 上游汇报单同一工序只报了 340
        FormData report = document("PROCESS_REPORT", "GX-001", "已审核",
                Map.of("单据编号", "GX-001", "加工单号", "MO-001"),
                Map.of("items", List.of(Map.of(
                        "工序编码", "PX001", "工序名称", "下料",
                        "报工数量", 340, "合格数量", 330, "不合格数量", 10))));
        lenient().when(formMapper.selectList(any())).thenReturn(List.of(report));

        IllegalStateException error = assertThrows(IllegalStateException.class,
                () -> service.callButton("PROCESS_INSPECT_APPLY", "审核", Map.of("编号", "GB-001"), Map.of()));

        assertTrue(error.getMessage().contains("报工数量"),
                "错误信息应说明超过报工数量：" + error.getMessage());
    }

    /** 报检的工序必须在上游汇报单里出现。 */
    @Test
    void rejectsProcessInspectWithUnknownProcess() throws Exception {
        FormData apply = document("PROCESS_INSPECT_APPLY", "GB-002", "草稿",
                Map.of("单据编号", "GB-002", "业务类型", "工序报检",
                        "加工单号", "MO-001", "来源单号", "GX-001"),
                Map.of("items", List.of(Map.of(
                        "存货编码", "CP001", "存货名称", "铝棒 Φ80",
                        "工序编码", "PX999", "工序名称", "不存在的工序",
                        "报检数量", 10, "累计检验数量", 0))));
        lenient().when(formMapper.selectOne(any())).thenReturn(apply);
        lenient().when(panelMapper.selectOne(any())).thenReturn(
                config("PROCESS_INSPECT_APPLY", "工序报检单"));

        FormData report = document("PROCESS_REPORT", "GX-001", "已审核",
                Map.of("单据编号", "GX-001", "加工单号", "MO-001"),
                Map.of("items", List.of(Map.of(
                        "工序编码", "PX001", "工序名称", "下料",
                        "报工数量", 340, "合格数量", 330, "不合格数量", 10))));
        lenient().when(formMapper.selectList(any())).thenReturn(List.of(report));

        IllegalStateException error = assertThrows(IllegalStateException.class,
                () -> service.callButton("PROCESS_INSPECT_APPLY", "审核", Map.of("编号", "GB-002"), Map.of()));

        assertTrue(error.getMessage().contains("PX999") || error.getMessage().contains("工序"),
                "错误信息应说明工序不在汇报单内：" + error.getMessage());
    }

    /** 数量不超报工量时不应被拦（合法场景必须能过）。 */
    @Test
    void allowsProcessInspectWithinReportedQuantity() throws Exception {
        FormData apply = document("PROCESS_INSPECT_APPLY", "GB-003", "草稿",
                Map.of("单据编号", "GB-003", "业务类型", "工序报检",
                        "加工单号", "MO-001", "来源单号", "GX-001"),
                Map.of("items", List.of(Map.of(
                        "存货编码", "CP001", "存货名称", "铝棒 Φ80",
                        "工序编码", "PX001", "工序名称", "下料",
                        "报检数量", 330, "累计检验数量", 0))));
        lenient().when(formMapper.selectOne(any())).thenReturn(apply);
        lenient().when(panelMapper.selectOne(any())).thenReturn(
                config("PROCESS_INSPECT_APPLY", "工序报检单"));

        FormData report = document("PROCESS_REPORT", "GX-001", "已审核",
                Map.of("单据编号", "GX-001", "加工单号", "MO-001"),
                Map.of("items", List.of(Map.of(
                        "工序编码", "PX001", "工序名称", "下料",
                        "报工数量", 340, "合格数量", 330, "不合格数量", 10))));
        lenient().when(formMapper.selectList(any())).thenReturn(List.of(report));

        // 不抛异常即为通过；异常会直接让测试失败
        service.callButton("PROCESS_INSPECT_APPLY", "审核", Map.of("编号", "GB-003"), Map.of());
    }

    /**
     * 报检的产品必须在来源工序汇报单的产品明细里。
     * 否则会出现「报了不属于这单的货」，与工序、数量校验形成完整闭环。
     */
    @Test
    void rejectsProcessInspectWithProductNotInReport() throws Exception {
        FormData apply = document("PROCESS_INSPECT_APPLY", "GB-004", "草稿",
                Map.of("单据编号", "GB-004", "业务类型", "工序报检",
                        "加工单号", "MO-001", "来源单号", "GX-001"),
                Map.of("items", List.of(Map.of(
                        "存货编码", "CP999", "存货名称", "不属于本单的货",
                        "工序编码", "PX001", "工序名称", "下料",
                        "报检数量", 10, "累计检验数量", 0))));
        lenient().when(formMapper.selectOne(any())).thenReturn(apply);
        lenient().when(panelMapper.selectOne(any())).thenReturn(
                config("PROCESS_INSPECT_APPLY", "工序报检单"));

        // 上游汇报单只生产 CP001
        FormData report = document("PROCESS_REPORT", "GX-001", "已审核",
                Map.of("单据编号", "GX-001", "加工单号", "MO-001"),
                Map.of("items", List.of(Map.of(
                        "产品编码", "CP001", "产品名称", "铝棒 Φ80",
                        "工序编码", "PX001", "工序名称", "下料",
                        "报工数量", 340, "合格数量", 330, "不合格数量", 10))));
        lenient().when(formMapper.selectList(any())).thenReturn(List.of(report));

        IllegalStateException error = assertThrows(IllegalStateException.class,
                () -> service.callButton("PROCESS_INSPECT_APPLY", "审核", Map.of("编号", "GB-004"), Map.of()));

        assertTrue(error.getMessage().contains("CP999"),
                "错误信息应指出不在单内的存货编码：" + error.getMessage());
        assertTrue(error.getMessage().contains("产品") || error.getMessage().contains("存货"),
                "错误信息应说明是产品归属问题：" + error.getMessage());
    }

    /**
     * 工序在汇报单里但报工数量为 0（该工序尚未开工），提示要区别于"超过报工量"。
     * 用户看到"超过报工数量 0"并不知道是自己根本没报工还是数量填错。
     */
    @Test
    void explainsProcessNotStartedYetWhenReportedQuantityIsZero() throws Exception {
        FormData apply = document("PROCESS_INSPECT_APPLY", "GB-006", "草稿",
                Map.of("单据编号", "GB-006", "业务类型", "工序报检",
                        "加工单号", "MO-001", "来源单号", "GX-001"),
                Map.of("items", List.of(Map.of(
                        "存货编码", "CP001", "存货名称", "铝棒 Φ80",
                        "工序编码", "PX003", "工序名称", "精车",
                        "报检数量", 10, "累计检验数量", 0))));
        lenient().when(formMapper.selectOne(any())).thenReturn(apply);
        lenient().when(panelMapper.selectOne(any())).thenReturn(
                config("PROCESS_INSPECT_APPLY", "工序报检单"));

        // 该工序登记了但还没报工
        FormData report = document("PROCESS_REPORT", "GX-001", "已审核",
                Map.of("单据编号", "GX-001", "加工单号", "MO-001"),
                Map.of("items", List.of(
                        Map.of("产品编码", "CP001", "产品名称", "铝棒 Φ80",
                                "工序编码", "PX001", "工序名称", "下料",
                                "报工数量", 340, "合格数量", 330, "不合格数量", 10),
                        Map.of("产品编码", "CP001", "产品名称", "铝棒 Φ80",
                                "工序编码", "PX003", "工序名称", "精车",
                                "报工数量", 0, "合格数量", 0, "不合格数量", 0))));
        lenient().when(formMapper.selectList(any())).thenReturn(List.of(report));

        IllegalStateException error = assertThrows(IllegalStateException.class,
                () -> service.callButton("PROCESS_INSPECT_APPLY", "审核", Map.of("编号", "GB-006"), Map.of()));

        assertTrue(error.getMessage().contains("尚未报工"),
                "报工量为 0 时应明确提示该工序尚未报工：" + error.getMessage());
        assertTrue(error.getMessage().contains("PX003"),
                "错误信息应指出具体工序编码：" + error.getMessage());
        assertTrue(!error.getMessage().contains("超过"),
                "报工量为 0 时不应再用「超过报工数量」的说法：" + error.getMessage());
    }

    /**
     * 保存时就该说清「尚未报工」：这类报检单多半是选单/带出来的，
     * 保存阶段撞到的是来源可执行量为 0，提示必须点明是工序没报工，
     * 而不是让用户去猜「来源明细超量：可执行 0.0，本次 10.0」。
     */
    @Test
    void explainsNotReportedYetWhenSourceReportLineHasZeroQuantity() {
        Map<String, Object> sourceRow = Map.of(
                "产品编码", "CP001", "产品名称", "铝棒 Φ80",
                "工序编码", "PX003", "工序名称", "精车",
                "报工数量", 0, "合格数量", 0, "不合格数量", 0);

        String hint = PxService.unReportedProcessMessage("PROCESS_REPORT", sourceRow, "GX-001", 1);

        assertTrue(hint != null && hint.contains("尚未报工"),
                "报工量为 0 时应给出尚未报工的专门提示：" + hint);
        assertTrue(hint.contains("PX003"), "提示应带工序编码：" + hint);
        assertTrue(hint.contains("GX-001"), "提示应带来源单号：" + hint);
        assertTrue(!hint.contains("超量"), "不应退回笼统的超量提示：" + hint);
    }

    /** 报工量大于 0 时不该走「尚未报工」分支，仍由通用超量校验处理。 */
    @Test
    void givesNoShortcutWhenSourceReportLineHasQuantity() {
        Map<String, Object> sourceRow = Map.of(
                "工序编码", "PX003", "工序名称", "精车",
                "报工数量", 340, "合格数量", 330, "不合格数量", 10);

        assertTrue(PxService.unReportedProcessMessage("PROCESS_REPORT", sourceRow, "GX-001", 1) == null,
                "报工量正常时不该抛专门提示");
        assertTrue(PxService.unReportedProcessMessage("PURCHASE_ORDER",
                        Map.of("数量", 0), "CG-001", 1) == null,
                "非工序汇报类来源不该套用该提示");
    }

    /** 报检产品与汇报单一致时应放行。 */
    @Test
    void allowsProcessInspectWithMatchingProduct() throws Exception {
        FormData apply = document("PROCESS_INSPECT_APPLY", "GB-005", "草稿",
                Map.of("单据编号", "GB-005", "业务类型", "工序报检",
                        "加工单号", "MO-001", "来源单号", "GX-001"),
                Map.of("items", List.of(Map.of(
                        "存货编码", "CP001", "存货名称", "铝棒 Φ80",
                        "工序编码", "PX001", "工序名称", "下料",
                        "报检数量", 330, "累计检验数量", 0))));
        lenient().when(formMapper.selectOne(any())).thenReturn(apply);
        lenient().when(panelMapper.selectOne(any())).thenReturn(
                config("PROCESS_INSPECT_APPLY", "工序报检单"));

        FormData report = document("PROCESS_REPORT", "GX-001", "已审核",
                Map.of("单据编号", "GX-001", "加工单号", "MO-001"),
                Map.of("items", List.of(Map.of(
                        "产品编码", "CP001", "产品名称", "铝棒 Φ80",
                        "工序编码", "PX001", "工序名称", "下料",
                        "报工数量", 340, "合格数量", 330, "不合格数量", 10))));
        lenient().when(formMapper.selectList(any())).thenReturn(List.of(report));

        service.callButton("PROCESS_INSPECT_APPLY", "审核", Map.of("编号", "GB-005"), Map.of());
    }
}