package com.mes.service;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.springframework.stereotype.Component;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.PanelConfig;
import com.mes.mapper.PanelConfigMapper;

import jakarta.annotation.PostConstruct;

/**
 * 委外检验单（OUTSOURCE_INSPECTION）面板：委外回厂检验独立成单。
 *
 * <p>口径（2026-11-13 用户要求）：委外加工完回厂的检验要有一张专门的委外检验单，
 * 只处理委外加工单的检验；检验合格了才能生成委外入库单。不再借用首件报检单
 * （首件是自制线「一工序一单」的自检口径，和回厂整批验收不是一回事）。
 *
 * <p>幂等：面板不存在就按下面的模板插入；已存在则只补「生成委外入库单」这个生单入口，
 * 不覆盖管理员改过的名称、字段和按钮。
 */
@Component
public class OutsourceInspectionPanelRegistry {

    static final String VERSION = "OI1.0";
    static final String PANEL = "OUTSOURCE_INSPECTION";
    static final String GENERATE_IN_ACTION = "生成委外入库单";

    private final PanelConfigMapper panelMapper;
    private final ObjectMapper json;

    public OutsourceInspectionPanelRegistry(PanelConfigMapper panelMapper, ObjectMapper json) {
        this.panelMapper = panelMapper;
        this.json = json;
    }

    @PostConstruct
    public void syncPanelConfigs() {
        PanelConfig row = panelMapper.selectOne(new LambdaQueryWrapper<PanelConfig>()
                .eq(PanelConfig::getPanelCode, PANEL));
        if (row == null) {
            PanelConfig created = new PanelConfig();
            created.setPanelCode(PANEL);
            created.setPanelName("委外检验单");
            created.setCategory("单据");
            created.setConfig(toJson(panelConfig()));
            created.setVersion(VERSION);
            created.setCreateTime(LocalDateTime.now());
            created.setUpdateTime(LocalDateTime.now());
            panelMapper.insert(created);
            return;
        }
        if (row.getConfig() == null || row.getConfig().isBlank()) return;
        Map<String, Object> config;
        try {
            config = json.readValue(row.getConfig(), new TypeReference<Map<String, Object>>() { });
        } catch (Exception e) {
            throw new IllegalStateException("委外检验单配置解析失败", e);
        }
        ensureGenerateInboundAction(config);
        try {
            row.setConfig(json.writeValueAsString(config));
            row.setVersion(VERSION);
            row.setUpdateTime(LocalDateTime.now());
            panelMapper.updateById(row);
        } catch (Exception e) {
            throw new IllegalStateException("委外检验单配置补齐失败", e);
        }
    }

    /** 幂等补「生单 → 生成委外入库单」（分组与顶部按钮各一份）。 */
    @SuppressWarnings("unchecked")
    static void ensureGenerateInboundAction(Map<String, Object> config) {
        Map<String, Object> metadata = config.get("metadata") instanceof Map<?, ?> m
                ? new LinkedHashMap<>((Map<String, Object>) m) : new LinkedHashMap<>();
        List<Map<String, Object>> groups = metadata.get("buttonGroups") instanceof List<?> list
                ? new ArrayList<>((List<Map<String, Object>>) list) : new ArrayList<>();
        boolean found = false;
        for (Map<String, Object> group : groups) {
            if (!"生单".equals(String.valueOf(group.get("name")))) continue;
            found = true;
            List<Object> actions = group.get("actions") instanceof List<?> a
                    ? new ArrayList<>((List<Object>) a) : new ArrayList<>();
            if (actions.stream().noneMatch(GENERATE_IN_ACTION::equals)) actions.add(GENERATE_IN_ACTION);
            group.put("actions", actions);
        }
        if (!found) {
            Map<String, Object> group = new LinkedHashMap<>();
            group.put("name", "生单");
            group.put("actions", new ArrayList<>(List.of(GENERATE_IN_ACTION)));
            groups.add(group);
        }
        metadata.put("buttonGroups", groups);

        List<Map<String, Object>> buttons = metadata.get("panelButtons") instanceof List<?> list
                ? new ArrayList<>((List<Map<String, Object>>) list) : new ArrayList<>();
        if (buttons.stream().noneMatch(b -> GENERATE_IN_ACTION.equals(String.valueOf(b.get("buttonName"))))) {
            Map<String, Object> button = new LinkedHashMap<>();
            button.put("buttonName", GENERATE_IN_ACTION);
            buttons.add(button);
        }
        metadata.put("panelButtons", buttons);
        config.put("metadata", metadata);
    }

