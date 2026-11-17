package com.mes.service;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.mapper.FormApprovalMapper;
import com.mes.mapper.FormDataMapper;
import com.mes.mapper.PanelConfigMapper;
import com.mes.panel.PanelActionRegistry;

/**
 * 加工单「选销售订单」弹窗的候选列表：已审核订单 + 排产进度 + 顶部筛选。
 *
 * <p>计划员在这张弹窗里要一眼看出「哪张订单还排得动」，所以每行要带
 * 订单数量 / 已排数量 / 可排数量 / 排产状态，并且能按单据日期与客户名筛。
 */
@ExtendWith(MockitoExtension.class)
class SaleOrderPlanCandidatesTest {

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

    private FormData saleOrder(String no, String date, String customer,
            List<Map<String, Object>> items) throws Exception {
        return doc("SO_ORDER", no, "已审核",
                Map.of("单据编号", no, "单据日期", date, "客户", customer),
                Map.of("items", items));
    }

    private Map<String, Object> item(String code, double qty) {
        return Map.of("存货编码", code, "存货名称", code + "-名称", "数量", qty, "销售单位", "件");
    }

    private FormData manuOrder(String soNo, Integer lineNo, String code, double qty) throws Exception {
        Map<String, Object> head = new java.util.LinkedHashMap<>();
        head.put("销售订单号", soNo);
        if (lineNo != null) head.put(SaleOrderPlanRules.LINE_FIELD, lineNo);
        return doc("MANU_ORDER", soNo + "-1", "已审核", head,
                Map.of("products", List.of(Map.of("产品编码", code, "数量", qty))));
    }

    /**
     * 同一个 mapper 先查销售订单、再查加工单（占用），按调用次序分流。
     *
     * <p>单元测试里没有 MyBatis 的 lambda 缓存，包装器拼不出 SQL，也没法按参数区分两条查询；
     * 而 {@code saleOrderPlanCandidates} 的第一次查询必然是候选订单、之后都是占用查询，
     * 所以这里按次序给数据（实现改成别的查询顺序时，这个桩会立刻失效暴露出来）。
     */
    private void stubMappers(List<FormData> saleOrders, List<FormData> manuOrders) {
        java.util.concurrent.atomic.AtomicInteger calls = new java.util.concurrent.atomic.AtomicInteger();
        when(formMapper.selectList(any(LambdaQueryWrapper.class)))
                .thenAnswer(inv -> calls.getAndIncrement() == 0 ? saleOrders : manuOrders);
    }

    @Test
    void marksFullyPlannedOrderAs已排产() throws Exception {
        FormData so1 = saleOrder("SO-1", "2026-11-12", "南方重工",
                List.of(item("CP001", 10), item("CP002", 5)));
        FormData so2 = saleOrder("SO-2", "2026-11-13", "华东铝业", List.of(item("CP001", 10)));
        stubMappers(List.of(so1, so2), List.of(
                manuOrder("SO-1", 1, "CP001", 10),
                manuOrder("SO-1", 2, "CP002", 5)));

        List<Map<String, Object>> rows = service.saleOrderPlanCandidates(null, null, null);

        assertEquals(2, rows.size());
        Map<String, Object> planned = rows.stream().filter(r -> "SO-1".equals(r.get("单据编号"))).findFirst().orElseThrow();
        assertEquals("已排产", planned.get("排产状态"));
        assertEquals(0.0, ((Number) planned.get("可排数量")).doubleValue(), 0.001);
        assertEquals(15.0, ((Number) planned.get("订单数量")).doubleValue(), 0.001);
        assertEquals(15.0, ((Number) planned.get("已排数量")).doubleValue(), 0.001);

        Map<String, Object> idle = rows.stream().filter(r -> "SO-2".equals(r.get("单据编号"))).findFirst().orElseThrow();
        assertEquals("未排产", idle.get("排产状态"));
        assertEquals(10.0, ((Number) idle.get("可排数量")).doubleValue(), 0.001);
    }

    @Test
    void partlyPlannedShowsRemaining() throws Exception {
        FormData so = saleOrder("SO-1", "2026-11-12", "南方重工",
                List.of(item("CP001", 10), item("CP002", 5)));
        stubMappers(List.of(so), List.of(manuOrder("SO-1", 1, "CP001", 4)));

        Map<String, Object> row = service.saleOrderPlanCandidates(null, null, null).get(0);

        assertEquals("部分排产", row.get("排产状态"));
        assertEquals(4.0, ((Number) row.get("已排数量")).doubleValue(), 0.001);
        assertEquals(11.0, ((Number) row.get("可排数量")).doubleValue(), 0.001);
    }

    @Test
    void filtersByDateAndCustomer() throws Exception {
        FormData so1 = saleOrder("SO-1", "2026-11-12", "南方重工", List.of(item("CP001", 10)));
        FormData so2 = saleOrder("SO-2", "2026-11-13", "华东铝业", List.of(item("CP002", 6)));
        // 桩按调用次序给数据，每次查询前重新打一次桩（每次调用自己从头计数）
        stubMappers(List.of(so1, so2), List.of());
        List<Map<String, Object>> byDate = service.saleOrderPlanCandidates("2026-11-13", null, null);
        assertEquals(1, byDate.size());
        assertEquals("SO-2", byDate.get(0).get("单据编号"));

        stubMappers(List.of(so1, so2), List.of());
        List<Map<String, Object>> byCustomer = service.saleOrderPlanCandidates(null, null, "南方");
        assertEquals(1, byCustomer.size());
        assertEquals("SO-1", byCustomer.get(0).get("单据编号"));

        stubMappers(List.of(so1, so2), List.of());
        List<Map<String, Object>> all = service.saleOrderPlanCandidates(null, null, null);
        assertEquals(2, all.size());
        assertTrue(all.stream().allMatch(r -> r.get("明细") instanceof List));
    }

    @Test
    void skipsOrdersWithoutItems() throws Exception {
        FormData empty = saleOrder("SO-9", "2026-11-12", "南方重工", List.of());
        stubMappers(List.of(empty), List.of());
        assertTrue(service.saleOrderPlanCandidates(null, null, null).isEmpty());
    }
}
