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
 * 销售费用分摊闭环所需的字段与按钮：分摊单加「分摊状态 / 执行时间 + 执行分摊按钮」，
 * 销售发票加「分摊费用 / 分摊单号」。
 *
 * <p>不加这些字段，费用摊完只能在分摊单里看到，发票上没有落点，毛利就算不进去。
 */
@Component
public class SaleCostLoopRegistry {

    static final String ALLOC = "SALE_COST_ALLOC";
    static final String INVOICE = "SALE_INVOICE";
    static final String ACTION = "执行分摊";

    private final PanelConfigMapper panelMapper;
    private final ObjectMapper json;

    public SaleCostLoopRegistry(PanelConfigMapper panelMapper, ObjectMapper json) {
        this.panelMapper = panelMapper;
        this.json = json;
    }

    @PostConstruct
    public void syncPanelConfigs() {
        upgrade(ALLOC, SaleCostLoopRegistry::upgradeAlloc);
        upgrade(INVOICE, SaleCostLoopRegistry::upgradeInvoice);
    }

    /** 分摊单：补执行状态字段 + 「执行分摊」按钮。 */
    static void upgradeAlloc(Map<String, Object> config) {
        ensureHeadField(config, "分摊状态", "文本", "未分摊");
        ensureHeadField(config, "执行时间", "文本", "");
        ensureButton(config, "工具", ACTION, "执行分摊");
    }

    /** 销售发票：补分摊费用与来源分摊单号，费用才能在票面/毛利口径上体现。 */
    static void upgradeInvoice(Map<String, Object> config) {
        ensureHeadField(config, "分摊费用", "小数", 0);
        ensureHeadField(config, "分摊单号", "文本", "");
    }

    // ---------- 字段 / 按钮 ----------

    @SuppressWarnings("unchecked")
    static boolean ensureHeadField(Map<String, Object> config, String name, String type, Object defaultValue) {
        Map<String, Object> schema = config.get("dataSchema") instanceof Map<?, ?> m
                ? (Map<String, Object>) m : new LinkedHashMap<>();
        List<Map<String, Object>> fields = schema.get("fields") instanceof List<?> list
                ? new ArrayList<>((List<Map<String, Object>>) list) : new ArrayList<>();
        for (Map<String, Object> field : fields) {
            if (name.equals(field.get("dataName"))) return false;
        }
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

    /** 按钮补进指定分组；分组不存在就建一个，已存在则跳过。 */
    @SuppressWarnings("unchecked")
    static boolean ensureButton(Map<String, Object> config, String groupName, String action, String label) {
        Map<String, Object> metadata = config.get("metadata") instanceof Map<?, ?> m
                ? (Map<String, Object>) m : new LinkedHashMap<>();
        List<Map<String, Object>> groups = metadata.get("buttonGroups") instanceof List<?> list
                ? new ArrayList<>((List<Map<String, Object>>) list) : new ArrayList<>();
        for (Map<String, Object> group : groups) {
            if (!groupName.equals(group.get("name"))) continue;
            List<Object> actions = group.get("actions") instanceof List<?> a
                    ? new ArrayList<>((List<Object>) a) : new ArrayList<>();
            if (actions.contains(action)) return false;
            actions.add(action);
            group.put("actions", actions);
            metadata.put("buttonGroups", groups);
            config.put("metadata", metadata);
            return true;
        }
        Map<String, Object> group = new LinkedHashMap<>();
        group.put("name", groupName);
        group.put("actions", new ArrayList<>(List.of(action)));
        groups.add(group);
        metadata.put("buttonGroups", groups);
        // 面板按钮清单也要有，否则工具栏设置里看不到
        List<Object> buttons = metadata.get("panelButtons") instanceof List<?> b
                ? new ArrayList<>((List<Object>) b) : new ArrayList<>();
        buttons.add(Map.of("buttonName", label));
        metadata.put("panelButtons", buttons);
        config.put("metadata", metadata);
        return true;
    }

    private void upgrade(String panelCode, java.util.function.Consumer<Map<String, Object>> upgrader) {
        PanelConfig row = panelMapper.selectOne(new LambdaQueryWrapper<PanelConfig>()
                .eq(PanelConfig::getPanelCode, panelCode));
        if (row == null || row.getConfig() == null || row.getConfig().isBlank()) return;
        Map<String, Object> config;
        try {
            config = json.readValue(row.getConfig(), new TypeReference<>() {});
        } catch (Exception e) {
            return;
        }
        upgrader.accept(config);
        row.setConfig(toJson(config));
        row.setUpdateTime(LocalDateTime.now());
        panelMapper.updateById(row);
    }

    private String toJson(Map<String, Object> config) {
        try {
            return json.writeValueAsString(config);
        } catch (Exception e) {
            throw new IllegalStateException("费用分摊面板配置序列化失败", e);
        }
    }
}
