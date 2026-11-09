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
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.atLeastOnce;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/** 到货单字段补齐：采购订单下推后需要的表头/明细字段与列表列必须幂等补齐。 */
@ExtendWith(MockitoExtension.class)
class PurchasePanelRegistryTest {

    @Mock private PanelConfigMapper panelMapper;

    private final ObjectMapper json = new ObjectMapper();

    /** 运行库配置可能被改过：到货单→检验单的入口必须在启动时幂等补回。 */
    @Test
    void ensuresArrivalInspectionEntryAtStartup() throws Exception {
        PanelConfig row = new PanelConfig();
        row.setPanelCode("ARRIVAL_IN");
        row.setConfig(json.writeValueAsString(arrivalConfig()));
        when(panelMapper.selectOne(any())).thenReturn(row);

        new PurchasePanelRegistry(panelMapper, json).syncPanelConfigs();

        ArgumentCaptor<PanelConfig> updated = ArgumentCaptor.forClass(PanelConfig.class);
        verify(panelMapper, atLeastOnce()).updateById(updated.capture());
        Map<String, Object> config = json.readValue(updated.getValue().getConfig(), new TypeReference<>() {});
        List<Map<String, Object>> groups = listOfMaps(map(config.get("metadata")).get("buttonGroups"));
        Map<String, Object> gen = groups.stream().filter(g -> "生单".equals(g.get("name"))).findFirst().orElseThrow();
        List<?> actions = (List<?>) gen.get("actions");
        assertTrue(actions.contains("生成检验单"), "到货单必须能生成检验单");
        assertTrue(actions.contains("生成采购入库单"), "到货单必须能生成采购入库单");
    }

    @Test
    void ensuresArrivalFieldsForOrderPushDown() {
        Map<String, Object> config = arrivalConfig();

        PurchasePanelRegistry.upgradeArrivalIn(config);

        List<Map<String, Object>> head = listOfMaps(map(config.get("dataSchema")).get("fields"));
        Map<String, Object> orderNo = fieldOf(head, "采购订单号");
        assertEquals("文本", orderNo.get("dataType"));
        Map<String, Object> inspectState = fieldOf(head, "检验状态");
        assertEquals("下拉框", inspectState.get("dataType"));
        assertEquals(List.of("未检", "部分检", "已检"), inspectState.get("options"));
        assertEquals("未检", inspectState.get("defaultValue"));
        Map<String, Object> source = fieldOf(head, "数据来源");
        assertEquals("文本", source.get("dataType"));

        List<Map<String, Object>> detail = detailFields(config);
        for (String name : List.of("检验数量", "合格数量", "不合格数量", "让步接收数量",
                "报废数量", "累计入库数量", "累计退货数量")) {
            Map<String, Object> field = fieldOf(detail, name);
            assertEquals("小数", field.get("dataType"), name + " 必须是小数");
            assertEquals(true, field.get("computed"), name + " 必须是计算字段");
        }
        // 到货单是收料通知：单价由采购订单带入，不允许卡住历史/手工单据保存
        assertEquals(false, fieldOf(detail, "单价").get("isRequired"));

        List<Object> columns = gridColumns(config);
        assertTrue(columns.contains("检验数量"));
        assertTrue(columns.contains("累计入库数量"));
        assertTrue(columns.contains("累计退货数量"));
    }

    @Test
    void upgradeIsIdempotent() {
        Map<String, Object> config = arrivalConfig();

        PurchasePanelRegistry.upgradeArrivalIn(config);
        PurchasePanelRegistry.upgradeArrivalIn(config);

        assertEquals(1, countField(detailFields(config), "检验数量"));
        assertEquals(1, countField(listOfMaps(map(config.get("dataSchema")).get("fields")), "检验状态"));
        assertEquals(1, gridColumns(config).stream().filter("检验数量"::equals).count());
    }

