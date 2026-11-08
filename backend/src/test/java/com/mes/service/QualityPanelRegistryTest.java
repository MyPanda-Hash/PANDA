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

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;

@ExtendWith(MockitoExtension.class)
class QualityPanelRegistryTest {

    @Mock private PanelConfigMapper panelMapper;

    private final ObjectMapper json = new ObjectMapper();

    @Test
    void registersCapturedTraceSettingsContracts() throws Exception {
        new QualityPanelRegistry(panelMapper, json).syncPanelConfigs();

        ArgumentCaptor<PanelConfig> inserted = ArgumentCaptor.forClass(PanelConfig.class);
        verify(panelMapper, times(8)).insert(inserted.capture());
        Map<String, PanelConfig> configs = inserted.getAllValues().stream()
                .collect(java.util.stream.Collectors.toMap(PanelConfig::getPanelCode, value -> value));

        assertSetting(configs.get("COMPANY_TRACE_SETTINGS"), "QT0101", "产品基本信息");
        assertSetting(configs.get("CUSTOMER_TRACE_SETTINGS"), "QT0102", "追溯模板标题");

        PanelConfig print = configs.get("TRACE_PRINT_TEMPLATE");
        assertNotNull(print);
        assertEquals("设置", print.getCategory());
        Map<String, Object> printConfig = parse(print);
        Map<String, Object> metadata = map(printConfig.get("metadata"));
        assertEquals("QT0103", metadata.get("tplusCode"));
        List<Map<String, Object>> tabs = maps(map(printConfig.get("detail")).get("tabs"));
        assertEquals("controls", tabs.get(0).get("key"));
        List<Map<String, Object>> fields = maps(tabs.get(0).get("fields"));
        Map<String, Object> controlType = fields.stream()
                .filter(field -> "控件类型".equals(field.get("dataName"))).findFirst().orElseThrow();
        assertEquals(List.of("直线", "矩形", "静态文本", "文本框", "图片", "明细"), controlType.get("options"));
    }

    @Test
    void removesDeprecatedArrivalReturnActionFromInspection() {
        Map<String, Object> config = new LinkedHashMap<>();
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("buttonGroups", new ArrayList<>(List.of(
                new LinkedHashMap<>(Map.of("name", "生单", "actions",
                        new ArrayList<>(List.of("生成采购入库单", "生成采购退货单", "生成到货退回单")))),
                new LinkedHashMap<>(Map.of("name", "修改", "actions", new ArrayList<>(List.of("修改")))))));
        metadata.put("panelButtons", new ArrayList<>(List.of(
                new LinkedHashMap<>(Map.of("buttonName", "生成到货退回单")),
                new LinkedHashMap<>(Map.of("buttonName", "保存")))));
        config.put("metadata", metadata);
        config.put("dataSchema", Map.of("type", "object", "fields", new ArrayList<>()));
        config.put("detail", Map.of("tabs", new ArrayList<>(List.of(Map.of(
                "key", "items", "label", "明细", "fields", new ArrayList<>())))));
        config.put("selectConfig", new LinkedHashMap<>());

        QualityPanelRegistry.upgradeInspection(config);

        List<Map<String, Object>> groups = maps(map(config.get("metadata")).get("buttonGroups"));
        Map<String, Object> generate = groups.stream()
                .filter(group -> "生单".equals(group.get("name"))).findFirst().orElseThrow();
        assertFalse(((List<?>) generate.get("actions")).contains("生成到货退回单"));
        assertTrue(((List<?>) generate.get("actions")).contains("生成采购退货单"), "采购退货单入口必须保留");
        List<Map<String, Object>> buttons = maps(map(config.get("metadata")).get("panelButtons"));
        assertFalse(buttons.stream().anyMatch(button -> "生成到货退回单".equals(button.get("buttonName"))));
    }