    /** 面板模板：表头挂委外加工单/供应商/检验员，明细一行一个回厂产品。 */
    static Map<String, Object> panelConfig() {
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("panelCode", PANEL);
        metadata.put("panelName", "委外检验单");
        metadata.put("panelCategory", "单据");
        metadata.put("autoCodeField", "单据编号");
        metadata.put("version", VERSION);
        metadata.put("panelState", Map.of(
                "dataName", "单据状态",
                "dataType", "STRING",
                "defaultOptions", List.of("草稿", "审批中", "已审核", "已中止")));
        metadata.put("panelButtons", buttons("新增流程", "删除", "刷新", "保存", "弃审", "放弃",
                "提交审批", "审批通过", "审批驳回", "审批情况", GENERATE_IN_ACTION, "打印", "导出"));
        metadata.put("buttonGroups", List.of(
                group("新增", "新增流程"),
                group("保存", "保存"),
                group("删除", "删除"),
                group("审批", "提交审批", "审批通过", "审批驳回", "审批情况", "弃审"),
                group("生单", GENERATE_IN_ACTION),
                group("工具", "现存量查询"),
                group("打印", "打印", "导出"),
                group("更多", "放弃", "刷新")));
        metadata.put("panelPageDto", Map.of(
                "tablePages", List.of(Map.of(
                        "tableName", "委外检验单列表",
                        "queryFields", List.of(
                                queryField("单据日期", "日期"),
                                queryField("单据编号", "文本"),
                                queryField("委外供应商", "参照"),
                                queryField("委外加工单号", "文本"),
                                queryField("单据状态", "文本")),
                        "gridTabs", List.of(Map.of(
                                "label", "明细",
                                "rowSource", "items",
                                "columns", List.of("产品编码", "产品名称", "规格型号", "计量单位",
                                        "报检数量", "检验数量", "合格数量", "不合格数量",
                                        "让步接收数量", "报废数量", "检验结果判定", "处置方式", "委外单价")),
                                Map.of("label", "汇总", "rowSource", "items", "summary", true,
                                        "columns", List.of("产品名称", "计量单位", "报检数量", "合格数量",
                                                "不合格数量", "检验结果判定"))),
                        "topBarBtn", List.of(Map.of("buttonName", "新增流程"),
                                Map.of("buttonName", "删除"), Map.of("buttonName", "刷新")),
                        "rowOperationBarBtn", List.of(),
                        "events", List.of())),
                "formPages", List.of(Map.of(
                        "formName", "委外检验单",
                        "fieldNames", "单据日期,单据编号,业务类型,委外供应商,委外加工单号,部门,经手人,检验员,检验日期,来源单据,来源单号,备注",
                        "bottomOperationBarBtn", List.of(Map.of("buttonName", "保存"),
                                Map.of("buttonName", "提交审批"), Map.of("buttonName", GENERATE_IN_ACTION),
                                Map.of("buttonName", "放弃")),
                        "events", List.of()))));

        Map<String, Object> head = Map.of("dataName", "单据编号", "dataType", "文本",
                "isRequired", true, "autoCode", true, "defaultValue", "");
        List<Map<String, Object>> fields = new ArrayList<>();
        fields.add(decimalHead("单据日期", "日期"));
        fields.add(head);
        fields.add(dropdown("业务类型", "委外检验", List.of("委外检验")));
        fields.add(ref("委外供应商", "PARTNER", "往来单位名称"));
        fields.add(text("委外加工单号"));
        fields.add(ref("部门", "DEPT", "部门名称"));
        fields.add(ref("经手人", "EMP", "员工名称"));
        fields.add(ref("检验员", "EMP", "员工名称"));
        fields.add(decimalHead("检验日期", "日期"));
        fields.add(text("来源单据"));
        fields.add(text("来源单号"));
        fields.add(text("备注"));

        List<Map<String, Object>> items = new ArrayList<>();
        items.add(text("产品编码"));
        items.add(ref("产品名称", "INV", "存货名称"));
        items.add(text("规格型号"));
        items.add(dropdown("计量单位", "件", List.of("件", "kg", "套", "升")));
        items.add(dropdown("仓库", "成品仓", List.of("成品仓", "原料仓", "辅料仓", "半成品仓", "不良品仓")));
        items.add(number("报检数量", true));
        items.add(number("检验数量", true));
        items.add(number("合格数量", false));
        items.add(number("不合格数量", false));
        items.add(number("让步接收数量", false));
        items.add(number("报废数量", false));
        items.add(dropdown("检验结果判定", "", List.of("合格", "让步接收", "不合格")));
        items.add(dropdown("处置方式", "原价", List.of("原价", "让步接收", "返工", "返修", "报废", "退回")));
        items.add(number("委外单价", false));
        items.add(text("备注"));

        Map<String, Object> itemsTab = new LinkedHashMap<>();
        itemsTab.put("key", "items");
        itemsTab.put("label", "明细");
        itemsTab.put("isRequired", true);
        itemsTab.put("summaryItems", List.of(
                Map.of("label", "报检数量合计", "field", "报检数量"),
                Map.of("label", "合格数量合计", "field", "合格数量"),
                Map.of("label", "不合格数量合计", "field", "不合格数量")));
        itemsTab.put("fields", items);

        Map<String, Object> config = new LinkedHashMap<>();
        config.put("metadata", metadata);
        config.put("dataSchema", Map.of("type", "object", "fields", fields));
        config.put("detail", Map.of("tabs", List.of(itemsTab)));
        return config;
    }