    private Map<String, Object> arrivalConfig() {
        Map<String, Object> config = new LinkedHashMap<>();
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("panelPageDto", Map.of(
                "tablePages", List.of(Map.of(
                        "gridTabs", List.of(Map.of(
                                "label", "明细",
                                "columns", new ArrayList<>(List.of("存货编码", "到货数量"))))))));
        config.put("metadata", metadata);
        config.put("dataSchema", Map.of("type", "object", "fields", new ArrayList<>(List.of(
                new LinkedHashMap<>(Map.of("dataName", "单据日期", "dataType", "日期")),
                new LinkedHashMap<>(Map.of("dataName", "供应商", "dataType", "文本"))))));
        config.put("detail", Map.of("tabs", List.of(Map.of(
                "key", "items",
                "fields", new ArrayList<>(List.of(
                        new LinkedHashMap<>(Map.of("dataName", "存货编码", "dataType", "文本")),
                        new LinkedHashMap<>(Map.of("dataName", "到货数量", "dataType", "小数")),
                        new LinkedHashMap<>(Map.of("dataName", "单价", "dataType", "小数", "isRequired", true))))))));
        return config;
    }

    @Test
    void ensuresPurchaseInTraceFields() {
        Map<String, Object> config = purchaseInConfig();

        PurchasePanelRegistry.upgradePurchaseIn(config);

        List<Map<String, Object>> head = listOfMaps(map(config.get("dataSchema")).get("fields"));
        for (String name : List.of("到货单号", "检验单号", "入库类别", "数据来源")) {
            assertEquals("文本", fieldOf(head, name).get("dataType"), name + " 必须补到表头");
        }
        String fieldNames = String.valueOf(formOf(config).get("fieldNames"));
        assertTrue(fieldNames.contains("到货单号"), "列表/表单字段顺序必须包含到货单号");
        assertTrue(fieldNames.contains("检验单号"), "列表/表单字段顺序必须包含检验单号");
        assertTrue(detailFields(config).stream().anyMatch(f -> "批号".equals(f.get("dataName"))));
    }

    private Map<String, Object> purchaseInConfig() {
        Map<String, Object> config = new LinkedHashMap<>();
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("panelPageDto", Map.of(
                "tablePages", List.of(Map.of(
                        "gridTabs", List.of(Map.of(
                                "label", "明细",
                                "columns", new ArrayList<>(List.of("存货编码", "实收数量")))))),
                "formPages", List.of(Map.of(
                        "formName", "采购入库单",
                        "fieldNames", "单据日期,单据编号,业务类型,"))));
        config.put("metadata", metadata);
        config.put("dataSchema", Map.of("type", "object", "fields", new ArrayList<>(List.of(
                new LinkedHashMap<>(Map.of("dataName", "单据日期", "dataType", "日期")),
                new LinkedHashMap<>(Map.of("dataName", "单据编号", "dataType", "文本"))))));
        config.put("detail", Map.of("tabs", List.of(Map.of(
                "key", "items",
                "fields", new ArrayList<>(List.of(
                        new LinkedHashMap<>(Map.of("dataName", "存货编码", "dataType", "文本"))))))));
        return config;
    }

    /** 采购订单物料参照：编码列显示编码，选择后带出名称/规格/单位；重复升级不重复追加。 */
    @Test
    void fixesOrderMaterialReferenceFill() {
        Map<String, Object> config = puOrderConfig();

        PurchasePanelRegistry.upgradePuOrder(config);

        List<Map<String, Object>> genGroups = listOfMaps(map(config.get("metadata")).get("buttonGroups"));
        Map<String, Object> genGroup = genGroups.stream()
                .filter(g -> "生单".equals(g.get("name"))).findFirst().orElseThrow();
        assertTrue(((List<?>) genGroup.get("actions")).contains("生成采购发票"),
                "采购订单必须保留先票后货的生成采购发票入口");

        Map<String, Object> code = fieldOf(detailFields(config), "物料编码");
        assertEquals("存货编码", code.get("displayField"));
        assertEquals(List.of("存货编码", "存货名称", "规格型号", "计量单位", "停用"), code.get("refColumns"));
        Map<String, String> maps = new LinkedHashMap<>();
        for (Map<String, Object> item : listOfMaps(code.get("refMap"))) {
            maps.put(String.valueOf(item.get("to")), String.valueOf(item.get("from")));
        }
        assertEquals("存货名称", maps.get("物料名称"));
        assertEquals("规格型号", maps.get("规格型号"));
        assertEquals("计量单位", maps.get("单位"));

        PurchasePanelRegistry.upgradePuOrder(config);

        assertEquals(3, listOfMaps(fieldOf(detailFields(config), "物料编码").get("refMap")).size());
    }

