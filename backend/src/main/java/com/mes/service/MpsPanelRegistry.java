package com.mes.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.PanelConfig;
import com.mes.mapper.PanelConfigMapper;
import jakarta.annotation.PostConstruct;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 计划面板：MPS 主生产计划。
 * 单厂按单生产，需求只认已审核销售订单；数据源在 MpsQueryService。
 */
@Component
public class MpsPanelRegistry {

    static final String VERSION = "MPS1.0";

    private final PanelConfigMapper panelMapper;
    private final ObjectMapper json;

    public MpsPanelRegistry(PanelConfigMapper panelMapper, ObjectMapper json) {
        this.panelMapper = panelMapper;
        this.json = json;
    }

    @PostConstruct
    public void syncPanelConfigs() {
        upsert("MPS_PLAN", "主生产计划", "报表", planConfig());
        upsert("MRP_RESULT", "物料需求运算结果", "报表", mrpConfig());
        upsert("KIT_ANALYSIS", "齐套分析", "报表", kitConfig());
    }

    private Map<String, Object> kitConfig() {
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("panelCode", "KIT_ANALYSIS");
        metadata.put("panelName", "齐套分析");
        metadata.put("panelCategory", "报表");
        metadata.put("panelButtons", List.of(Map.of("buttonName", "刷新"), Map.of("buttonName", "导出")));
        metadata.put("buttonGroups", List.of(
                Map.of("name", "查找", "actions", List.of("查找", "刷新")),
                Map.of("name", "更多", "actions", List.of("导出", "退出"))));
        metadata.put("panelPageDto", Map.of(
                "tablePages", List.of(Map.of(
                        "tableName", "齐套分析",
                        "queryFields", List.of(
                                Map.of("dataName", "成品编码", "dataType", "文本"),
                                Map.of("dataName", "子件名称", "dataType", "文本"),
                                Map.of("dataName", "齐套状态", "dataType", "下拉框",
                                        "options", List.of("齐套", "缺料"))),
                        "gridTabs", List.of(Map.of(
                                "label", "齐套明细",
                                "rowSource", "rows",
                                "columns", List.of("成品编码", "成品名称", "需求数量", "子件编码", "子件名称",
                                        "需要数量", "现存量", "缺料数量", "满足率%", "齐套状态",
                                        "客户", "需求交期"))),
                        "topBarBtn", List.of(Map.of("buttonName", "刷新")),
                        "rowOperationBarBtn", List.of(),
                        "events", List.of())),
                "formPages", List.of()));

        Map<String, Object> config = new LinkedHashMap<>();
        config.put("metadata", metadata);
        config.put("dataSchema", Map.of("type", "object", "fields", List.of()));
        config.put("detail", Map.of("tabs", List.of()));
        return config;
    }

    private Map<String, Object> mrpConfig() {
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("panelCode", "MRP_RESULT");
        metadata.put("panelName", "物料需求运算结果");
        metadata.put("panelCategory", "报表");
        metadata.put("panelButtons", List.of(Map.of("buttonName", "刷新"), Map.of("buttonName", "导出")));
        metadata.put("buttonGroups", List.of(
                Map.of("name", "查找", "actions", List.of("查找", "刷新")),
                Map.of("name", "更多", "actions", List.of("导出", "退出"))));
        metadata.put("panelPageDto", Map.of(
                "tablePages", List.of(Map.of(
                        "tableName", "物料需求运算结果",
                        "queryFields", List.of(
                                Map.of("dataName", "物料编码", "dataType", "文本"),
                                Map.of("dataName", "物料名称", "dataType", "文本"),
                                Map.of("dataName", "供应方式", "dataType", "下拉框",
                                        "options", List.of("采购", "自制"))),
                        "gridTabs", List.of(Map.of(
                                "label", "需求明细",
                                "rowSource", "rows",
                                "columns", List.of("物料编码", "物料名称", "供应方式",
                                        "BOM层级", "毛需求", "现存量", "净需求", "建议数量"))),
                        "topBarBtn", List.of(Map.of("buttonName", "刷新")),
                        "rowOperationBarBtn", List.of(),
                        "events", List.of())),
                "formPages", List.of()));

        Map<String, Object> config = new LinkedHashMap<>();
        config.put("metadata", metadata);
        config.put("dataSchema", Map.of("type", "object", "fields", List.of()));
        config.put("detail", Map.of("tabs", List.of()));
        return config;
    }

    private Map<String, Object> planConfig() {
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("panelCode", "MPS_PLAN");
        metadata.put("panelName", "主生产计划");
        metadata.put("panelCategory", "报表");
        metadata.put("panelButtons", List.of(Map.of("buttonName", "刷新"), Map.of("buttonName", "导出")));
        metadata.put("buttonGroups", List.of(
                Map.of("name", "查找", "actions", List.of("查找", "刷新")),
                Map.of("name", "更多", "actions", List.of("导出", "退出"))));
        metadata.put("panelPageDto", Map.of(
                "tablePages", List.of(Map.of(
                        "tableName", "主生产计划",
                        "queryFields", List.of(
                                Map.of("dataName", "存货编码", "dataType", "文本"),
                                Map.of("dataName", "存货名称", "dataType", "文本"),
                                Map.of("dataName", "客户", "dataType", "文本"),
                                Map.of("dataName", "优先级", "dataType", "下拉框",
                                        "options", List.of("特急", "紧急", "正常"))),
                        "gridTabs", List.of(Map.of(
                                "label", "待排产",
                                "rowSource", "rows",
                                "columns", List.of("存货编码", "存货名称", "规格型号", "计量单位",
                                        "待排产数量", "客户", "需求交期", "建议开工", "优先级", "需求来源"))),
                        "topBarBtn", List.of(Map.of("buttonName", "刷新")),
                        "rowOperationBarBtn", List.of(),
                        "events", List.of())),
                "formPages", List.of()));

        Map<String, Object> config = new LinkedHashMap<>();
        config.put("metadata", metadata);
        config.put("dataSchema", Map.of("type", "object", "fields", List.of()));
        config.put("detail", Map.of("tabs", List.of()));
        return config;
    }

    private void upsert(String code, String name, String category, Map<String, Object> config) {
        PanelConfig row = panelMapper.selectOne(new LambdaQueryWrapper<PanelConfig>()
                .eq(PanelConfig::getPanelCode, code));
        LocalDateTime now = LocalDateTime.now();
        if (row == null) {
            row = new PanelConfig();
            row.setPanelCode(code);
            row.setCreateTime(now);
        }
        row.setPanelName(name);
        row.setCategory(category);
        row.setModuleGroup("plan");
        row.setConfig(toJson(config));
        row.setVersion(VERSION);
        row.setUpdateTime(now);
        if (row.getId() == null) panelMapper.insert(row);
        else panelMapper.updateById(row);
    }

    private String toJson(Object value) {
        try {
            return json.writeValueAsString(value);
        } catch (Exception e) {
            throw new IllegalStateException("计划面板配置序列化失败", e);
        }
    }
}
