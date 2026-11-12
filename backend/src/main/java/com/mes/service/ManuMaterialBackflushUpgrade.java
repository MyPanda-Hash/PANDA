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
 * 生产加工单材料行补倒冲现场字段：线边仓 / 批次号 / 实际消耗数量 / 不可倒冲，
 * 并把「倒冲差异调整」挂到「生单」按钮组。
 *
 * <p>这四个字段决定倒冲怎么落账：扣哪个仓、扣哪一批、定额之外差多少要调、哪些料不许自动扣。
 * 幂等：已补齐的库重复启动不会重复写入。
 */
@Component
public class ManuMaterialBackflushUpgrade {

    private final PanelConfigMapper panelMapper;
    private final ObjectMapper json;

    public ManuMaterialBackflushUpgrade(PanelConfigMapper panelMapper, ObjectMapper json) {
        this.panelMapper = panelMapper;
        this.json = json;
    }

    @PostConstruct
    public void upgrade() {
        PanelConfig row = panelMapper.selectOne(new LambdaQueryWrapper<PanelConfig>()
                .eq(PanelConfig::getPanelCode, "MANU_ORDER"));
        if (row == null || row.getConfig() == null) return;
        Map<String, Object> config;
        try {
            config = json.readValue(row.getConfig(), new TypeReference<Map<String, Object>>() {});
        } catch (Exception e) {
            return;
        }
        boolean changed = addMaterialFields(config);
        changed |= addGridColumns(config);
        changed |= addAction(config);
        changed |= removeLegacyReportAction(config);
        if (!changed) return;
        try {
            row.setConfig(json.writeValueAsString(config));
            row.setUpdateTime(LocalDateTime.now());
            panelMapper.updateById(row);
        } catch (Exception ignored) {
        }
    }

    private boolean addMaterialFields(Map<String, Object> config) {
        Map<String, Object> tab = materialTab(config);
        if (tab == null) return false;
        Object fieldsObject = tab.get("fields");
        if (!(fieldsObject instanceof List<?> fieldsRaw)) return false;
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> fields = (List<Map<String, Object>>) fieldsRaw;
        boolean changed = false;
        int anchor = -1;
        for (int i = 0; i < fields.size(); i++) {
            if ("材料倒冲方式".equals(String.valueOf(fields.get(i).get("dataName")))) {
                anchor = i;
                break;
            }
        }
        changed |= insertIfMissing(fields, "线边仓", anchor >= 0 ? anchor + 1 : -1,
                field("线边仓", "下拉框", false, Map.of("options", List.of("线边仓A", "线边仓B"))));
        changed |= insertIfMissing(fields, "批次号", -1, field("批次号", "文本", false, Map.of()));
        changed |= insertIfMissing(fields, "实际消耗数量", -1, field("实际消耗数量", "小数", false, Map.of()));
        changed |= insertIfMissing(fields, "不可倒冲", -1, field("不可倒冲", "是否", false, Map.of()));
        if (changed) tab.put("fields", fields);
        return changed;
    }

    private boolean addGridColumns(Map<String, Object> config) {
        Map<String, Object> tab = materialTab(config);
        if (tab == null) return false;
        String columnsKey = "columns";
        Object metadataObject = config.get("metadata");
        if (!(metadataObject instanceof Map<?, ?> metadataRaw)) return false;
        @SuppressWarnings("unchecked")
        Map<String, Object> metadata = (Map<String, Object>) metadataRaw;
        Object pageDtoObject = metadata.get("panelPageDto");
        if (!(pageDtoObject instanceof Map<?, ?> pageDtoRaw)) return false;
        @SuppressWarnings("unchecked")
        Map<String, Object> pageDto = (Map<String, Object>) pageDtoRaw;
        boolean changed = false;
        for (Object tableObject : listOf(pageDto.get("tablePages"))) {
            if (!(tableObject instanceof Map<?, ?> tableRaw)) continue;
            @SuppressWarnings("unchecked")
            Map<String, Object> table = (Map<String, Object>) tableRaw;
            for (Object gridTabObject : listOf(table.get("gridTabs"))) {
                if (!(gridTabObject instanceof Map<?, ?> gridTabRaw)) continue;
                @SuppressWarnings("unchecked")
                Map<String, Object> gridTab = (Map<String, Object>) gridTabRaw;
                if (!"materials".equals(String.valueOf(gridTab.get("rowSource")))) continue;
                Object columnsObject = gridTab.get(columnsKey);
                if (!(columnsObject instanceof List<?> columnsRaw)) continue;
                List<String> columns = new ArrayList<>();
                for (Object value : columnsRaw) columns.add(String.valueOf(value));
                for (String name : List.of("线边仓", "批次号", "实际消耗数量", "不可倒冲")) {
                    if (columns.contains(name)) continue;
                    columns.add(name);
                    changed = true;
                }
                if (changed) gridTab.put(columnsKey, columns);
            }
        }
        return changed;
    }

