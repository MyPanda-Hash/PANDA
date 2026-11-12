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
 * 工序派工单（DISPATCH）面板收敛：它现在是「排产批次」载体，不再承担报工/出库生单。
 *
 * <p>报工统一在「工序报工」工作台按批次报；材料出库统一从领料申请单生。
 * 所以把面板上两个已失效的生单入口摘掉（点了只会报「生产加工单不存在」/「材料已全部领用」），
 * 并补上「中止（释放未执行量）」——按产能调整批次时把没做的量放回可排产量。
 */
@Component
public class DispatchPanelUpgrade {

    private static final List<String> DEAD_ACTIONS = List.of(
            "生成工序汇报单（自制汇报）", "生成工序汇报单（委外汇报）",
            "生成工序汇报单（自制汇报退回）", "生成工序汇报单（委外汇报退回）",
            "生成材料出库单", "生成材料出库单(分单)", "生成材料出库单（退料）");
    private static final String RELEASE = "中止（释放未执行量）";

    private final PanelConfigMapper panelMapper;
    private final ObjectMapper json;

    public DispatchPanelUpgrade(PanelConfigMapper panelMapper, ObjectMapper json) {
        this.panelMapper = panelMapper;
        this.json = json;
    }

    @PostConstruct
    public void upgrade() {
        PanelConfig row = panelMapper.selectOne(new LambdaQueryWrapper<PanelConfig>()
                .eq(PanelConfig::getPanelCode, "DISPATCH"));
        if (row == null || row.getConfig() == null) return;
        Map<String, Object> config;
        try {
            config = json.readValue(row.getConfig(), new TypeReference<Map<String, Object>>() {});
        } catch (Exception e) {
            return;
        }
        boolean changed = cleanGroups(config);
        changed |= addReleaseAction(config);
        if (!changed) return;
        try {
            row.setConfig(json.writeValueAsString(config));
            row.setUpdateTime(LocalDateTime.now());
            panelMapper.updateById(row);
        } catch (Exception ignored) {
        }
    }

    @SuppressWarnings("unchecked")
    private boolean cleanGroups(Map<String, Object> config) {
        Object metadataObject = config.get("metadata");
        if (!(metadataObject instanceof Map<?, ?> metadataRaw)) return false;
        Map<String, Object> metadata = (Map<String, Object>) metadataRaw;
        boolean changed = false;
        List<Map<String, Object>> groups = new ArrayList<>();
        for (Object groupObject : listOf(metadata.get("buttonGroups"))) {
            if (!(groupObject instanceof Map<?, ?> groupRaw)) continue;
            Map<String, Object> group = (Map<String, Object>) groupRaw;
            Object actionsObject = group.get("actions");
            if (!(actionsObject instanceof List<?> actions)) continue;
            List<String> kept = new ArrayList<>();
            for (Object value : actions) {
                if (!DEAD_ACTIONS.contains(String.valueOf(value))) kept.add(String.valueOf(value));
            }
            if (kept.size() != actions.size()) {
                if (kept.isEmpty()) {
                    changed = true;               // 整组都是死按钮 → 整组去掉
                    continue;
                }
                group.put("actions", kept);
                changed = true;
            }
            groups.add(group);
        }
        if (changed) metadata.put("buttonGroups", groups);
        // panelButtons 里同样清掉
        if (metadata.get("panelButtons") instanceof List<?> buttons) {
            List<Map<String, Object>> kept = new ArrayList<>();
            for (Object value : buttons) {
                if (value instanceof Map<?, ?> raw
                        && DEAD_ACTIONS.contains(String.valueOf(raw.get("buttonName")))) {
                    changed = true;
                    continue;
                }
                kept.add(value instanceof Map<?, ?> raw
                        ? new LinkedHashMap<>((Map<String, Object>) raw) : new LinkedHashMap<>());
            }
            if (changed) metadata.put("panelButtons", kept);
        }
        return changed;
    }

    @SuppressWarnings("unchecked")
    private boolean addReleaseAction(Map<String, Object> config) {
        Object metadataObject = config.get("metadata");
        if (!(metadataObject instanceof Map<?, ?> metadataRaw)) return false;
        Map<String, Object> metadata = (Map<String, Object>) metadataRaw;
        boolean exists = false;
        for (Object groupObject : listOf(metadata.get("buttonGroups"))) {
            if (!(groupObject instanceof Map<?, ?> raw)) continue;
            for (Object action : listOf(raw.get("actions"))) {
                if (RELEASE.equals(String.valueOf(action))) exists = true;
            }
        }
        if (exists) return false;
        boolean changed = false;
        for (Object groupObject : listOf(metadata.get("buttonGroups"))) {
            if (!(groupObject instanceof Map<?, ?> raw)) continue;
            Map<String, Object> group = (Map<String, Object>) raw;
            if (!"中止".equals(String.valueOf(group.get("name")))) continue;
            @SuppressWarnings("unchecked")
            List<String> actions = new ArrayList<>((List<String>) group.get("actions"));
            actions.add(RELEASE);
            group.put("actions", actions);
            changed = true;
            break;
        }
        if (metadata.get("panelButtons") instanceof List<?> buttonsRaw) {
            List<Map<String, Object>> buttons = new ArrayList<>();
            for (Object value : buttonsRaw) {
                buttons.add(value instanceof Map<?, ?> raw
                        ? new LinkedHashMap<>((Map<String, Object>) raw) : new LinkedHashMap<>());
            }
            buttons.add(new LinkedHashMap<>(Map.of("buttonName", RELEASE)));
            metadata.put("panelButtons", buttons);
            changed = true;
        }
        return changed;
    }

    private static List<?> listOf(Object value) {
        return value instanceof List<?> list ? list : List.of();
    }
}
