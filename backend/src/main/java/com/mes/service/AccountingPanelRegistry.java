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
 * 账务面板：会计科目、凭证查询、科目余额表、明细账。
 * 报表类只读；科目为档案。
 */
@Component
public class AccountingPanelRegistry {

    static final String VERSION = "ACC1.0";

    private final PanelConfigMapper panelMapper;
    private final ObjectMapper json;

    public AccountingPanelRegistry(PanelConfigMapper panelMapper, ObjectMapper json) {
        this.panelMapper = panelMapper;
        this.json = json;
    }

    @PostConstruct
    public void syncPanelConfigs() {
        upsert("ACCOUNT", "会计科目", "基础档案", accountConfig());
        upsert("ACCOUNTING_ENTRY", "凭证查询", "报表", entryConfig());
        upsert("ACCOUNT_BALANCE", "科目余额表", "报表", balanceConfig());
        upsert("ACCOUNT_DETAIL", "明细账", "报表", detailConfig());
    }

    private Map<String, Object> accountConfig() {
        Map<String, Object> metadata = reportShell("ACCOUNT", "会计科目", List.of(
                Map.of("dataName", "科目编码", "dataType", "文本"),
                Map.of("dataName", "科目名称", "dataType", "文本")),
                List.of("科目编码", "科目名称", "科目类别", "余额方向", "启用状态"));
        Map<String, Object> config = new LinkedHashMap<>();
        config.put("metadata", metadata);
        config.put("dataSchema", Map.of("type", "object", "fields",
                List.of(field("科目编码", true), field("科目名称", true),
                        field("科目类别", false), field("余额方向", false))));
        config.put("detail", Map.of("tabs", List.of()));
        return config;
    }

    private Map<String, Object> entryConfig() {
        Map<String, Object> metadata = reportShell("ACCOUNTING_ENTRY", "凭证查询", List.of(
                Map.of("dataName", "业务日期", "dataType", "日期"),
                Map.of("dataName", "凭证号", "dataType", "文本"),
                Map.of("dataName", "科目编码", "dataType", "文本"),
                Map.of("dataName", "往来单位", "dataType", "文本")),
                List.of("业务日期", "凭证号", "科目编码", "科目名称", "方向", "金额",
                        "往来单位", "摘要", "来源单号"));
        Map<String, Object> config = new LinkedHashMap<>();
        config.put("metadata", metadata);
        config.put("dataSchema", Map.of("type", "object", "fields", List.of()));
        config.put("detail", Map.of("tabs", List.of()));
        return config;
    }

    private Map<String, Object> balanceConfig() {
        Map<String, Object> metadata = reportShell("ACCOUNT_BALANCE", "科目余额表", List.of(
                Map.of("dataName", "科目编码", "dataType", "文本"),
                Map.of("dataName", "科目名称", "dataType", "文本")),
                List.of("科目编码", "科目名称", "借方发生额", "贷方发生额", "余额方向", "余额"));
        Map<String, Object> config = new LinkedHashMap<>();
        config.put("metadata", metadata);
        config.put("dataSchema", Map.of("type", "object", "fields", List.of()));
        config.put("detail", Map.of("tabs", List.of()));
        return config;
    }

    private Map<String, Object> detailConfig() {
        Map<String, Object> metadata = reportShell("ACCOUNT_DETAIL", "明细账", List.of(
                Map.of("dataName", "科目编码", "dataType", "文本"),
                Map.of("dataName", "开始日期", "dataType", "日期"),
                Map.of("dataName", "结束日期", "dataType", "日期")),
                List.of("业务日期", "凭证号", "科目编码", "科目名称", "方向", "金额",
                        "往来单位", "摘要", "来源面板", "来源单号"));
        Map<String, Object> config = new LinkedHashMap<>();
        config.put("metadata", metadata);
        config.put("dataSchema", Map.of("type", "object", "fields", List.of()));
        config.put("detail", Map.of("tabs", List.of()));
        return config;
    }

    private Map<String, Object> reportShell(String code, String name,
                                            List<Map<String, Object>> queryFields,
                                            List<String> columns) {
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("panelCode", code);
        metadata.put("panelName", name);
        metadata.put("panelCategory", "报表");
        metadata.put("panelButtons", List.of(Map.of("buttonName", "刷新"), Map.of("buttonName", "导出")));
        metadata.put("buttonGroups", List.of(
                Map.of("name", "查找", "actions", List.of("查找", "刷新")),
                Map.of("name", "更多", "actions", List.of("导出", "退出"))));
        metadata.put("panelPageDto", Map.of(
                "tablePages", List.of(Map.of(
                        "tableName", name,
                        "queryFields", queryFields,
                        "gridTabs", List.of(Map.of("label", "列表", "rowSource", "rows", "columns", columns)),
                        "topBarBtn", List.of(Map.of("buttonName", "刷新")),
                        "rowOperationBarBtn", List.of(),
                        "events", List.of())),
                "formPages", List.of()));
        return metadata;
    }

    private Map<String, Object> field(String name, boolean required) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("dataName", name);
        m.put("dataType", "文本");
        m.put("isRequired", required);
        return m;
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
        row.setModuleGroup("accounting");
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
            throw new IllegalStateException("账务面板配置序列化失败", e);
        }
    }
}