    /**
     * 报工已改为「工序报工」工作台按排产批次报，加工单上的「生成工序汇报单（自制汇报）」下线。
     * 直接从按钮组与 panelButtons 里摘掉，避免点到一个只会报错的按钮。
     */
    @SuppressWarnings("unchecked")
    private boolean removeLegacyReportAction(Map<String, Object> config) {
        Object metadataObject = config.get("metadata");
        if (!(metadataObject instanceof Map<?, ?> metadataRaw)) return false;
        Map<String, Object> metadata = (Map<String, Object>) metadataRaw;
        String legacy = "生成工序汇报单（自制汇报）";
        boolean changed = false;
        for (Object groupObject : listOf(metadata.get("buttonGroups"))) {
            if (!(groupObject instanceof Map<?, ?> groupRaw)) continue;
            Map<String, Object> group = (Map<String, Object>) groupRaw;
            Object actionsObject = group.get("actions");
            if (!(actionsObject instanceof List<?> list)) continue;
            List<String> actions = new ArrayList<>();
            for (Object value : list) if (!legacy.equals(String.valueOf(value))) actions.add(String.valueOf(value));
            if (actions.size() != list.size()) {
                group.put("actions", actions);
                changed = true;
            }
        }
        Object buttonsObject = metadata.get("panelButtons");
        if (buttonsObject instanceof List<?> list) {
            List<Map<String, Object>> buttons = new ArrayList<>();
            for (Object value : list) {
                if (value instanceof Map<?, ?> raw
                        && legacy.equals(String.valueOf(raw.get("buttonName")))) continue;
                buttons.add(value instanceof Map<?, ?> raw
                        ? new LinkedHashMap<>((Map<String, Object>) raw) : new LinkedHashMap<>());
            }
            if (buttons.size() != list.size()) {
                metadata.put("panelButtons", buttons);
                changed = true;
            }
        }
        return changed;
    }

    private boolean addAction(Map<String, Object> config) {
        Object metadataObject = config.get("metadata");
        if (!(metadataObject instanceof Map<?, ?> metadataRaw)) return false;
        @SuppressWarnings("unchecked")
        Map<String, Object> metadata = (Map<String, Object>) metadataRaw;
        boolean changed = false;
        for (Object groupObject : listOf(metadata.get("buttonGroups"))) {
            if (!(groupObject instanceof Map<?, ?> groupRaw)) continue;
            @SuppressWarnings("unchecked")
            Map<String, Object> group = (Map<String, Object>) groupRaw;
            if (!"生单".equals(String.valueOf(group.get("name")))) continue;
            Object actionsObject = group.get("actions");
            if (!(actionsObject instanceof List<?> actionsRaw)) continue;
            List<String> actions = new ArrayList<>();
            for (Object value : actionsRaw) actions.add(String.valueOf(value));
            if (actions.contains("倒冲差异调整")) break;
            actions.add("倒冲差异调整");
            group.put("actions", actions);
            changed = true;
            break;
        }
        for (Object buttonObject : listOf(metadata.get("panelButtons"))) {
            if (!(buttonObject instanceof Map<?, ?> buttonRaw)) continue;
            @SuppressWarnings("unchecked")
            Map<String, Object> button = (Map<String, Object>) buttonRaw;
            if ("倒冲差异调整".equals(String.valueOf(button.get("buttonName")))) return changed;
        }
        Object buttonsObject = metadata.get("panelButtons");
        if (buttonsObject instanceof List<?> buttonsRaw) {
            @SuppressWarnings("unchecked")
            List<Map<String, Object>> buttons = (List<Map<String, Object>>) buttonsRaw;
            buttons.add(new LinkedHashMap<>(Map.of("buttonName", "倒冲差异调整")));
            metadata.put("panelButtons", buttons);
            changed = true;
        }
        return changed;
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> materialTab(Map<String, Object> config) {
        Object detailObject = config.get("detail");
        if (!(detailObject instanceof Map<?, ?> detailRaw)) return null;
        Map<String, Object> detail = (Map<String, Object>) detailRaw;
        for (Object tabObject : listOf(detail.get("tabs"))) {
            if (!(tabObject instanceof Map<?, ?> tabRaw)) continue;
            Map<String, Object> tab = (Map<String, Object>) tabRaw;
            if ("materials".equals(String.valueOf(tab.get("key")))) return tab;
        }
        return null;
    }

    private boolean insertIfMissing(List<Map<String, Object>> fields, String name, int index,
                                    Map<String, Object> field) {
        for (Map<String, Object> existing : fields) {
            if (name.equals(String.valueOf(existing.get("dataName")))) return false;
        }
        if (index < 0 || index > fields.size()) fields.add(field);
        else fields.add(index, field);
        return true;
    }

    private static Map<String, Object> field(String name, String type, boolean required,
                                             Map<String, Object> extra) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("dataName", name);
        out.put("dataType", type);
        out.put("isRequired", required);
        out.putAll(extra);
        return out;
    }

    private static List<?> listOf(Object value) {
        return value instanceof List<?> list ? list : List.of();
    }
}