    @Test
    void upgradesArrivalSelectMappingForInspection() {
        Map<String, Object> legacy = new LinkedHashMap<>();
        legacy.put("source", "ARRIVAL_IN");
        legacy.put("columns", new ArrayList<>(List.of("单据编号", "到货数量")));
        legacy.put("headerMap", new ArrayList<>());
        legacy.put("detailMap", new ArrayList<>(List.of(
                new LinkedHashMap<>(Map.of("from", "到货数量", "to", "报检数量")),
                new LinkedHashMap<>(Map.of("from", "计量单位", "to", "计量单位")))));
        Map<String, Object> config = new LinkedHashMap<>();
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("buttonGroups", new ArrayList<>());
        metadata.put("panelButtons", new ArrayList<>());
        config.put("metadata", metadata);
        config.put("dataSchema", Map.of("type", "object", "fields", new ArrayList<>()));
        config.put("detail", Map.of("tabs", new ArrayList<>(List.of(new LinkedHashMap<>(Map.of(
                "key", "items", "label", "明细", "fields", new ArrayList<>()))))));
        config.put("selectConfig", legacy);

        QualityPanelRegistry.upgradeInspection(config);

        Map<String, Object> selects = map(config.get("selectConfigs"));
        Map<String, Object> select = map(selects.get("选到货单"));
        List<Map<String, Object>> detailMap = maps(select.get("detailMap"));
        assertTrue(detailMap.stream().anyMatch(m -> "实收数量".equals(m.get("from"))
                && "报检数量".equals(m.get("to"))), "报检数量必须取实收，拒收数量不送检");
        assertFalse(detailMap.stream().anyMatch(m -> "到货数量".equals(m.get("from"))
                && "报检数量".equals(m.get("to"))));
        assertTrue(detailMap.stream().anyMatch(m -> "存货编码".equals(m.get("from"))
                && "存货编码".equals(m.get("to"))), "选到货单必须带存货编码");
        assertTrue(detailMap.stream().anyMatch(m -> "采购单位".equals(m.get("from"))
                && "计量单位".equals(m.get("to"))));
        assertTrue(((List<?>) select.get("columns")).contains("实收数量"));
        assertEquals("实收数量", select.get("sourceQuantityField"));
        assertEquals("报检数量", select.get("targetQuantityField"));
        assertEquals("生成检验单", select.get("generateButton"), "选到货单必须走后端生单而不是客户端拼行");
        Map<String, Object> finishReport = map(selects.get("选成品报检单"));
        assertEquals("生成检验单", finishReport.get("generateButton"));
    }

    @Test
    void upgradesArrivalSelectToBackendGeneration() {
        Map<String, Object> select = new LinkedHashMap<>();
        select.put("source", "PU_ORDER");
        select.put("headerMap", new ArrayList<>(List.of(
                new LinkedHashMap<>(Map.of("from", "单据编号", "to", "来源单号")))));
        select.put("detailMap", new ArrayList<>(List.of(
                new LinkedHashMap<>(Map.of("from", "数量", "to", "到货数量")))));
        Map<String, Object> config = new LinkedHashMap<>();
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("buttonGroups", new ArrayList<>());
        metadata.put("panelButtons", new ArrayList<>());
        metadata.put("panelPageDto", Map.of(
                "tablePages", List.of(),
                "formPages", List.of(new LinkedHashMap<>(Map.of("formName", "到货单", "fieldNames", "")))));
        config.put("metadata", metadata);
        config.put("dataSchema", Map.of("type", "object", "fields", new ArrayList<>()));
        config.put("detail", Map.of("tabs", new ArrayList<>(List.of(new LinkedHashMap<>(Map.of(
                "key", "items", "label", "明细", "fields", new ArrayList<>()))))));
        config.put("selectConfig", select);

        QualityPanelRegistry.upgradeArrivalIn(config);

        Map<String, Object> selects = map(config.get("selectConfigs"));
        Map<String, Object> purchaseOrder = map(selects.get("选采购订单"));
        assertEquals("生成到货单", purchaseOrder.get("generateButton"), "选采购订单必须走后端生单");
        Map<String, Object> root = map(config.get("selectConfig"));
        assertEquals("生成到货单", root.get("generateButton"));
    }

