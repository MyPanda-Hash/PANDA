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

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.doReturn;
import static org.mockito.Mockito.spy;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 分批报工（一天一批次）与默认合格数量。
 *
 * <p>要解决的问题：
 * <ul>
 *   <li>一张加工单要能分多次报工，每次只报「还没报完的量」；</li>
 *   <li>批次号按天生成（yyyyMMdd），同一天多次报工用同一批次；</li>
 *   <li>报工数量/检验数量已填时，合格数量默认等于它，省掉一次必填。</li>
 * </ul>
 */
@ExtendWith(MockitoExtension.class)
class PxServiceProcBatchTest {

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

    private PanelConfig config(String code) {
        PanelConfig cfg = new PanelConfig();
        cfg.setPanelCode(code);
        cfg.setPanelName(code);
        cfg.setConfig("{\"metadata\":{\"panelCode\":\"" + code
                + "\",\"autoCodeField\":\"单据编号\",\"panelButtons\":[],\"buttonGroups\":[],\"panelPageDto\":{}},"
                + "\"dataSchema\":{\"fields\":[]},\"detail\":{\"tabs\":[]}}");
        return cfg;
    }

    private FormData mo() throws Exception {
        return document("MANU_ORDER", "MO-1", "已审核",
                Map.of("单据编号", "MO-1", "销售订单号", "SO-1", "客户", "华东铝业", "生产车间", "熔铸车间"),
                Map.of(
                        "products", List.of(Map.of(
                                "产品编码", "CP001", "产品名称", "铝棒 Φ80", "规格型号", "Φ80×3000",
                                "生产单位", "件", "数量", 100)),
                        "processes", List.of(
                                Map.of("工序编码", "PX001", "工序名称", "下料", "计划数量", 100,
                                        "生产车间", "熔铸车间", "工作中心", "熔铸车间中心", "设备", "锯床-01",
                                        "班组", "下料班", "工人", "王强", "工序单位", "件"),
                                Map.of("工序编码", "PX002", "工序名称", "机加工", "计划数量", 100,
                                        "生产车间", "熔铸车间", "工作中心", "熔铸车间中心", "设备", "数控车床-01",
                                        "班组", "车工班", "工人", "李强", "工序单位", "件"))));
    }

    /** 首轮：没有历史汇报，默认按计划量报工，合格数默认等于报工数，并打当天批次。 */
    @Test
    void firstBatchDefaultsReportedAndQualified() throws Exception {
        PxService spied = spy(service);
        doReturn(Map.of()).when(spied).reportedQtyByProcess(anyString());
        when(panelMapper.selectOne(any())).thenReturn(config("PROCESS_REPORT"));
        when(formMapper.selectOne(any())).thenReturn(mo());
        when(formMapper.selectCount(any())).thenReturn(0L);

        spied.callButton("MANU_ORDER", "生成工序汇报单（自制汇报）", Map.of("编号", "MO-1"), Map.of());

        FormData target = captureInserted();
        Map<?, ?> head = json.readValue(target.getData(), Map.class);
        Map<?, ?> detail = json.readValue(target.getDetailData(), Map.class);
        List<?> items = (List<?>) detail.get("items");
        Map<?, ?> first = (Map<?, ?>) items.get(0);

        String batch = LocalDate.now().format(DateTimeFormatter.ofPattern("yyyyMMdd"));
        assertEquals(batch, String.valueOf(head.get("批次号")), "表头批次号应为当天日期");
        assertEquals(100.0, ((Number) first.get("报工数量")).doubleValue(), 0.001);
        assertEquals(100.0, ((Number) first.get("合格数量")).doubleValue(), 0.001, "合格数默认等于报工数");
        assertEquals(batch, String.valueOf(first.get("批次号")), "明细也要带批次号，便于追溯");
    }

    /** 第二轮：已有 40 报过，本次只应带剩余 60。 */
    @Test
    void secondBatchCarriesRemainingQuantityOnly() throws Exception {
        PxService spied = spy(service);
        doReturn(Map.of("PX001", 40.0, "PX002", 40.0)).when(spied).reportedQtyByProcess(anyString());
        when(panelMapper.selectOne(any())).thenReturn(config("PROCESS_REPORT"));
        when(formMapper.selectOne(any())).thenReturn(mo());
        when(formMapper.selectCount(any())).thenReturn(0L);

        spied.callButton("MANU_ORDER", "生成工序汇报单（自制汇报）", Map.of("编号", "MO-1"), Map.of());

        FormData target = captureInserted();
        Map<?, ?> detail = json.readValue(target.getDetailData(), Map.class);
        List<?> items = (List<?>) detail.get("items");
        Map<?, ?> first = (Map<?, ?>) items.get(0);
        assertEquals(60.0, ((Number) first.get("报工数量")).doubleValue(), 0.001, "只报剩余 60");
        assertEquals(60.0, ((Number) first.get("合格数量")).doubleValue(), 0.001);
        assertEquals(40.0, ((Number) first.get("累计汇报数量")).doubleValue(), 0.001);
        assertEquals(60.0, ((Number) first.get("可报工数量")).doubleValue(), 0.001);
    }

    /** 全部报完后不再产出新行。 */
    @Test
    void noLineLeftWhenFullyReported() throws Exception {
        PxService spied = spy(service);
        doReturn(Map.of("PX001", 100.0, "PX002", 100.0)).when(spied).reportedQtyByProcess(anyString());
        when(formMapper.selectOne(any())).thenReturn(mo());

        IllegalStateException e = org.junit.jupiter.api.Assertions.assertThrows(
                IllegalStateException.class,
                () -> spied.callButton("MANU_ORDER", "生成工序汇报单（自制汇报）", Map.of("编号", "MO-1"), Map.of()));
        assertTrue(e.getMessage().contains("已全部报工"), e.getMessage());
    }

    /** 检验单默认合格数 = 检验数量，默认判定合格，省掉一次必填。 */
    @Test
    void inspectionDefaultsQualifiedToInspected() throws Exception {
        when(panelMapper.selectOne(any())).thenReturn(config("PROCESS_INSPECTION"));
        FormData apply = document("PROCESS_INSPECT_APPLY", "GB-1", "已审核",
                Map.of("单据编号", "GB-1", "加工单号", "MO-1", "生产车间", "熔铸车间"),
                Map.of("items", List.of(Map.of(
                        "存货编码", "CP001", "存货名称", "铝棒 Φ80", "工序编码", "PX001",
                        "工序名称", "下料", "计量单位", "件", "报检数量", 60))));
        when(formMapper.selectOne(any())).thenReturn(apply);
        when(formMapper.selectCount(any())).thenReturn(0L);

        service.callButton("PROCESS_INSPECT_APPLY", "生成检验单", Map.of("编号", "GB-1"), Map.of());

        FormData target = captureInserted();
        Map<?, ?> detail = json.readValue(target.getDetailData(), Map.class);
        Map<?, ?> row = (Map<?, ?>) ((List<?>) detail.get("items")).get(0);
        assertEquals(60.0, ((Number) row.get("检验数量")).doubleValue(), 0.001);
        assertEquals(60.0, ((Number) row.get("合格数量")).doubleValue(), 0.001, "合格数默认等于检验数");
        assertEquals(0.0, ((Number) row.get("不合格数量")).doubleValue(), 0.001);
        assertEquals("合格", row.get("检验结果判定"));
    }

    private FormData captureInserted() {
        ArgumentCaptor<FormData> captor = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).insert(captor.capture());
        return captor.getValue();
    }
}