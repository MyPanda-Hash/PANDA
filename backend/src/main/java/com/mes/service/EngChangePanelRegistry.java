package com.mes.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.PanelConfig;
import com.mes.mapper.PanelConfigMapper;
import jakarta.annotation.PostConstruct;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 工程变更单（ECR/ECO）面板，并给物料清单 / 工艺路线补上版本字段。
 *
 * <p>此前 PDM 只有主数据（存货、BOM、工艺路线），没有「谁改的、改了什么、现在是第几版」的记录：
 * BOM 行上有版本号，但没有任何单据能驱动它变化，也没法追溯。这里补一张变更单：
 * 明细写「变更对象 + 变更级别」，审核后执行，把目标单据的版本号往上抬一版并盖章变更单号。
 */
@Component
public class EngChangePanelRegistry {

    static final String PANEL = "ENG_CHANGE";
    static final String PANEL_BOM = "BOM";
    static final String PANEL_ROUTE = "ROUTE";

    private final PanelConfigMapper panelMapper;
    private final ObjectMapper json;

    public EngChangePanelRegistry(PanelConfigMapper panelMapper, ObjectMapper json) {
        this.panelMapper = panelMapper;
        this.json = json;
    }

    @PostConstruct
    public void syncPanelConfigs() {
        upsert(PANEL, "工程变更单", "单据", changeConfig());
        upgradeVersionFields(PANEL_BOM);
        upgradeVersionFields(PANEL_ROUTE);
    }

    private Map<String, Object> changeConfig() {
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("panelCode", PANEL);
        metadata.put("panelName", "工程变更单");
        metadata.put("panelCategory", "单据");
        metadata.put("autoCodeField", "单据编号");
        metadata.put("panelState", Map.of("dataName", "状态", "dataType", "STRING",
                "defaultOptions", List.of("草稿", "已审核", "已中止")));
        metadata.put("panelButtons", List.of(
                Map.of("buttonName", "新增流程"), Map.of("buttonName", "保存"),
                Map.of("buttonName", "审核"), Map.of("buttonName", "弃审"),
                Map.of("buttonName", "删除"), Map.of("buttonName", "刷新")));
        metadata.put("buttonGroups", List.of(
                Map.of("name", "新增", "actions", List.of("新增流程")),
                Map.of("name", "保存", "actions", List.of("保存", "保存为草稿")),
                Map.of("name", "审核", "actions", List.of("审核", "弃审")),
                Map.of("name", "删除", "actions", List.of("删除")),
                Map.of("name", "查找", "actions", List.of("查找", "刷新"))));
        metadata.put("panelPageDto", Map.of(
                "tablePages", List.of(Map.of(
                        "tableName", "工程变更单列表",
                        "queryFields", List.of(
                                Map.of("dataName", "单据编号", "dataType", "文本"),
                                Map.of("dataName", "申请人", "dataType", "文本")),
                        "gridTabs", List.of(Map.of(
                                "label", "变更明细",
                                "rowSource", "detail",
                                "columns", List.of("对象类型", "对象编码", "变更级别", "变更说明"))),
                        "topBarBtn", List.of(Map.of("buttonName", "新增流程"), Map.of("buttonName", "刷新")),
                        "rowOperationBarBtn", List.of(),
                        "events", List.of())),
                "formPages", List.of(Map.of(
                        "formName", "工程变更单",
                        "fieldNames", "单据日期,单据编号,申请人,变更原因,备注",
                        "bottomOperationBarBtn", List.of(Map.of("buttonName", "保存"),
                                Map.of("buttonName", "审核"))))));

        List<Map<String, Object>> headFields = List.of(
                field("单据日期", "日期", true, ""),
                field("单据编号", "文本", true, ""),
                field("申请人", "文本", false, ""),
                field("变更原因", "文本", false, ""),
                field("备注", "文本", false, ""));

        List<Map<String, Object>> items = List.of(
                withOptions(field("对象类型", "下拉框", true, "物料清单"), List.of("物料清单", "工艺路线")),
                field("对象编码", "文本", true, ""),
                withOptions(field("变更级别", "下拉框", true, "小版本"), List.of("小版本", "大版本")),
                field("变更说明", "文本", false, ""));

        Map<String, Object> config = new LinkedHashMap<>();
        config.put("metadata", metadata);
        config.put("dataSchema", Map.of("type", "object", "fields", headFields));
        config.put("detail", Map.of("tabs", List.of(Map.of(
                "key", "items", "label", "变更明细", "isRequired", true, "fields", items))));
        return config;
    }

