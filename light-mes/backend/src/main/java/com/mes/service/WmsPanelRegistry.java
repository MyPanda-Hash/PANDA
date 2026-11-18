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
 * 仓内作业面板：拣货工作台。
 * 数据源在 PickQueryService，按已审核出库单生成拣货任务。
 */
@Component
public class WmsPanelRegistry {

    static final String VERSION = "WMS1.0";

    private final PanelConfigMapper panelMapper;
    private final ObjectMapper json;

    public WmsPanelRegistry(PanelConfigMapper panelMapper, ObjectMapper json) {
        this.panelMapper = panelMapper;
        this.json = json;
    }

    @PostConstruct
    public void syncPanelConfigs() {
        upsert("PICK_WORKBENCH", "拣货工作台", "报表", workbenchConfig());
    }

    private Map<String, Object> workbenchConfig() {
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("panelCode", "PICK_WORKBENCH");
        metadata.put("panelName", "拣货工作台");
        metadata.put("panelCategory", "报表");
        metadata.put("panelButtons", List.of(Map.of("buttonName", "刷新"), Map.of("buttonName", "导出")));
        metadata.put("buttonGroups", List.of(
                Map.of("name", "查找", "actions", List.of("查找", "刷新")),
                Map.of("name", "更多", "actions", List.of("导出", "退出"))));
        metadata.put("panelPageDto", Map.of(
                "tablePages", List.of(Map.of(
                        "tableName", "拣货任务",
                        "queryFields", List.of(
                                Map.of("dataName", "来源单号", "dataType", "文本"),
                                Map.of("dataName", "仓库", "dataType", "文本"),
                                Map.of("dataName", "库位", "dataType", "文本"),
                                Map.of("dataName", "存货名称", "dataType", "文本"),
                                Map.of("dataName", "拣货状态", "dataType", "下拉框",
                                        "options", List.of("待拣", "未拣", "部分拣货", "已拣货"))),
                        "gridTabs", List.of(Map.of(
                                "label", "待拣任务",
                                "rowSource", "rows",
                                "columns", List.of("来源面板", "来源单号", "仓库", "库位",
                                        "存货编码", "存货名称", "计划数量", "实拣数量",
                                        "差异数量", "拣货状态"))),
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
        row.setModuleGroup("wms");
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
            throw new IllegalStateException("仓内作业面板配置序列化失败", e);
        }
    }
}
