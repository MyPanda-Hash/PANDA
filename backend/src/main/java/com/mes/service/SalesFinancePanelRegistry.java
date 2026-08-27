package com.mes.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.PanelConfig;
import com.mes.mapper.PanelConfigMapper;
import jakarta.annotation.PostConstruct;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Persists the sales finance voucher metadata required by the SQL panel runtime.
 */
@Component
public class SalesFinancePanelRegistry {

    static final String VERSION = "sales-finance-2.0";

    private final PanelConfigMapper panelMapper;
    private final ObjectMapper json;

    public SalesFinancePanelRegistry(PanelConfigMapper panelMapper, ObjectMapper json) {
        this.panelMapper = panelMapper;
        this.json = json;
    }

    @PostConstruct
    public void syncPanelConfigs() {
        upsert("SALE_INVOICE", "销售发票", saleInvoiceConfig());
        upsert("EXPENSE", "费用单", expenseConfig());
        upsert("SALE_COST_ALLOC", "销售费用分摊单", saleCostAllocConfig());
    }

    private Map<String, Object> saleInvoiceConfig() {
        List<Map<String, Object>> head = fields(
                date("单据日期"), autoCode(),
                select("业务类型", "销售发票", "销售发票(退货)"),
                partner("客户", true, List.of("客户", "两者"), "客户编码"),
                field("客户编码", "文本", false),
                partner("结算客户", false, List.of("客户", "两者"), null),
                reference("部门", "DEPT", "部门名称", false),
                reference("业务员", "EMP", "员工名称", false),
                select("开票类型", "增值税专用发票", "增值税普通发票"),
                field("销货单号", "文本", false),
                field("来源单据", "文本", false),
                field("来源单号", "文本", false),
                decimal("价税合计", false), field("备注", "文本", false));

        List<Map<String, Object>> items = fields(
                inventoryReference("存货编码", "存货编码", false),
                inventoryReference("存货名称", "存货名称", true),
                field("规格型号", "文本", false),
                unit("计量单位"), decimal("数量", true), decimal("无税单价", true),
                defaultValue(decimal("税率%", false), 13), computed("税额"), computed("价税合计"));
        items.addAll(sourceFields());

        Map<String, Object> tab = tab("items", "发票明细", items,
                List.of(summary("数量合计", "数量"), summary("税额合计", "税额"), summary("价税合计", "价税合计")),
                List.of(calc("税额", "数量 * 无税单价 * 税率% / 100"),
                        calc("价税合计", "数量 * 无税单价 * (1 + 税率% / 100)")));

        Map<String, Object> select = selectConfig("SALE_INV", "选销货单",
                "仅显示已审核销货单；选择后带入客户、来源信息及商品明细",
                List.of("单据编号", "单据日期", "客户", "存货名称", "数量", "单价", "含税单价"),
                List.of(
                        mapping("单据编号", "销货单号"), mapping("单据编号", "来源单号"),
                        fixed("来源单据", "SALE_INV"), mapping("客户", "客户"),
                        mapping("客户编码", "客户编码"), mapping("结算客户", "结算客户"),
                        mapping("部门", "部门"), mapping("经手人", "业务员")),
                List.of(
                        mapping("存货编码", "存货编码"), mapping("存货名称", "存货名称"),
                        mapping("规格型号", "规格型号"), mapping("销售单位", "计量单位"),
                        mapping("数量", "数量"), mapping("单价", "无税单价"),
                        mapping("税率%", "税率%")),
                "数量", "数量", Map.of());

        List<Map<String, Object>> groups = voucherGroups(List.of("选销货单"), List.of());
        return voucherConfig("SALE_INVOICE", "销售发票", head, List.of(tab), groups,
                selects("选销货单", select));
    }

