package com.mes.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.PanelConfig;
import com.mes.mapper.PanelConfigMapper;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 库存台账的合计口径：只合计金额（收入/发出），不合计数量、单价和结存。
 *
 * <p>台账一张表里混着 kg、件、升，数量相加没有业务意义；单价相加同样没有意义；
 * 结存是余额列，逐行相加会重复计算。
 */
class ReportPanelRegistrySummaryTest {

    @Test
    void stockLedgerOnlySumsFlowAmounts() throws Exception {
        PanelConfigMapper mapper = mock(PanelConfigMapper.class);
        ObjectMapper json = new ObjectMapper();
        when(mapper.selectOne(any())).thenReturn(null);
        ReportPanelRegistry registry = new ReportPanelRegistry(mapper, json);

        registry.syncPanelConfigs();

        ArgumentCaptor<PanelConfig> captor = ArgumentCaptor.forClass(PanelConfig.class);
        verify(mapper, org.mockito.Mockito.atLeastOnce()).insert(captor.capture());
        PanelConfig ledger = captor.getAllValues().stream()
                .filter((c) -> "STOCK_LEDGER".equals(c.getPanelCode()))
                .findFirst().orElseThrow();

        Map<?, ?> config = json.readValue(ledger.getConfig(), Map.class);
        Map<?, ?> metadata = (Map<?, ?>) config.get("metadata");
        Map<?, ?> pageDto = (Map<?, ?>) metadata.get("panelPageDto");
        List<?> pages = (List<?>) pageDto.get("tablePages");
        Map<?, ?> grid = (Map<?, ?>) ((List<?>) ((Map<?, ?>) pages.get(0)).get("gridTabs")).get(0);
        Object summary = grid.get("summaryColumns");

        assertTrue(summary instanceof List<?>, "库存台账必须声明 summaryColumns");
        assertTrue(((List<?>) summary).containsAll(List.of("收入金额", "发出金额")), String.valueOf(summary));
        assertTrue(!((List<?>) summary).contains("收入数量"), "数量不能合计：" + summary);
        assertTrue(!((List<?>) summary).contains("结存金额"), "结存是余额列，逐行相加会重复计算：" + summary);
    }
}
