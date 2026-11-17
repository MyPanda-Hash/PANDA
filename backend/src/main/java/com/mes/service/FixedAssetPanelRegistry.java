package com.mes.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
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
 * 固定资产卡片面板。
 *
 * <p>卡片存在 form_data（走通用面板），不建自己的业务表 —— 与系统其他档案一致：
 * 字段改配置就够，不用改表结构。
 */
@Component
public class FixedAssetPanelRegistry {

    private static final String VERSION = "FA1.0";

    private static final List<String> FIELDS = List.of(
            "资产编码", "资产名称", "资产类别", "所属部门", "原值", "残值率%",
            "使用月数", "开始使用日期", "存放地点", "使用状态", "备注");

    private static final List<String> COLUMNS = List.of(
            "资产编码", "资产名称", "资产类别", "所属部门", "原值",
            "残值率%", "使用月数", "开始使用日期", "使用状态");

    private final PanelConfigMapper panelMapper;
    private final ObjectMapper json;

    public FixedAssetPanelRegistry(PanelConfigMapper panelMapper, ObjectMapper json) {
        this.panelMapper = panelMapper;
        this.json = json;
    }

    @PostConstruct
    public void syncPanelConfigs() {
        upsert("FA_CARD", "固定资产卡片", cardConfig());
    }

    private Map<String, Object> cardConfig() {
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("panelCode", "FA_CARD");
        metadata.put("panelName", "固定资产卡片");
        metadata.put("panelCategory", "基础档案");
        metadata.put("autoCodeField", "资产编码");
        metadata.put("panelState", Map.of("dataName", "单据状态", "dataType", "STRING",
                "defaultOptions", List.of("草稿", "已审核", "已中止")));
        metadata.put("panelButtons", List.of(
                Map.of("buttonName", "新增流程"), Map.of("buttonName", "保存"),
                Map.of("buttonName", "删除"), Map.of("buttonName", "刷新"),
                Map.of("buttonName", "审核"), Map.of("buttonName", "弃审")));
        metadata.put("buttonGroups", List.of(
                Map.of("name", "新增", "actions", List.of("新增流程")),
                Map.of("name", "保存", "actions", List.of("保存")),
                Map.of("name", "删除", "actions", List.of("删除")),
                Map.of("name", "查找", "actions", List.of("查找", "刷新")),
                Map.of("name", "审核", "actions", List.of("审核", "弃审"))));

        Map<String, Object> table = new LinkedHashMap<>();
        table.put("tableName", "固定资产");
        table.put("queryFields", List.of(
                Map.of("dataName", "资产编码", "dataType", "文本"),
                Map.of("dataName", "资产名称", "dataType", "文本"),
                Map.of("dataName", "资产类别", "dataType", "下拉框",
                        "options", List.of("房屋建筑物", "机器设备", "运输工具", "电子设备", "办公设备"))));
        table.put("gridTabs", List.of(Map.of("label", "列表", "rowSource", "rows", "columns", COLUMNS)));
        table.put("topBarBtn", List.of(Map.of("buttonName", "刷新")));
        table.put("rowOperationBarBtn", List.of());
        table.put("events", List.of());

        Map<String, Object> form = new LinkedHashMap<>();
        form.put("formName", "固定资产卡片");
        form.put("fieldNames", String.join(",", FIELDS));
        form.put("bottomOperationBarBtn", List.of(
                Map.of("buttonName", "保存"), Map.of("buttonName", "删除")));
        form.put("events", List.of());

        Map<String, Object> pageDto = new LinkedHashMap<>();
        pageDto.put("tablePages", List.of(table));
        pageDto.put("formPages", List.of(form));
        metadata.put("panelPageDto", pageDto);

        Map<String, Object> config = new LinkedHashMap<>();
        config.put("metadata", metadata);
        config.put("dataSchema", Map.of("type", "object", "fields", schemaFields()));
        config.put("detail", Map.of("tabs", List.of()));
        return config;
    }

    private List<Map<String, Object>> schemaFields() {
        List<Map<String, Object>> out = new ArrayList<>();
        for (String name : FIELDS) out.add(field(name, typeOf(name)));
        return out;
    }

    /** 字段类型：小数/整数/日期/下拉，其余按文本。类型错了表单控件就不对。 */
    private String typeOf(String name) {
        return switch (name) {
            case "原值", "残值率%" -> "小数";
            case "使用月数" -> "整数";
            case "开始使用日期" -> "日期";
            case "资产类别" -> "下拉框";
            case "使用状态" -> "下拉框";
            default -> "文本";
        };
    }

    private Map<String, Object> field(String name, String type) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("dataName", name);
        m.put("dataType", type);
        if ("资产类别".equals(name)) {
            m.put("options", List.of("房屋建筑物", "机器设备", "运输工具", "电子设备", "办公设备"));
        }
        if ("使用状态".equals(name)) {
            m.put("options", List.of("在用", "停用", "处置"));
        }
        if ("残值率%".equals(name)) m.put("defaultValue", 5);
        if ("使用状态".equals(name)) m.put("defaultValue", "在用");
        return m;
    }

    private void upsert(String code, String name, Map<String, Object> config) {
        PanelConfig row = panelMapper.selectOne(new LambdaQueryWrapper<PanelConfig>()
                .eq(PanelConfig::getPanelCode, code));
        LocalDateTime now = LocalDateTime.now();
        try {
            String text = json.writeValueAsString(config);
            if (row == null) {
                row = new PanelConfig();
                row.setPanelCode(code);
                row.setPanelName(name);
                row.setConfig(text);
                row.setVersion(VERSION);
                row.setCreateTime(now);
                row.setUpdateTime(now);
                panelMapper.insert(row);
            } else {
                row.setPanelName(name);
                row.setConfig(text);
                row.setVersion(VERSION);
                row.setUpdateTime(now);
                panelMapper.updateById(row);
            }
        } catch (Exception e) {
            throw new IllegalStateException("固定资产面板配置写入失败：" + code, e);
        }
    }
}