    private Map<String, Object> expenseConfig() {
        List<Map<String, Object>> head = fields(
                date("单据日期"), autoCode(), select("业务类型", "费用单"),
                select("费用类型", "销售费用", "采购费用", "管理费用", "其他费用"),
                partner("往来单位", false, List.of(), null),
                reference("部门", "DEPT", "部门名称", false),
                reference("经手人", "EMP", "员工名称", false),
                reference("项目", "PROJ", "项目名称", false),
                select("结算方式", "转账", "现金", "票据", "其他"),
                decimal("费用合计", false), field("备注", "文本", false));

        List<Map<String, Object>> items = fields(
                select("费用项目", "运费", "装卸费", "包装费", "广告费", "差旅费", "其他"),
                decimal("金额", true), defaultValue(decimal("税率%", false), 0),
                computed("税额"), computed("含税金额"),
                reference("费用承担部门", "DEPT", "部门名称", false),
                field("备注", "文本", false));
        Map<String, Object> tab = tab("items", "费用明细", items,
                List.of(summary("金额合计", "金额"), summary("税额合计", "税额"),
                        summary("含税金额合计", "含税金额")),
                List.of(calc("税额", "金额 * 税率% / 100"),
                        calc("含税金额", "金额 * (1 + 税率% / 100)")));

        List<Map<String, Object>> groups = voucherGroups(List.of(),
                List.of("生成销售费用分摊单", "生成采购费用分摊单"));
        return voucherConfig("EXPENSE", "费用单", head, List.of(tab), groups, Map.of());
    }

    private Map<String, Object> saleCostAllocConfig() {
        List<Map<String, Object>> head = fields(
                date("单据日期"), autoCode(), select("业务类型", "销售费用分摊"),
                select("费用类型", "销售费用", "其他费用"),
                reference("部门", "DEPT", "部门名称", false),
                reference("经手人", "EMP", "员工名称", false),
                reference("项目", "PROJ", "项目名称", false),
                field("来源单据", "文本", false), field("来源单号", "文本", false),
                decimal("分摊合计", false), field("备注", "文本", false));

        List<Map<String, Object>> items = fields(
                field("费用单号", "文本", false),
                select("费用项目", "运费", "装卸费", "包装费", "广告费", "差旅费", "其他"),
                select("分摊对象", "客户", "销售发票"),
                partner("客户", false, List.of("客户", "两者"), null),
                reference("销售发票号", "SALE_INVOICE", "单据编号", false),
                decimal("分摊金额", true), field("备注", "文本", false));
        items.addAll(sourceFields());
        Map<String, Object> tab = tab("items", "分摊明细", items,
                List.of(summary("分摊金额合计", "分摊金额")), List.of());

        Map<String, Object> select = selectConfig("EXPENSE", "选费用单",
                "仅显示已审核且费用类型为销售费用的费用单；选择后带入待分摊费用",
                List.of("单据编号", "单据日期", "费用类型", "往来单位", "费用项目", "金额"),
                List.of(
                        mapping("单据编号", "来源单号"), fixed("来源单据", "EXPENSE"),
                        mapping("费用类型", "费用类型"), mapping("部门", "部门"),
                        mapping("经手人", "经手人"), mapping("项目", "项目")),
                List.of(
                        mapping("单据编号", "费用单号"), mapping("费用项目", "费用项目"),
                        mapping("往来单位", "客户"), mapping("金额", "分摊金额"),
                        mapping("备注", "备注")),
                "金额", "分摊金额", Map.of("费用类型", "销售费用"));

        List<Map<String, Object>> groups = voucherGroups(List.of("选费用单"), List.of());
        return voucherConfig("SALE_COST_ALLOC", "销售费用分摊单", head, List.of(tab), groups,
                selects("选费用单", select));
    }

