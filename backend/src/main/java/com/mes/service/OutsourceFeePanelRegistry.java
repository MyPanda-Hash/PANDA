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
 * 委外加工费用单的自动计算配置修正。
 *
 * <p>缺陷（2026-11-13 用户反馈「委外费用要自动计算总的费用金额」「单价不能有小数」）：
 * 明细配置里的 calc 是 `金额 = 数量 * 单价`，但这张单的列叫「费用金额」「委外单价」——
 * 两个变量都不存在，{@code evaluateExpr} 抛「未知变量」被吞成 0，所以填了数量×单价后
 * 费用金额永远是 0，表头「费用合计」也没有任何汇总规则。
 *
 * <p>本类在启动时把 calc 修正为真实列名，并补一份 {@code formSummary} 让表头「费用合计」
 * 由明细自动汇总。幂等：重复执行不追加第二条。
 */
@Component
public class OutsourceFeePanelRegistry {

    static final String VERSION = "OF1.0";
    static final String PANEL = "OUTSOURCE_FEE";

    private final PanelConfigMapper panelMapper;
    private final ObjectMapper json;

    public OutsourceFeePanelRegistry(PanelConfigMapper panelMapper, ObjectMapper json) {
        this.panelMapper = panelMapper;
        this.json = json;
    }

    @PostConstruct
    public void syncPanelConfigs() {
        PanelConfig row = panelMapper.selectOne(new LambdaQueryWrapper<PanelConfig>()
                .eq(PanelConfig::getPanelCode, PANEL));
        if (row == null || row.getConfig() == null) return;
        Map<String, Object> config;
        try {
            config = json.readValue(row.getConfig(), new TypeReference<Map<String, Object>>() { });
        } catch (Exception e) {
            throw new IllegalStateException("委外加工费用单配置解析失败", e);
        }
        upgrade(config);
        try {
            row.setConfig(json.writeValueAsString(config));
            row.setVersion(VERSION);
            row.setUpdateTime(LocalDateTime.now());
            panelMapper.updateById(row);
        } catch (Exception e) {
            throw new IllegalStateException("委外加工费用单计算配置修正失败", e);
        }
    }

    /**
     * 把明细金额规则改成真实列名，并让表头「费用合计」汇总明细。
     *
     * <p>只动「费用金额」这一条规则：其它面板的 calc 不归这里管。
     */
    @SuppressWarnings("unchecked")
    static void upgrade(Map<String, Object> config) {
        // 复制到可变 Map：配置可能来自不可变集合（Map.of），直接 put 会抛 UnsupportedOperationException
        Map<String, Object> detail = config.get("detail") instanceof Map<?, ?> d
                ? new LinkedHashMap<>((Map<String, Object>) d) : new LinkedHashMap<>();
        List<Map<String, Object>> tabs = detail.get("tabs") instanceof List<?> list
                ? (List<Map<String, Object>>) list : new ArrayList<>();

        for (Map<String, Object> tab : tabs) {
            List<Map<String, Object>> fields = tab.get("fields") instanceof List<?> list
                    ? (List<Map<String, Object>>) list : new ArrayList<>();
            if (fields.stream().noneMatch(f -> "费用金额".equals(String.valueOf(f.get("dataName"))))) continue;

            List<Map<String, Object>> kept = new ArrayList<>();
            if (tab.get("calc") instanceof List<?> calcList) {
                for (Object item : calcList) {
                    if (!(item instanceof Map)) continue;
                    Map<String, Object> rule = (Map<String, Object>) item;
                    String target = String.valueOf(rule.get("target"));
                    String formula = String.valueOf(rule.get("formula"));
                    // 丢掉指向不存在列的旧规则（金额 / 单价）
                    boolean stale = ("金额".equals(target) && !hasField(fields, "金额"))
                            || formula.contains("单价") && !formula.contains("委外单价")
                            || formula.startsWith("数量 * 单价") && !hasField(fields, "单价");
                    if (!stale) kept.add(rule);
                }
            }
            if (kept.stream().noneMatch(r -> "费用金额".equals(String.valueOf(r.get("target"))))) {
                Map<String, Object> rule = new LinkedHashMap<>();
                rule.put("target", "费用金额");
                rule.put("formula", "数量 * 委外单价");
                rule.put("round", 2);
                kept.add(rule);
            }
            tab.put("calc", kept);
            // 「费用金额」是算出来的：标 computed 后前端由 detailCellLocked 锁定，不让手填与计算互相覆盖
            for (Map<String, Object> field : fields) {
                if ("费用金额".equals(String.valueOf(field.get("dataName")))) {
                    field.put("computed", true);
                    field.put("isRequired", false);
                }
            }
            tab.put("fields", fields);
            // 表头「费用合计」= 本页签「费用金额」之和：前端 applyCalc 读 headerCalc 自动回写
            List<Map<String, Object>> headerCalc = tab.get("headerCalc") instanceof List<?> hc
                    ? new ArrayList<>((List<Map<String, Object>>) hc) : new ArrayList<>();
            if (headerCalc.stream().noneMatch(r -> "费用合计".equals(String.valueOf(r.get("target"))))) {
                Map<String, Object> rule = new LinkedHashMap<>();
                rule.put("target", "费用合计");
                rule.put("source", "费用金额");
                rule.put("op", "sum");
                rule.put("round", 2);
                headerCalc.add(rule);
            }
            tab.put("headerCalc", headerCalc);
        }
        detail.put("tabs", tabs);
        config.put("detail", detail);

        // 表头 费用合计：由明细汇总，只读展示
        // 复制到可变 Map：配置可能来自不可变集合（测试里常是 Map.of），直接 put 会抛 UnsupportedOperationException
        Map<String, Object> summary = config.get("formSummary") instanceof Map<?, ?> s
                ? new LinkedHashMap<>((Map<String, Object>) s) : new LinkedHashMap<>();
        summary.putIfAbsent("title", "金额信息");
        List<Object> summaryFields = summary.get("fields") instanceof List<?> f
                ? new ArrayList<>((List<Object>) f) : new ArrayList<>();
        if (summaryFields.stream().noneMatch("费用合计"::equals)) summaryFields.add("费用合计");
        summary.put("fields", summaryFields);
        config.put("formSummary", summary);
    }

    private static boolean hasField(List<Map<String, Object>> fields, String name) {
        return fields.stream().anyMatch(f -> name.equals(String.valueOf(f.get("dataName"))));
    }
}
