package com.mes.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.mes.entity.PanelConfig;
import com.mes.mapper.PanelConfigMapper;
import jakarta.annotation.PostConstruct;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 主生产计划（MPS）面板：把销售订单需求变成「某计划期内要做多少、什么时候开工/完工」的计划单。
 *
 * <p>此前 `MPS_PLAN` 只有一行报表定义、没有任何实现与数据；计划分析只能一张订单一张订单看，
 * 没有横向汇总的计划期概念。这里补的是一张单据：明细 = 成品行 + 计划数量 + 开工/完工日期 + 来源订单行，
 * 由「销售订单生成」写入（幂等，已计划的数量不重复计划），MRP 汇总再基于这些行展开。
 */
@Component
public class MpsPanelRegistry {

    static final String PANEL = "MPS_PLAN";

    private final PanelConfigMapper panelMapper;

    public MpsPanelRegistry(PanelConfigMapper panelMapper) {
        this.panelMapper = panelMapper;
    }

    @PostConstruct
    public void syncPanelConfigs() {
        upsert(PANEL, "主生产计划", "单据", config());
    }

    private Map<String, Object> config() {
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("panelCode", PANEL);
        metadata.put("panelName", "主生产计划");
        metadata.put("panelCategory", "单据");
        metadata.put("autoCodeField", "单据编号");
        metadata.put("panelState", Map.of("dataName", "单据状态", "dataType", "STRING",
                "defaultOptions", List.of("草稿", "已确认")));
        metadata.put("panelButtons", List.of(
                Map.of("buttonName", "新增流程"), Map.of("buttonName", "保存"),
                Map.of("buttonName", "删除"), Map.of("buttonName", "刷新")));
        metadata.put("buttonGroups", List.of(
                Map.of("name", "新增", "actions", List.of("新增流程")),
                Map.of("name", "保存", "actions", List.of("保存")),
                Map.of("name", "删除", "actions", List.of("删除")),
                Map.of("name", "查找", "actions", List.of("查找", "刷新"))));
        metadata.put("panelPageDto", Map.of(
                "tablePages", List.of(Map.of(
                        "tableName", "主生产计划列表",
                        "queryFields", List.of(
                                Map.of("dataName", "计划期", "dataType", "文本"),
                                Map.of("dataName", "单据编号", "dataType", "文本")),
                        "gridTabs", List.of(Map.of(
                                "label", "计划明细",
                                "rowSource", "detail",
                                "columns", List.of("存货编码", "存货名称", "规格型号", "计量单位",
                                        "计划数量", "计划开工日期", "计划完工日期",
                                        "来源销售订单", "来源订单行号"))),
                        "topBarBtn", List.of(Map.of("buttonName", "新增流程"), Map.of("buttonName", "刷新")),
                        "rowOperationBarBtn", List.of(),
                        "events", List.of())),
                "formPages", List.of(Map.of(
                        "formName", "主生产计划",
                        "fieldNames", "单据日期,单据编号,计划期,计划名称,备注",
                        "bottomOperationBarBtn", List.of(Map.of("buttonName", "保存"),
                                Map.of("buttonName", "删除"))))));

        List<Map<String, Object>> headFields = List.of(
                field("单据日期", "日期", true, ""),
                field("单据编号", "文本", true, ""),
                field("计划期", "文本", true, ""),
                field("计划名称", "文本", false, ""),
                field("备注", "文本", false, ""));

        List<Map<String, Object>> itemFields = List.of(
                field("存货编码", "文本", true, ""),
                field("存货名称", "文本", true, ""),
                field("规格型号", "文本", false, ""),
                field("计量单位", "文本", false, ""),
                field("计划数量", "小数", true, 0),
                field("计划开工日期", "日期", false, ""),
                field("计划完工日期", "日期", false, ""),
                field("来源销售订单", "文本", false, ""),
                field("来源订单行号", "整数", false, 0));

        Map<String, Object> config = new LinkedHashMap<>();
        config.put("metadata", metadata);
        config.put("dataSchema", Map.of("type", "object", "fields", headFields));
        config.put("detail", Map.of("tabs", List.of(Map.of(
                "key", "items", "label", "计划明细", "isRequired", true, "fields", itemFields))));
        return config;
    }

    private static Map<String, Object> field(String name, String type, boolean required, Object def) {
        Map<String, Object> field = new LinkedHashMap<>();
        field.put("dataName", name);
        field.put("dataType", type);
        field.put("isRequired", required);
        field.put("defaultValue", def);
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
        // 历史遗留：MPS_PLAN 原来只是一行「报表」占位（没有实现、没有数据）。
        // 占位面板直接升级成单据；真单据（分类已是单据）才遵循「只补不覆盖」，
        // 免得把管理员在页面上调过的字段/列宽改回去。
        boolean placeholder = row.getCategory() == null || !"单据".equals(row.getCategory());
        boolean blankConfig = row.getConfig() == null || row.getConfig().isBlank()
                || "{}".equals(row.getConfig());
        if (placeholder || blankConfig) {
            row.setPanelName(name);
            row.setCategory(category);
            row.setConfig(toJson(config));
            row.setUpdateTime(now);
            panelMapper.updateById(row);
        }
    }

    private String toJson(Map<String, Object> config) {
        try {
            return new com.fasterxml.jackson.databind.ObjectMapper().writeValueAsString(config);
        } catch (Exception e) {
            throw new IllegalStateException("主生产计划面板配置序列化失败", e);
        }
    }
}