    private Map<String, Object> voucherConfig(String code, String name, List<Map<String, Object>> head,
                                               List<Map<String, Object>> tabs, List<Map<String, Object>> groups,
                                               Map<String, Object> selectConfigs) {
        List<Map<String, Object>> buttons = new ArrayList<>();
        for (Map<String, Object> group : groups) {
            Object actions = group.get("actions");
            if (actions instanceof List<?> list) {
                for (Object action : list) buttons.add(Map.of("buttonName", action));
            }
        }

        List<String> columns = maps(tabs.get(0).get("fields")).stream()
                .filter(field -> !Boolean.TRUE.equals(field.get("hidden")))
                .map(field -> String.valueOf(field.get("dataName"))).toList();
        List<Map<String, Object>> queryFields = head.stream()
                .filter(field -> !List.of("备注", "来源单据", "来源单号").contains(field.get("dataName")))
                .limit(6).toList();

        Map<String, Object> table = new LinkedHashMap<>();
        table.put("tableName", name + "列表");
        table.put("queryFields", queryFields);
        table.put("gridTabs", List.of(Map.of("label", tabs.get(0).get("label"),
                "rowSource", "detail", "columns", columns)));
        table.put("topBarBtn", buttons);
        table.put("rowOperationBarBtn", List.of());
        table.put("events", List.of());

        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("panelCode", code);
        metadata.put("panelName", name);
        metadata.put("panelCategory", "单据");
        metadata.put("autoCodeField", "单据编号");
        metadata.put("panelState", Map.of("dataName", "单据状态", "dataType", "STRING",
                "defaultOptions", List.of("草稿", "审批中", "已审核", "已中止")));
        metadata.put("panelPageDto", Map.of(
                "tablePages", List.of(table),
                "formPages", List.of(Map.of(
                        "formName", name,
                        "fieldNames", String.join(",", head.stream()
                                .map(field -> String.valueOf(field.get("dataName"))).toList()),
                        "bottomOperationBarBtn", buttons,
                        "events", List.of()))));
        metadata.put("panelButtons", buttons);
        metadata.put("buttonGroups", groups);
        metadata.put("version", VERSION);

        Map<String, Object> config = new LinkedHashMap<>();
        config.put("metadata", metadata);
        config.put("dataSchema", Map.of("type", "object", "fields", head));
        config.put("detail", Map.of("tabs", tabs));
        if (!selectConfigs.isEmpty()) {
            config.put("selectConfigs", selectConfigs);
            config.put("selectConfig", selectConfigs.values().iterator().next());
        }
        return config;
    }

    private List<Map<String, Object>> voucherGroups(List<String> selectActions, List<String> generateActions) {
        List<Map<String, Object>> groups = new ArrayList<>();
        groups.add(group("新增", "新增"));
        if (!selectActions.isEmpty()) groups.add(group("选单", selectActions.toArray(String[]::new)));
        groups.add(group("保存", "保存", "保存新增", "保存为草稿"));
        groups.add(group("删除", "删除", "删除单据"));
        groups.add(group("审批", "提交审批", "审批通过", "审批驳回", "审批情况", "弃审"));
        if (!generateActions.isEmpty()) groups.add(group("生单", generateActions.toArray(String[]::new)));
        groups.add(group("更多", "刷新"));
        return groups;
    }

    private Map<String, Object> selectConfig(String source, String title, String tip, List<String> columns,
                                             List<Map<String, Object>> headerMap,
                                             List<Map<String, Object>> detailMap,
                                             String sourceQuantityField, String targetQuantityField,
                                             Map<String, Object> condition) {
        Map<String, Object> config = new LinkedHashMap<>();
        config.put("source", source);
        config.put("title", title);
        config.put("tip", tip);
        config.put("columns", columns);
        config.put("detailKey", "items");
        config.put("headerMap", headerMap);
        config.put("detailMap", detailMap);
        config.put("sourceQuantityField", sourceQuantityField);
        config.put("targetQuantityField", targetQuantityField);
        if (!condition.isEmpty()) config.put("condition", condition);
        return config;
    }

    private void upsert(String code, String name, Map<String, Object> config) {
        PanelConfig row = panelMapper.selectOne(new LambdaQueryWrapper<PanelConfig>()
                .eq(PanelConfig::getPanelCode, code));
        LocalDateTime now = LocalDateTime.now();
        if (row == null) {
            row = new PanelConfig();
            row.setPanelCode(code);
            row.setCreateTime(now);
        }
        row.setPanelName(name);
        row.setCategory("单据");
        row.setConfig(toJson(config));
        row.setVersion(VERSION);
        row.setUpdateTime(now);
        if (row.getId() == null) panelMapper.insert(row); else panelMapper.updateById(row);
    }

    private String toJson(Object value) {
        try {
            return json.writeValueAsString(value);
        } catch (Exception e) {
            throw new IllegalStateException("销售财务面板配置序列化失败", e);
        }
    }

    @SafeVarargs
    private static List<Map<String, Object>> fields(Map<String, Object>... values) {
        return new ArrayList<>(Arrays.asList(values));
    }

    private static Map<String, Object> field(String name, String type, boolean required) {
        Map<String, Object> field = new LinkedHashMap<>();
        field.put("dataName", name);
        field.put("dataType", type);
        field.put("isRequired", required);
        field.put("defaultValue", List.of("小数", "整数").contains(type) ? 0 : "");
        return field;
    }

