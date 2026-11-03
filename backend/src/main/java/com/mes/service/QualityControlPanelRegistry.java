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
 * 质量管控面板：SPC 控制图。
 * 数据源在 SpcQueryService，从已审核检验单的实测值算控制限。
 */
@Component
public class QualityControlPanelRegistry {

    static final String VERSION = "QMS2.0";

    private final PanelConfigMapper panelMapper;
    private final ObjectMapper json;

    public QualityControlPanelRegistry(PanelConfigMapper panelMapper, ObjectMapper json) {
        this.panelMapper = panelMapper;
        this.json = json;
    }

    @PostConstruct
    public void syncPanelConfigs() {
        upsert("SPC_CONTROL", "SPC 控制图", "报表", spcConfig());
    }

    private Map<String, Object> spcConfig() {
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("panelCode", "SPC_CONTROL");
        metadata.put("panelName", "SPC 控制图");
        metadata.put("panelCategory", "报表");
        metadata.put("panelButtons", List.of(Map.of("buttonName", "刷新"), Map.of("buttonName", "导出")));
        metadata.put("buttonGroups", List.of(
                Map.of("name", "查找", "actions", List.of("查找", "刷新")),
                Map.of("name", "更多", "actions", List.of("导出", "退出"))));
        metadata.put("panelPageDto", Map.of(
                "tablePages", List.of(Map.of(
                        "tableName", "控制图参数",
                        "queryFields", List.of(
                                Map.of("dataName", "检验项目", "dataType", "文本"),
                                Map.of("dataName", "过程状态", "dataType", "下拉框",
                                        "options", List.of("受控", "失控"))),
                        "gridTabs", List.of(Map.of(
                                "label", "控制图",
                                "rowSource", "rows",
                                "columns", List.of("检验项目", "特性值", "样本数", "均值", "标准差",
                                        "控制上限", "控制下限", "越界点数", "过程状态"))),
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
        row.setModuleGroup("qc");
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
            throw new IllegalStateException("质量管控面板配置序列化失败", e);
        }
    }
}
