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

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.when;

/**
 * 齐套检查：按当前库存推算这张加工单能开几件。
 *
 * <p>齐套数量 = min(计划数量, 各材料 floor(可用库存 / 单件用量))；
 * 单件用量取「定额需用数量 / 定额生产数量」。
 */
@ExtendWith(MockitoExtension.class)
class PxServiceKittingTest {

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

    private FormData mo() throws Exception {
        FormData d = new FormData();
        d.setId(1L);
        d.setPanelCode("MANU_ORDER");
        d.setFormNo("MO-1");
        d.setStatus("已审核");
        d.setData(json.writeValueAsString(Map.of("单据编号", "MO-1")));
        d.setDetailData(json.writeValueAsString(Map.of(
                "products", List.of(Map.of("产品编码", "CP001", "产品名称", "散热片", "数量", 20, "齐套数量(主)", 0)),
                "processes", List.of(),
                "materials", List.of(
                        Map.of("材料编码", "CL003", "材料名称", "铝棒", "定额生产数量", 1,
                                "定额需用数量", 1.1, "需用数量", 22, "计划数量", 24.2),
                        Map.of("材料编码", "CL005", "材料名称", "木箱", "定额生产数量", 1,
                                "定额需用数量", 0.02, "需用数量", 0.4, "计划数量", 0.4)))));
        d.setCreateTime(LocalDateTime.now());
        return d;
    }

    @Test
    void computesKittingFromStock() throws Exception {
        FormData mo = mo();
        when(formMapper.selectOne(any())).thenReturn(mo);
        // 铝棒 582.2kg / 1.1 = 529 件；木箱 296.4 / 0.02 = 14820 件 → 瓶颈在计划量 20
        when(stockTxnService.quantityOf(anyString(), anyString(), any()))
                .thenReturn(new BigDecimal("582.2"))
                .thenReturn(new BigDecimal("296.4"));

        PxService.KittingResult r = service.refreshKitting("MO-1");

        assertEquals(20, r.kitting(), "料够时应齐套到计划量");
        assertEquals(0, r.shortageCount(), "无缺料材料");
        Map<?, ?> detail = json.readValue(mo.getDetailData(), Map.class);
        Map<?, ?> product = (Map<?, ?>) ((List<?>) detail.get("products")).get(0);
        assertEquals(20.0, ((Number) product.get("齐套数量(主)")).doubleValue(), 0.001);
    }

    @Test
    void kittingLimitedByShortMaterial() throws Exception {
        FormData mo = mo();
        when(formMapper.selectOne(any())).thenReturn(mo);
        // 铝棒只有 11kg → 11 / 1.1 = 10 件；木箱充足
        when(stockTxnService.quantityOf(anyString(), anyString(), any()))
                .thenReturn(new BigDecimal("11"))
                .thenReturn(new BigDecimal("296.4"));

        PxService.KittingResult r = service.refreshKitting("MO-1");

        assertEquals(10, r.kitting(), "瓶颈材料决定齐套数量");
        assertEquals(1, r.shortageCount());
        assertTrue(r.message().contains("CL003") || r.message().contains("铝棒"), r.message());
        assertTrue(r.message().contains("10"), r.message());
    }

    @Test
    void zeroStockMeansNoKitting() throws Exception {
        FormData mo = mo();
        when(formMapper.selectOne(any())).thenReturn(mo);
        lenient().when(stockTxnService.quantityOf(anyString(), anyString(), any())).thenReturn(BigDecimal.ZERO);

        PxService.KittingResult r = service.refreshKitting("MO-1");
        assertEquals(0, r.kitting());
        assertTrue(r.message().contains("0"));
    }

    @Test
    void unknownOrderReturnsZero() {
        when(formMapper.selectOne(any())).thenReturn(null);
        PxService.KittingResult r = service.refreshKitting("NOT-EXIST");
        assertEquals(0, r.kitting());
    }
}
