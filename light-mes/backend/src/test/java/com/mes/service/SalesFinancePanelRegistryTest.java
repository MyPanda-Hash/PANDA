package com.mes.service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.PanelConfig;
import com.mes.mapper.PanelConfigMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.List;
import java.util.Map;
import java.util.function.Function;
import java.util.stream.Collectors;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;

@ExtendWith(MockitoExtension.class)
class SalesFinancePanelRegistryTest {

    @Mock private PanelConfigMapper panelMapper;

    private final ObjectMapper json = new ObjectMapper();

    @Test
    void registersCompleteSalesFinancePanelContracts() throws Exception {
        new SalesFinancePanelRegistry(panelMapper, json).syncPanelConfigs();

        ArgumentCaptor<PanelConfig> inserted = ArgumentCaptor.forClass(PanelConfig.class);
        verify(panelMapper, times(3)).insert(inserted.capture());
        Map<String, PanelConfig> configs = inserted.getAllValues().stream()
                .collect(Collectors.toMap(PanelConfig::getPanelCode, Function.identity()));

        assertEquals(List.of("EXPENSE", "SALE_COST_ALLOC", "SALE_INVOICE"),
                configs.keySet().stream().sorted().toList());
        assertPanel(configs.get("SALE_INVOICE"), "销售发票", "价税合计", "选销货单");
        assertPanel(configs.get("EXPENSE"), "费用单", "含税金额", null);
        assertPanel(configs.get("SALE_COST_ALLOC"), "销售费用分摊单", "分摊金额", "选费用单");

        Map<String, Object> expense = parse(configs.get("EXPENSE"));
        List<String> generateActions = groups(expense).stream()
                .filter(group -> "生单".equals(group.get("name")))
                .flatMap(group -> strings(group.get("actions")).stream()).toList();
        assertEquals(List.of("生成销售费用分摊单", "生成采购费用分摊单"), generateActions);

        Map<String, Object> allocation = parse(configs.get("SALE_COST_ALLOC"));
        Map<String, Object> select = map(map(allocation.get("selectConfigs")).get("选费用单"));
        assertEquals("EXPENSE", select.get("source"));
        assertEquals("销售费用", map(select.get("condition")).get("费用类型"));
        assertEquals("金额", select.get("sourceQuantityField"));
        assertEquals("分摊金额", select.get("targetQuantityField"));
    }

    /**
     * 费用分摊闭环的字段与按钮必须写进 SF2.0 模板本身。
     *
     * <p>此前这些是 `SaleCostLoopRegistry` 在启动时往旧配置上补的，而 SF2.0 每次启动都会用模板
     * 整体重建这三个面板——两个 @PostConstruct 的执行顺序不确定，模板一旦后跑就把补丁抹掉，
     * 表现为分摊单上既没有「分摊状态」字段、也没有「执行分摊」按钮（用户根本没法分摊）。
     */
    @Test
    void keepsCostAllocationLoopFieldsAndButton() throws Exception {
        new SalesFinancePanelRegistry(panelMapper, json).syncPanelConfigs();

        ArgumentCaptor<PanelConfig> inserted = ArgumentCaptor.forClass(PanelConfig.class);
        verify(panelMapper, times(3)).insert(inserted.capture());
        Map<String, PanelConfig> configs = inserted.getAllValues().stream()
                .collect(Collectors.toMap(PanelConfig::getPanelCode, Function.identity()));

        Map<String, Object> alloc = parse(configs.get("SALE_COST_ALLOC"));
        List<String> allocFields = headFields(alloc);
        assertTrue(allocFields.contains("分摊状态"), "分摊单要有分摊状态字段：" + allocFields);
        assertTrue(allocFields.contains("执行时间"), "分摊单要有执行时间字段：" + allocFields);
        List<String> toolActions = groups(alloc).stream()
                .filter(group -> "工具".equals(group.get("name")))
                .flatMap(group -> strings(group.get("actions")).stream()).toList();
        assertTrue(toolActions.contains("执行分摊"), "「工具」分组里要有执行分摊：" + toolActions);
        assertTrue(panelButtons(alloc).contains("执行分摊"), "面板按钮清单也要有：" + panelButtons(alloc));

        Map<String, Object> invoice = parse(configs.get("SALE_INVOICE"));
        List<String> invoiceFields = headFields(invoice);
        assertTrue(invoiceFields.contains("分摊费用"), "发票要有分摊费用字段：" + invoiceFields);
        assertTrue(invoiceFields.contains("分摊单号"), "发票要有分摊单号字段：" + invoiceFields);

        // 查询条要能按分摊单号捞票（默认只取表头前 6 个，摊费用的票会搜不到）
        Map<String, Object> table = maps(map(map(invoice.get("metadata")).get("panelPageDto")).get("tablePages")).get(0);
        List<String> queryFields = maps(table.get("queryFields")).stream()
                .map(field -> String.valueOf(field.get("dataName"))).toList();
        assertTrue(queryFields.contains("分摊单号"), "发票查询条要有分摊单号：" + queryFields);
    }

