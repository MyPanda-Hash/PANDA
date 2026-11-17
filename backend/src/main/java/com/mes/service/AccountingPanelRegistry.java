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
        upsert("ACCOUNT_PERIOD", "会计期间", "报表", periodConfig());
        upsert("BALANCE_SHEET", "资产负债表", "报表",
                statementConfig("BALANCE_SHEET", "资产负债表", "截至所选期间期末的科目余额"));
        upsert("INCOME_STATEMENT", "利润表", "报表",
                statementConfig("INCOME_STATEMENT", "利润表", "所选期间的收入、成本费用与净利润"));
        upsert("CASH_FLOW", "现金流量表", "报表",
                statementConfig("CASH_FLOW", "现金流量表", "所选期间的经营/投资/筹资现金流"));
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

    /**
     * 会计期间：查看每月凭证量与开/封账状态，并在这里执行封账、反封账。
     *
     * <p>封账后该期间的凭证不可新增、不可重记、不可冲销（三条都在 AccountingService 写入口拦）。
     */
    private Map<String, Object> periodConfig() {
        Map<String, Object> metadata = reportShell("ACCOUNT_PERIOD", "会计期间",
                List.of(Map.of("dataName", "期间", "dataType", "文本"),
                        Map.of("dataName", "状态", "dataType", "下拉框",
                                "options", List.of("可记账", "已封账"))),
                List.of("期间", "状态", "凭证张数", "分录数", "借方合计", "贷方合计",
                        "封账时间", "封账人"));
        metadata.put("panelButtons", List.of(
                Map.of("buttonName", "刷新"), Map.of("buttonName", "导出"),
                Map.of("buttonName", "封账"), Map.of("buttonName", "反封账")));
        // 操作挂在行上：选中哪个月就封哪个月，比先选行再点顶栏更直接，
        // 也避开报表面板顶栏只渲染固定按钮的问题（实测顶栏组按钮不显示）。
        applyRowActions(metadata, List.of("封账", "反封账", "结转损益", "计提折旧"));
        metadata.put("buttonGroups", List.of(
                Map.of("name", "查找", "actions", List.of("查找", "刷新")),
                Map.of("name", "期间", "actions", List.of("封账", "反封账", "结转损益", "计提折旧")),
                Map.of("name", "更多", "actions", List.of("导出", "退出"))));
        Map<String, Object> config = new LinkedHashMap<>();
        config.put("metadata", metadata);
        config.put("dataSchema", Map.of("type", "object", "fields", List.of()));
        config.put("detail", Map.of("tabs", List.of()));
        return config;
    }

    /**
     * 三大财务报表的通用外壳：项目类别 / 项目编码 / 项目名称 / 金额。
     *
     * <p>三张表结构一致（都是「项目 → 金额」），只是取数与汇总规则不同，
     * 所以共用一套列定义，避免三处各写一遍导致格式漂移。
     */
    private Map<String, Object> statementConfig(String code, String name, String hint) {
        Map<String, Object> metadata = reportShell(code, name,
                List.of(Map.of("dataName", "期间", "dataType", "文本")),
                List.of("项目类别", "项目编码", "项目名称", "金额"));
        applyTopBar(metadata, List.of("刷新", "导出"));
        Map<String, Object> config = new LinkedHashMap<>();
        config.put("metadata", metadata);
        config.put("dataSchema", Map.of("type", "object", "fields", List.of()));
        config.put("detail", Map.of("tabs", List.of()));
        config.put("remark", hint);
        return config;
    }

    /** 给报表面板的每一行挂操作按钮（表格行内渲染）。 */
    @SuppressWarnings("unchecked")
    private void applyRowActions(Map<String, Object> metadata, List<String> buttons) {
        Object dto = metadata.get("panelPageDto");
        if (!(dto instanceof Map<?, ?> page)) return;
        Object tables = ((Map<String, Object>) page).get("tablePages");
        if (!(tables instanceof List<?> list) || list.isEmpty()) return;
        Object first = list.get(0);
        if (!(first instanceof Map<?, ?> table)) return;
        List<Map<String, Object>> rows = new java.util.ArrayList<>();
        for (String name : buttons) rows.add(Map.of("buttonName", name));
        ((Map<String, Object>) table).put("rowOperationBarBtn", rows);
    }

    /** 覆盖报表面板顶部的可见按钮（reportShell 默认只给「刷新」）。 */
    @SuppressWarnings("unchecked")
    private void applyTopBar(Map<String, Object> metadata, List<String> buttons) {
        Object dto = metadata.get("panelPageDto");
        if (!(dto instanceof Map<?, ?> page)) return;
        Object tables = ((Map<String, Object>) page).get("tablePages");
        if (!(tables instanceof List<?> list) || list.isEmpty()) return;
        Object first = list.get(0);
        if (!(first instanceof Map<?, ?> table)) return;
        List<Map<String, Object>> top = new java.util.ArrayList<>();
        for (String name : buttons) top.add(Map.of("buttonName", name));
        ((Map<String, Object>) table).put("topBarBtn", top);
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
        // 用可变 Map 构造：调用方（如会计期间面板）要覆盖 topBarBtn，
        // Map.of 是不可变的，put 会直接抛 UnsupportedOperationException（实测启动即崩）。
        Map<String, Object> table = new LinkedHashMap<>();
        table.put("tableName", name);
        table.put("queryFields", queryFields);
        table.put("gridTabs", List.of(Map.of("label", "列表", "rowSource", "rows", "columns", columns)));
        table.put("topBarBtn", List.of(Map.of("buttonName", "刷新")));
        table.put("rowOperationBarBtn", List.of());
        table.put("events", List.of());

        Map<String, Object> pageDto = new LinkedHashMap<>();
        pageDto.put("tablePages", List.of(table));
        pageDto.put("formPages", List.of());
        metadata.put("panelPageDto", pageDto);
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