    private static Map<String, Object> group(String name, String... actions) {
        Map<String, Object> group = new LinkedHashMap<>();
        group.put("name", name);
        group.put("actions", new ArrayList<>(List.of(actions)));
        return group;
    }

    private static List<Map<String, Object>> buttons(String... names) {
        List<Map<String, Object>> out = new ArrayList<>();
        for (String name : names) out.add(Map.of("buttonName", name));
        return out;
    }

    private static Map<String, Object> queryField(String name, String type) {
        Map<String, Object> field = new LinkedHashMap<>();
        field.put("dataName", name);
        field.put("dataType", type);
        return field;
    }

    private static Map<String, Object> text(String name) {
        Map<String, Object> field = new LinkedHashMap<>();
        field.put("dataName", name);
        field.put("dataType", "文本");
        field.put("isRequired", false);
        field.put("defaultValue", "");
        return field;
    }

    private static Map<String, Object> decimalHead(String name, String type) {
        Map<String, Object> field = new LinkedHashMap<>();
        field.put("dataName", name);
        field.put("dataType", type);
        field.put("isRequired", false);
        field.put("defaultValue", "");
        return field;
    }

    private static Map<String, Object> number(String name, boolean required) {
        Map<String, Object> field = new LinkedHashMap<>();
        field.put("dataName", name);
        field.put("dataType", "小数");
        field.put("isRequired", required);
        field.put("defaultValue", 0);
        return field;
    }

    private static Map<String, Object> dropdown(String name, String value, List<String> options) {
        Map<String, Object> field = new LinkedHashMap<>();
        field.put("dataName", name);
        field.put("dataType", "下拉框");
        field.put("isRequired", false);
        field.put("defaultValue", value);
        field.put("options", options);
        return field;
    }

    private static Map<String, Object> ref(String name, String panel, String refField) {
        Map<String, Object> field = new LinkedHashMap<>();
        field.put("dataName", name);
        field.put("dataType", "参照");
        field.put("isRequired", false);
        field.put("defaultValue", "");
        field.put("refPanel", panel);
        field.put("refField", refField);
        field.put("displayField", refField);
        field.put("filter", Map.of("停用", false));
        return field;
    }

    private String toJson(Object value) {
        try {
            return json.writeValueAsString(value);
        } catch (Exception e) {
            throw new IllegalStateException("委外检验单配置序列化失败", e);
        }
    }
}