    /** 物料清单 / 工艺路线表头补三个版本字段：改版必须有地方落版本与变更单号。 */
    private void upgradeVersionFields(String panelCode) {
        PanelConfig row = panelMapper.selectOne(new LambdaQueryWrapper<PanelConfig>()
                .eq(PanelConfig::getPanelCode, panelCode));
        if (row == null || row.getConfig() == null || row.getConfig().isBlank()) return;
        Map<String, Object> config = parse(row.getConfig());
        if (config.isEmpty()) return;
        boolean changed = false;
        changed |= ensureHeadField(config, "版本号", "文本", "V1.0");
        changed |= ensureHeadField(config, "变更单号", "文本", "");
        changed |= ensureHeadField(config, "最后变更日期", "日期", "");
        if (!changed) return;
        row.setConfig(toJson(config));
        row.setUpdateTime(LocalDateTime.now());
        panelMapper.updateById(row);
    }

    @SuppressWarnings("unchecked")
    private boolean ensureHeadField(Map<String, Object> config, String name, String type, Object defaultValue) {
        Map<String, Object> schema = config.get("dataSchema") instanceof Map<?, ?> m
                ? (Map<String, Object>) m : new LinkedHashMap<>();
        List<Map<String, Object>> fields = schema.get("fields") instanceof List<?> list
                ? new ArrayList<>((List<Map<String, Object>>) list) : new ArrayList<>();
        Map<String, Object> target = null;
        for (Map<String, Object> field : fields) {
            if (name.equals(field.get("dataName"))) {
                target = field;
                break;
            }
        }
        if (target != null) return false;
        Map<String, Object> created = new LinkedHashMap<>();
        created.put("dataName", name);
        created.put("dataType", type);
        created.put("isRequired", false);
        created.put("defaultValue", defaultValue);
        fields.add(created);
        schema.put("type", "object");
        schema.put("fields", fields);
        config.put("dataSchema", schema);
        return true;
    }

    private static Map<String, Object> field(String name, String type, boolean required, Object def) {
        Map<String, Object> field = new LinkedHashMap<>();
        field.put("dataName", name);
        field.put("dataType", type);
        field.put("isRequired", required);
        field.put("defaultValue", def);
        return field;
    }

    private static Map<String, Object> withOptions(Map<String, Object> field, List<String> options) {
        field.put("options", options);
        return field;
    }

    private void upsert(String code, String name, String category, Map<String, Object> config) {
        PanelConfig row = panelMapper.selectOne(new LambdaQueryWrapper<PanelConfig>()
                .eq(PanelConfig::getPanelCode, code));
        LocalDateTime now = LocalDateTime.now();
        if (row == null) {
            row = new PanelConfig();
            row.setPanelCode(code);
            row.setPanelName(name);
            row.setCategory(category);
            row.setConfig(toJson(config));
            row.setCreateTime(now);
            row.setUpdateTime(now);
            panelMapper.insert(row);
            return;
        }
        if (row.getConfig() == null || row.getConfig().isBlank() || "{}".equals(row.getConfig())) {
            row.setPanelName(name);
            row.setCategory(category);
            row.setConfig(toJson(config));
            row.setUpdateTime(now);
            panelMapper.updateById(row);
        }
    }

    private Map<String, Object> parse(String raw) {
        try {
            return json.readValue(raw, new TypeReference<>() {});
        } catch (Exception e) {
            return Map.of();
        }
    }

    private String toJson(Map<String, Object> config) {
        try {
            return json.writeValueAsString(config);
        } catch (Exception e) {
            throw new IllegalStateException("工程变更单面板配置序列化失败", e);
        }
    }
}