    private static Map<String, Object> decimal(String name, boolean required) {
        return field(name, "小数", required);
    }

    private static Map<String, Object> computed(String name) {
        Map<String, Object> field = decimal(name, false);
        field.put("computed", true);
        field.remove("defaultValue");
        return field;
    }

    private static Map<String, Object> defaultValue(Map<String, Object> field, Object value) {
        field.put("defaultValue", value);
        return field;
    }

    private static Map<String, Object> date(String name) {
        return field(name, "日期", true);
    }

    private static Map<String, Object> autoCode() {
        Map<String, Object> field = field("单据编号", "文本", true);
        field.put("autoCode", true);
        return field;
    }

    private static Map<String, Object> select(String name, String... options) {
        Map<String, Object> field = field(name, "下拉框", true);
        field.put("options", Arrays.asList(options));
        if (options.length > 0) field.put("defaultValue", options[0]);
        return field;
    }

    private static Map<String, Object> unit(String name) {
        return select(name, "件", "kg", "套", "升");
    }

    private static Map<String, Object> reference(String name, String panel, String refField, boolean required) {
        Map<String, Object> field = field(name, "参照", required);
        field.put("refPanel", panel);
        field.put("refField", refField);
        field.put("displayField", refField);
        field.put("filter", Map.of("停用", false));
        return field;
    }

    private static Map<String, Object> partner(String name, boolean required, List<String> natures,
                                               String codeTarget) {
        Map<String, Object> field = reference(name, "PARTNER", "往来单位名称", required);
        if (!natures.isEmpty()) field.put("filter", Map.of("停用", false, "性质", natures));
        field.put("refColumns", List.of("往来单位编码", "往来单位名称", "往来单位简称", "性质", "停用"));
        if (codeTarget != null) field.put("refMap", List.of(mapping("往来单位编码", codeTarget)));
        return field;
    }

    private static Map<String, Object> inventoryReference(String name, String refField, boolean required) {
        Map<String, Object> field = reference(name, "INV", refField, required);
        field.put("refColumns", List.of("存货编码", "存货名称", "规格型号", "计量单位", "停用"));
        field.put("refMap", List.of(
                mapping("存货编码", "存货编码"), mapping("存货名称", "存货名称"),
                mapping("规格型号", "规格型号"), mapping("计量单位", "计量单位")));
        return field;
    }

    private static List<Map<String, Object>> sourceFields() {
        List<Map<String, Object>> fields = new ArrayList<>();
        for (String name : List.of("来源面板", "来源单号", "来源行号", "来源数量", "累计执行数量")) {
            Map<String, Object> field = field(name,
                    List.of("来源行号").contains(name) ? "整数" : name.contains("数量") ? "小数" : "文本", false);
            field.put("hidden", true);
            fields.add(field);
        }
        return fields;
    }

    private static Map<String, Object> tab(String key, String label, List<Map<String, Object>> fields,
                                           List<Map<String, Object>> summaries,
                                           List<Map<String, Object>> calculations) {
        Map<String, Object> tab = new LinkedHashMap<>();
        tab.put("key", key);
        tab.put("label", label);
        tab.put("isRequired", true);
        tab.put("fields", fields);
        if (!summaries.isEmpty()) tab.put("summaryItems", summaries);
        if (!calculations.isEmpty()) tab.put("calc", calculations);
        return tab;
    }

    private static Map<String, Object> summary(String label, String field) {
        return Map.of("label", label, "field", field);
    }

    private static Map<String, Object> calc(String target, String formula) {
        return Map.of("target", target, "formula", formula, "round", 2);
    }

    private static Map<String, Object> group(String name, String... actions) {
        return Map.of("name", name, "actions", Arrays.asList(actions));
    }

    private static Map<String, Object> mapping(String from, String to) {
        return Map.of("from", from, "to", to);
    }

    private static Map<String, Object> fixed(String to, Object value) {
        return Map.of("to", to, "fixed", value);
    }

    private static Map<String, Object> selects(String action, Map<String, Object> config) {
        Map<String, Object> selects = new LinkedHashMap<>();
        selects.put(action, config);
        return selects;
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> maps(Object value) {
        return value instanceof List<?> list ? (List<Map<String, Object>>) list : List.of();
    }
}