    @Test
    void addsT15InspectionFieldsWithoutAddingTab() {
        Map<String, Object> config = new LinkedHashMap<>();
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("buttonGroups", new ArrayList<>());
        metadata.put("panelButtons", new ArrayList<>());
        Map<String, Object> grid = new LinkedHashMap<>();
        grid.put("label", "明细");
        grid.put("columns", new ArrayList<>(List.of(
                "存货编码", "存货名称", "报检数量", "检验数量",
                "合格数量", "不合格数量", "让步接收数量", "报废数量",
                "判定退货数量", "检验日期", "检验项目")));
        Map<String, Object> table = new LinkedHashMap<>();
        table.put("gridTabs", List.of(grid));
        Map<String, Object> form = new LinkedHashMap<>();
        form.put("formName", "来料/成品检验单");
        form.put("fieldNames", "单据日期,单据编号,业务类型");
        Map<String, Object> pageDto = new LinkedHashMap<>();
        pageDto.put("tablePages", List.of(table));
        pageDto.put("formPages", List.of(form));
        metadata.put("panelPageDto", pageDto);
        config.put("metadata", metadata);
        config.put("dataSchema", Map.of("type", "object", "fields", new ArrayList<>()));
        config.put("detail", Map.of("tabs", new ArrayList<>(List.of(new LinkedHashMap<>(Map.of(
                "key", "items", "label", "明细", "fields", new ArrayList<>()))))));
        config.put("selectConfig", new LinkedHashMap<>(Map.of("source", "ARRIVAL_IN")));

        QualityPanelRegistry.upgradeInspection(config);

        List<Map<String, Object>> head = maps(map(config.get("dataSchema")).get("fields"));
        assertEquals("参照", fieldOf(head, "检验部门").get("dataType"));
        assertEquals("文本", fieldOf(head, "采购订单号").get("dataType"));
        assertEquals("文本", fieldOf(head, "加工单号").get("dataType"));
        Map<String, Object> page = map(map(config.get("metadata")).get("panelPageDto"));
        List<Map<String, Object>> formPages = maps(page.get("formPages"));
        String fieldNames = String.valueOf(formPages.get(0).get("fieldNames"));
        assertTrue(fieldNames.contains("检验部门"));

        List<Map<String, Object>> tabs = maps(map(config.get("detail")).get("tabs"));
        List<Map<String, Object>> fields = maps(tabs.get(0).get("fields"));
        for (String name : List.of("应检数量", "验收退回数量", "不合格原因",
                "让步接收含税单价", "记录样本明细", "样本明细个数")) {
            assertNotNull(fieldOf(fields, name), name + " 必须补进明细");
        }
        assertEquals("参照", fieldOf(fields, "不合格原因").get("dataType"));
        assertEquals(true, fieldOf(fields, "验收退回数量").get("computed"));
        assertEquals(false, fieldOf(fields, "记录样本明细").get("defaultValue"));
        assertEquals(0, ((Number) fieldOf(fields, "样本明细个数").get("defaultValue")).intValue());
        assertEquals(true, fieldOf(fields, "判定退货数量").get("hidden"), "判定退货数量保留为内部字段");

        List<Object> columns = gridColumns(config);
        assertColumnAfter(columns, "报检数量", "应检数量");
        assertColumnAfter(columns, "不合格数量", "验收退回数量");
        assertColumnAfter(columns, "报废数量", "不合格原因");
        assertColumnAfter(columns, "不合格原因", "让步接收含税单价");
        assertColumnAfter(columns, "检验日期", "检验方案");
        assertColumnAfter(columns, "检验方案", "记录样本明细");
        assertColumnAfter(columns, "记录样本明细", "样本明细个数");
        assertFalse(columns.contains("判定退货数量"), "主表列应显示验收退回数量");
        // 三个页签：明细 + 检验项目（结果）+ 不合格处理
        assertEquals(3, maps(map(config.get("detail")).get("tabs")).size());
        Map<String, Object> projectTab = maps(map(config.get("detail")).get("tabs")).stream()
                .filter(tab -> "projects".equals(tab.get("key"))).findFirst().orElseThrow();
        List<Map<String, Object>> projectFields = maps(projectTab.get("fields"));
        for (String name : List.of("关联明细行号", "检验项目", "检验内容", "检验标准",
                "检验结果", "单项判定", "检验时间", "检验员")) {
            assertNotNull(fieldOf(projectFields, name), "检验项目页签缺 " + name);
        }
    }

    private static Map<String, Object> fieldOf(List<Map<String, Object>> fields, String name) {
        return fields.stream().filter(f -> name.equals(f.get("dataName"))).findFirst().orElse(null);
    }