    /** 模板已经自带时，启动补丁不能再补一遍（不能出现两个「执行分摊」）。 */
    @Test
    void costLoopPatchDoesNotDuplicateOnTemplateConfig() throws Exception {
        new SalesFinancePanelRegistry(panelMapper, json).syncPanelConfigs();

        ArgumentCaptor<PanelConfig> inserted = ArgumentCaptor.forClass(PanelConfig.class);
        verify(panelMapper, times(3)).insert(inserted.capture());
        Map<String, Object> alloc = parse(inserted.getAllValues().stream()
                .filter(row -> "SALE_COST_ALLOC".equals(row.getPanelCode())).findFirst().orElseThrow());

        SaleCostLoopRegistry.upgradeAlloc(alloc);

        long count = groups(alloc).stream()
                .flatMap(group -> strings(group.get("actions")).stream())
                .filter("执行分摊"::equals).count();
        assertEquals(1, count, "执行分摊只能出现一次");
    }

    private List<String> headFields(Map<String, Object> config) {
        return maps(map(config.get("dataSchema")).get("fields")).stream()
                .map(field -> String.valueOf(field.get("dataName"))).toList();
    }

    private List<String> panelButtons(Map<String, Object> config) {
        return maps(map(config.get("metadata")).get("panelButtons")).stream()
                .map(button -> String.valueOf(button.get("buttonName"))).toList();
    }

    private void assertPanel(PanelConfig row, String name, String detailField, String selectAction) throws Exception {
        assertNotNull(row);
        assertEquals(name, row.getPanelName());
        assertEquals("单据", row.getCategory());
        assertEquals(SalesFinancePanelRegistry.VERSION, row.getVersion());
        Map<String, Object> config = parse(row);
        Map<String, Object> metadata = map(config.get("metadata"));
        assertEquals(row.getPanelCode(), metadata.get("panelCode"));
        assertTrue(strings(map(metadata.get("panelState")).get("defaultOptions")).contains("审批中"));
        assertTrue(groups(config).stream().anyMatch(group ->
                strings(group.get("actions")).contains("提交审批")));
        List<Map<String, Object>> tabs = maps(map(config.get("detail")).get("tabs"));
        assertTrue(maps(tabs.get(0).get("fields")).stream()
                .anyMatch(field -> detailField.equals(field.get("dataName"))));
        if (selectAction != null) {
            assertTrue(map(config.get("selectConfigs")).containsKey(selectAction));
        }
    }

    private List<Map<String, Object>> groups(Map<String, Object> config) {
        return maps(map(config.get("metadata")).get("buttonGroups"));
    }

    private Map<String, Object> parse(PanelConfig row) throws Exception {
        return json.readValue(row.getConfig(), new TypeReference<>() {});
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> map(Object value) {
        return (Map<String, Object>) value;
    }

    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> maps(Object value) {
        return (List<Map<String, Object>>) value;
    }

    @SuppressWarnings("unchecked")
    private List<String> strings(Object value) {
        return (List<String>) value;
    }

    /** 销货单要按入口区分选单来源：选销售出库单不能再回落到选销售订单。 */
    @Test
    void upgradesSaleInvSelectSourcesByTitle() throws Exception {
        SalesFinancePanelRegistry registry = new SalesFinancePanelRegistry(panelMapper, json);
        Map<String, Object> base = new java.util.LinkedHashMap<>();
        base.put("metadata", Map.of("panelCode", "SALE_INV"));
        base.put("selectConfig", Map.of("source", "SO_ORDER", "title", "选销售订单", "detailKey", "items"));

        Map<String, Object> sources = registry.saleInvSelectSources(base);

        assertTrue(sources.containsKey("选销售订单"), "原有单来源要保留：" + sources.keySet());
        Map<String, Object> saleOut = map(sources.get("选销售出库单"));
        assertEquals("SALE_OUT", saleOut.get("source"));
        assertEquals("items", saleOut.get("detailKey"));
        assertEquals("数量", saleOut.get("sourceQuantityField"));
        assertEquals("数量", saleOut.get("targetQuantityField"));
        assertEquals("已审核", map(saleOut.get("condition")).get("单据状态"));
        String detailJson = json.writeValueAsString(saleOut.get("detailMap"));
        assertTrue(detailJson.contains("仓库"), "选单必须带出仓库，否则销货单保存报「仓库不能为空」：" + detailJson);
        assertTrue(detailJson.contains("含税售价") && detailJson.contains("含税单价"),
                "出库单的含税售价要映射到销货单含税单价：" + detailJson);
    }
}