    private Map<String, Object> puOrderConfig() {
        Map<String, Object> config = new LinkedHashMap<>();
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("panelState", new LinkedHashMap<>(Map.of("dataName", "单据状态")));
        metadata.put("buttonGroups", new ArrayList<>(List.of(
                new LinkedHashMap<>(Map.of("name", "生单", "actions", new ArrayList<>(List.of("生成到货单")))))));
        metadata.put("panelButtons", new ArrayList<>());
        metadata.put("panelPageDto", Map.of(
                "tablePages", List.of(Map.of(
                        "gridTabs", List.of(Map.of(
                                "label", "明细",
                                "columns", new ArrayList<>(List.of("物料编码", "物料名称")))))),
                "formPages", List.of(Map.of(
                        "formName", "采购订单",
                        "fieldNames", "单据日期,单据编号"))));
        config.put("metadata", metadata);
        config.put("dataSchema", Map.of("type", "object", "fields", new ArrayList<>(List.of(
                new LinkedHashMap<>(Map.of("dataName", "单据日期", "dataType", "日期")),
                new LinkedHashMap<>(Map.of("dataName", "单据编号", "dataType", "文本"))))));
        Map<String, Object> code = new LinkedHashMap<>();
        code.put("dataName", "物料编码");
        code.put("dataType", "参照");
        code.put("refPanel", "INV");
        code.put("refField", "存货编码");
        code.put("displayField", "存货名称");
        Map<String, Object> name = new LinkedHashMap<>();
        name.put("dataName", "物料名称");
        name.put("dataType", "参照");
        name.put("refPanel", "INV");
        name.put("refField", "存货名称");
        name.put("displayField", "存货名称");
        name.put("refMap", new ArrayList<>(List.of(
                new LinkedHashMap<>(Map.of("from", "存货编码", "to", "物料编码")))));
        config.put("detail", Map.of("tabs", List.of(Map.of(
                "key", "items",
                "fields", new ArrayList<>(List.of(code, name))))));
        return config;
    }

    private static Map<String, Object> formOf(Map<String, Object> config) {
        Map<String, Object> pageDto = map(map(config.get("metadata")).get("panelPageDto"));
        List<Map<String, Object>> forms = listOfMaps(pageDto.get("formPages"));
        return forms.get(0);
    }

    private static List<Map<String, Object>> detailFields(Map<String, Object> config) {
        List<Map<String, Object>> tabs = listOfMaps(map(config.get("detail")).get("tabs"));
        return listOfMaps(tabs.get(0).get("fields"));
    }

    private static List<Object> gridColumns(Map<String, Object> config) {
        Map<String, Object> pageDto = map(map(config.get("metadata")).get("panelPageDto"));
        List<Map<String, Object>> tables = listOfMaps(pageDto.get("tablePages"));
        List<Map<String, Object>> grids = listOfMaps(tables.get(0).get("gridTabs"));
        return (List<Object>) grids.get(0).get("columns");
    }

    private static Map<String, Object> fieldOf(List<Map<String, Object>> fields, String name) {
        return fields.stream().filter(f -> name.equals(f.get("dataName"))).findFirst().orElseThrow();
    }

    private static long countField(List<Map<String, Object>> fields, String name) {
        return fields.stream().filter(f -> name.equals(f.get("dataName"))).count();
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> map(Object value) {
        return value instanceof Map<?, ?> m ? (Map<String, Object>) m : new LinkedHashMap<>();
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> listOfMaps(Object value) {
        List<Map<String, Object>> out = new ArrayList<>();
        if (value instanceof List<?> list) {
            for (Object item : list) {
                if (item instanceof Map<?, ?> m) out.add((Map<String, Object>) m);
            }
        }
        return out;
    }
}
