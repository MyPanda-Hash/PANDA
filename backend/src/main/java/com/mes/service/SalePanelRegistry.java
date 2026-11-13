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
 * 销售订单的委外入口补齐。
 *
 * <p>背景（2026-11-13 用户口径）：销售订单的生单必须能出「委外加工单」，否则委外这条线
 * 只能靠手工新建委外单再选单带入，流程图上「销售订单 → 委外加工单」这一段点不动。
 *
 * <p>幂等：只补缺失的动作与顶部按钮，不覆盖管理员对其它按钮的调整，不动其它面板。
 * 生单互斥（委外 / 自制 / 出库三条线只能走一条）在 {@code PxService} 里拦截。
 */
@Component
public class SalePanelRegistry {

    static final String VERSION = "SL1.0";
    static final String ACTION = "生成委外加工单";
    static final String GROUP = "生单";

    private final PanelConfigMapper panelMapper;
    private final ObjectMapper json;

    public SalePanelRegistry(PanelConfigMapper panelMapper, ObjectMapper json) {
        this.panelMapper = panelMapper;
        this.json = json;
    }

    @PostConstruct
    public void syncPanelConfigs() {
        PanelConfig row = panelMapper.selectOne(new LambdaQueryWrapper<PanelConfig>()
                .eq(PanelConfig::getPanelCode, "SO_ORDER"));
        if (row == null || row.getConfig() == null) return;
        Map<String, Object> config;
        try {
            config = json.readValue(row.getConfig(), new TypeReference<Map<String, Object>>() { });
        } catch (Exception e) {
            throw new IllegalStateException("销售订单配置解析失败", e);
        }
        ensureOutsourceAction(config);
        try {
            row.setConfig(json.writeValueAsString(config));
            row.setVersion(VERSION);
            row.setUpdateTime(LocalDateTime.now());
            panelMapper.updateById(row);
        } catch (Exception e) {
            throw new IllegalStateException("销售订单委外入口补齐失败", e);
        }
    }

    /** 幂等把「生成委外加工单」加进生单分组与顶部按钮（已有则跳过）。 */
    @SuppressWarnings("unchecked")
    static void ensureOutsourceAction(Map<String, Object> config) {
        Map<String, Object> metadata = config.get("metadata") instanceof Map<?, ?> m
                ? (Map<String, Object>) m : new LinkedHashMap<>();

        List<Map<String, Object>> groups = metadata.get("buttonGroups") instanceof List<?> list
                ? (List<Map<String, Object>>) list : new ArrayList<>();
        boolean found = false;
        for (Map<String, Object> group : groups) {
            if (!GROUP.equals(String.valueOf(group.get("name")))) continue;
            found = true;
            List<Object> actions = group.get("actions") instanceof List<?> a
                    ? new ArrayList<>((List<Object>) a) : new ArrayList<>();
            if (actions.stream().noneMatch(ACTION::equals)) actions.add(ACTION);
            group.put("actions", actions);
        }
        if (!found) {
            Map<String, Object> group = new LinkedHashMap<>();
            group.put("name", GROUP);
            group.put("actions", new ArrayList<>(List.of(ACTION)));
            groups.add(group);
        }
        metadata.put("buttonGroups", groups);

        List<Map<String, Object>> buttons = metadata.get("panelButtons") instanceof List<?> list
                ? (List<Map<String, Object>>) list : new ArrayList<>();
        if (buttons.stream().noneMatch(b -> ACTION.equals(String.valueOf(b.get("buttonName"))))) {
            Map<String, Object> button = new LinkedHashMap<>();
            button.put("buttonName", ACTION);
            buttons.add(button);
        }
        metadata.put("panelButtons", buttons);
        config.put("metadata", metadata);
    }
}