    @SuppressWarnings("unchecked")
    private List<Object> gridColumns(Map<String, Object> config) {
        Map<String, Object> pageDto = map(map(config.get("metadata")).get("panelPageDto"));
        List<Map<String, Object>> tables = maps(pageDto.get("tablePages"));
        List<Map<String, Object>> grids = maps(tables.get(0).get("gridTabs"));
        return (List<Object>) grids.get(0).get("columns");
    }

    private void assertColumnAfter(List<Object> columns, String after, String name) {
        int afterIndex = columns.indexOf(after);
        int index = columns.indexOf(name);
        assertTrue(afterIndex >= 0, "缺少前置列 " + after);
        assertTrue(index == afterIndex + 1, name + " 应紧跟在 " + after + " 之后，实际列：" + columns);
    }

    @Test
    void registersFirstInspectReportWithProcessSource() throws Exception {
        new QualityPanelRegistry(panelMapper, json).syncPanelConfigs();

        ArgumentCaptor<PanelConfig> inserted = ArgumentCaptor.forClass(PanelConfig.class);
        verify(panelMapper, times(8)).insert(inserted.capture());
        PanelConfig first = inserted.getAllValues().stream()
                .filter(row -> "FIRST_INSPECT".equals(row.getPanelCode())).findFirst().orElseThrow();
        Map<String, Object> config = parse(first);
        List<Map<String, Object>> head = maps(map(config.get("dataSchema")).get("fields"));
        for (String name : List.of("委外供应商", "部门", "负责人", "检验员", "来源单号")) {
            assertNotNull(fieldOf(head, name), "首件报检表头缺 " + name);
        }
        Map<String, Object> select = map(config.get("selectConfig"));
        assertEquals("PROCESS_REPORT", select.get("source"), "首件报检来源应为工序汇报单");
        List<Map<String, Object>> fields = maps(map(maps(map(config.get("detail")).get("tabs")).get(0)).get("fields"));
        for (String name : List.of("BOM版本号", "加工单号", "工序汇报单号", "工序编码", "工序名称",
                "加工类型", "检验方式", "抽检比例%", "抽检数量", "最大不合格数", "检验要求",
                "生产车间", "班组", "工人", "累计检验数量", "累计合格数量", "累计不合格数量",
                "累计检验返工数量", "不合格原因", "检验方案", "板号")) {
            assertNotNull(fieldOf(fields, name), "首件报检明细缺 " + name);
        }
        assertEquals(1, maps(map(config.get("detail")).get("tabs")).size(), "首件报检保持单页签");
    }

    @Test
    void addsT10FinishInspectFieldsAndColumns() {
        Map<String, Object> config = new LinkedHashMap<>();
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("buttonGroups", new ArrayList<>());
        metadata.put("panelButtons", new ArrayList<>());
        Map<String, Object> grid = new LinkedHashMap<>();
        grid.put("label", "明细");
        grid.put("columns", new ArrayList<>(List.of(
                "产品名称", "存货编码", "规格型号", "计量单位", "报检数量", "合格数量",
                "不合格数量", "检验结果", "检验员", "检验方式", "抽检比例%", "检验要求",
                "班组", "工人", "仓库", "图号")));
        Map<String, Object> table = new LinkedHashMap<>();
        table.put("gridTabs", List.of(grid));
        Map<String, Object> form = new LinkedHashMap<>();
        form.put("formName", "成品报检单");
        form.put("fieldNames", "单据日期,单据编号,业务类型");
        Map<String, Object> pageDto = new LinkedHashMap<>();
        pageDto.put("tablePages", List.of(table));
        pageDto.put("formPages", List.of(form));
        metadata.put("panelPageDto", pageDto);
        config.put("metadata", metadata);
        config.put("dataSchema", Map.of("type", "object", "fields", new ArrayList<>()));
        config.put("detail", Map.of("tabs", new ArrayList<>(List.of(new LinkedHashMap<>(Map.of(
                "key", "items", "label", "明细", "fields", new ArrayList<>()))))));
        config.put("selectConfig", new LinkedHashMap<>());

        QualityPanelRegistry.upgradeFinishInspect(config);

        List<Map<String, Object>> head = maps(map(config.get("dataSchema")).get("fields"));
        assertEquals("参照", fieldOf(head, "委外供应商").get("dataType"));
        assertEquals("参照", fieldOf(head, "部门").get("dataType"));
        assertEquals("参照", fieldOf(head, "负责人").get("dataType"));
        assertEquals("文本", fieldOf(head, "供应商编码").get("dataType"));
        List<Map<String, Object>> fields = maps(map(maps(map(config.get("detail")).get("tabs")).get(0)).get("fields"));
        for (String name : List.of("BOM版本号", "供应商(库存)", "单价", "税率%", "含税单价", "金额",
                "税额", "含税金额", "抽检数量", "最大不合格数", "累计检验数量", "累计合格数量",
                "累计验收退回数量", "累计让步接收数量", "累计报废数量", "累计入库数量",
                "累计已退数量", "不合格原因", "检验方案")) {
            assertNotNull(fieldOf(fields, name), "成品报检明细缺 " + name);
        }
        assertEquals("参照", fieldOf(fields, "不合格原因").get("dataType"));
        assertEquals("参照", fieldOf(fields, "检验方案").get("dataType"));
        assertEquals(true, fieldOf(fields, "累计合格数量").get("computed"));

        List<Object> columns = gridColumns(config);
        assertColumnAfter(columns, "报检数量", "单价");
        assertColumnAfter(columns, "单价", "税率%");
        assertColumnAfter(columns, "税率%", "含税单价");
        assertColumnAfter(columns, "含税单价", "金额");
        assertColumnAfter(columns, "金额", "税额");
        assertColumnAfter(columns, "税额", "含税金额");
        assertColumnAfter(columns, "抽检比例%", "抽检数量");
        assertColumnAfter(columns, "抽检数量", "最大不合格数");
        assertColumnAfter(columns, "检验要求", "累计检验数量");
        assertColumnAfter(columns, "累计已退数量", "不合格原因");
        assertColumnAfter(columns, "不合格原因", "检验方案");
        assertColumnAfter(columns, "规格型号", "BOM版本号");
    }

    @Test
    void registersProcessInspectionWithOwnProcessFields() throws Exception {
        new QualityPanelRegistry(panelMapper, json).syncPanelConfigs();

        ArgumentCaptor<PanelConfig> inserted = ArgumentCaptor.forClass(PanelConfig.class);
        verify(panelMapper, times(8)).insert(inserted.capture());
        PanelConfig process = inserted.getAllValues().stream()
                .filter(row -> "PROCESS_INSPECTION".equals(row.getPanelCode())).findFirst().orElseThrow();
        Map<String, Object> config = parse(process);
        List<Map<String, Object>> head = maps(map(config.get("dataSchema")).get("fields"));
        for (String name : List.of("检验部门", "数据来源", "加工单号", "生产车间")) {
            assertNotNull(fieldOf(head, name), "生产过程检验表头缺 " + name);
        }
        List<Map<String, Object>> fields = maps(map(maps(map(config.get("detail")).get("tabs")).get(0)).get("fields"));
        for (String name : List.of("产品批号", "加工类型", "加工单号", "工序汇报单号", "工序编码",
                "工序名称", "工序辅单位", "检验返工数量", "工废数量", "料废数量",
                "生产车间", "工作中心", "设备", "班组", "工人", "检验方案", "不合格原因")) {
            assertNotNull(fieldOf(fields, name), "生产过程检验明细缺 " + name);
        }
        assertEquals(3, maps(map(config.get("detail")).get("tabs")).size(), "明细+检验项目+不合格处理");
        Map<String, Object> select = map(config.get("selectConfig"));
        assertEquals("PROCESS_INSPECT_APPLY", select.get("source"));
        List<Map<String, Object>> groups = maps(map(config.get("metadata")).get("buttonGroups"));
        assertFalse(groups.stream().anyMatch(group -> "生单".equals(group.get("name"))),
                "过程检验当前不做下游转换，不应挂生单组");
    }

    private void assertSetting(PanelConfig row, String tplusCode, String expectedField) throws Exception {
        assertNotNull(row);
        assertEquals("设置", row.getCategory());
        Map<String, Object> config = parse(row);
        Map<String, Object> metadata = map(config.get("metadata"));
        assertEquals(tplusCode, metadata.get("tplusCode"));
        assertEquals(true, metadata.get("singleDoc"));
        List<Map<String, Object>> fields = maps(map(config.get("dataSchema")).get("fields"));
        assertTrue(fields.stream().anyMatch(field -> expectedField.equals(field.get("dataName"))));
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
}
