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
 * 应收应付面板：收款单、付款单、客户对账单、供应商对账单、账龄分析表。
 * 收款/付款是单据（带核销动作），对账与账龄是报表（只读）。
 */
@Component
public class FinancePanelRegistry {

    static final String VERSION = "FIN1.0";

    private final PanelConfigMapper panelMapper;
    private final ObjectMapper json;

    public FinancePanelRegistry(PanelConfigMapper panelMapper, ObjectMapper json) {
        this.panelMapper = panelMapper;
        this.json = json;
    }

    @PostConstruct
    public void syncPanelConfigs() {
        upsert("RECEIPT", "收款单", "单据", receiptConfig());
        upsert("RECEIPT_PAYABLE", "付款单", "单据", paymentConfig());
        upsert("SETTLE_CUSTOMER", "客户对账单", "报表", customerStatementConfig());
        upsert("SETTLE_SUPPLIER", "供应商对账单", "报表", supplierStatementConfig());
        upsert("AGING_ANALYSIS", "账龄分析表", "报表", agingConfig());
    }

    // ---------- 收款单 / 付款单 ----------

    private Map<String, Object> receiptConfig() {
        return voucherConfig("RECEIPT", "收款单", "收款", "客户", "AR");
    }

    private Map<String, Object> paymentConfig() {
        return voucherConfig("RECEIPT_PAYABLE", "付款单", "付款", "供应商", "AP");
    }

    /**
     * 收付款单结构一致，只区分往来方与往来类型。
     * 核销与反核销走 PanelActionHandler 之外的服务调用，按钮在此登记。
     */
    private Map<String, Object> voucherConfig(String code, String name, String bizType,
                                              String partnerField, String voucherType) {
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("panelCode", code);
        metadata.put("panelName", name);
        metadata.put("panelCategory", "单据");
        metadata.put("autoCodeField", "单据编号");
        metadata.put("bizType", bizType);
        metadata.put("voucherType", voucherType);
        metadata.put("panelState", Map.of("dataName", "单据状态", "dataType", "STRING",
                "defaultOptions", List.of("草稿", "已审核", "已核销", "部分核销")));
        metadata.put("panelButtons", List.of(
                Map.of("buttonName", "新增流程"),
                Map.of("buttonName", "保存"),
                Map.of("buttonName", "审核"),
                Map.of("buttonName", "弃审"),
                Map.of("buttonName", "核销"),
                Map.of("buttonName", "反核销"),
                Map.of("buttonName", "刷新")));
        metadata.put("buttonGroups", List.of(
                Map.of("name", "新增", "actions", List.of("新增", "引入常用单据")),
                Map.of("name", "保存", "actions", List.of("保存", "保存新增", "保存为草稿")),
                Map.of("name", "删除", "actions", List.of("删除", "删除单据")),
                Map.of("name", "审核", "actions", List.of("审核", "弃审")),
                Map.of("name", "核销", "actions", List.of("核销", "反核销")),
                Map.of("name", "查找", "actions", List.of("查找", "刷新")),
                Map.of("name", "打印", "actions", List.of("打印", "预览", "导出")),
                Map.of("name", "更多", "actions", List.of("复制", "放弃", "附件", "消息"))));

        List<Map<String, Object>> queryFields = List.of(
                Map.of("dataName", "单据日期", "dataType", "日期"),
                Map.of("dataName", "单据编号", "dataType", "文本"),
                Map.of("dataName", partnerField, "dataType", "文本"),
                Map.of("dataName", "结算方式", "dataType", "下拉框",
                        "options", List.of("银行转账", "现金", "承兑汇票", "其他")));
        Map<String, Object> tablePage = Map.of(
                "tableName", name + "列表",
                "queryFields", queryFields,
                "gridTabs", List.of(Map.of(
                        "label", "列表",
                        "rowSource", "rows",
                        "columns", List.of("单据日期", "单据编号", partnerField, "结算方式",
                                "金额", "已核销金额", "未核销金额", "单据状态"))),
                "topBarBtn", List.of(Map.of("buttonName", "新增流程"),
                        Map.of("buttonName", "删除"), Map.of("buttonName", "刷新")),
                "rowOperationBarBtn", List.of(),
                "events", List.of());
        metadata.put("panelPageDto", Map.of(
                "tablePages", List.of(tablePage),
                "formPages", List.of(Map.of(
                        "formName", name,
                        "fieldNames", "单据日期,单据编号," + partnerField
                                + ",结算方式,金额,已核销金额,未核销金额,核销日期,未结单据,备注",
                        "bottomOperationBarBtn", List.of(Map.of("buttonName", "保存"),
                                Map.of("buttonName", "审核"), Map.of("buttonName", "放弃")),
                        "events", List.of()))));

        List<Map<String, Object>> schema = List.of(
                field("单据日期", "日期", true),
                Map.of("dataName", "单据编号", "dataType", "文本", "isRequired", true, "autoCode", true),
                field(partnerField, "文本", true),
                partnerCodeField(partnerField),
                select("结算方式", "银行转账", "现金", "承兑汇票", "其他"),
                amount("金额", true),
                amount("已核销金额", false),
                amount("未核销金额", false),
                field("核销日期", "日期", false),
                field("未结单据", "文本", false),
                field("备注", "文本", false));

        // 核销明细：填了「来源单号 + 本次核销金额」就按行核（可只核一部分、可指定到某张单）；
        // 不填则按业务日期自动分配。未结单据字段在表单上列出可核的单号，照抄即可。
        Map<String, Object> settleTab = new LinkedHashMap<>();
        settleTab.put("key", "settle");
        settleTab.put("label", "核销明细");
        settleTab.put("isRequired", false);
        // block:B = 单独成一个网格区块。收款单没有明细行，若按默认规则它会被当成主网格的字段
        // schema 吃掉，页签在界面上不出现（通用运行时按 detail.tabs[1..n] 建第二区块）。
        settleTab.put("block", "B");
        settleTab.put("fields", List.of(
                field("来源单号", "文本", true),
                amount("未结金额", false),
                amount("本次核销金额", true),
                field("备注", "文本", false)));

        Map<String, Object> config = new LinkedHashMap<>();
        config.put("metadata", metadata);
        config.put("dataSchema", Map.of("type", "object", "fields", schema));
        config.put("detail", Map.of("tabs", List.of(settleTab)));
        return config;
    }

    // ---------- 报表 ----------

    private Map<String, Object> customerStatementConfig() {
        return statementConfig("SETTLE_CUSTOMER", "客户对账单", "客户", "AR");
    }

    private Map<String, Object> supplierStatementConfig() {
        return statementConfig("SETTLE_SUPPLIER", "供应商对账单", "供应商", "AP");
    }

    private Map<String, Object> statementConfig(String code, String name, String partnerField, String voucherType) {
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("panelCode", code);
        metadata.put("panelName", name);
        metadata.put("panelCategory", "报表");
        metadata.put("voucherType", voucherType);
        metadata.put("panelButtons", List.of(Map.of("buttonName", "刷新"), Map.of("buttonName", "导出")));
        metadata.put("buttonGroups", List.of(
                Map.of("name", "查找", "actions", List.of("查找", "刷新")),
                Map.of("name", "更多", "actions", List.of("导出", "退出"))));
        metadata.put("panelPageDto", Map.of(
                "tablePages", List.of(Map.of(
                        "tableName", name,
                        "queryFields", List.of(
                                Map.of("dataName", partnerField, "dataType", "文本"),
                                Map.of("dataName", "开始日期", "dataType", "日期"),
                                Map.of("dataName", "结束日期", "dataType", "日期")),
                        "gridTabs", List.of(Map.of(
                                "label", "明细",
                                "rowSource", "rows",
                                "columns", List.of("业务日期", "往来单位", "来源单据", "来源单号",
                                        "金额", "已核销金额", "未核销金额", "结算状态"))),
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

    private Map<String, Object> agingConfig() {
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("panelCode", "AGING_ANALYSIS");
        metadata.put("panelName", "账龄分析表");
        metadata.put("panelCategory", "报表");
        metadata.put("panelButtons", List.of(Map.of("buttonName", "刷新"), Map.of("buttonName", "导出")));
        metadata.put("buttonGroups", List.of(
                Map.of("name", "查找", "actions", List.of("查找", "刷新")),
                Map.of("name", "更多", "actions", List.of("导出", "退出"))));
        metadata.put("panelPageDto", Map.of(
                "tablePages", List.of(Map.of(
                        "tableName", "账龄分析表",
                        "queryFields", List.of(
                                Map.of("dataName", "往来类型", "dataType", "下拉框",
                                        "options", List.of("应收", "应付")),
                                Map.of("dataName", "往来单位", "dataType", "文本"),
                                Map.of("dataName", "截止日期", "dataType", "日期")),
                        "gridTabs", List.of(Map.of(
                                "label", "账龄分布",
                                "rowSource", "rows",
                                "columns", List.of("往来类型", "往来单位", "未结金额",
                                        "未到期", "30天内", "31-60天", "61-90天", "90天以上"))),
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

    // ---------- 字段构造助手 ----------

    private Map<String, Object> field(String name, String type, boolean required) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("dataName", name);
        m.put("dataType", type);
        m.put("isRequired", required);
        return m;
    }

    private Map<String, Object> partnerCodeField(String partnerField) {
        String codeName = "客户".equals(partnerField) ? "客户编码" : "供应商编码";
        return field(codeName, "文本", false);
    }

    private Map<String, Object> select(String name, String... options) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("dataName", name);
        m.put("dataType", "下拉框");
        m.put("options", List.of(options));
        return m;
    }

    private Map<String, Object> amount(String name, boolean required) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("dataName", name);
        m.put("dataType", "小数");
        m.put("isRequired", required);
        m.put("defaultValue", 0);
        return m;
    }

    // ---------- 持久化 ----------

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
        row.setModuleGroup("finance");
        row.setConfig(toJson(config));
        row.setVersion(VERSION);
        row.setUpdateTime(now);
        if (row.getId() == null) {
            panelMapper.insert(row);
        } else {
            panelMapper.updateById(row);
        }
    }

    private String toJson(Object value) {
        try {
            return json.writeValueAsString(value);
        } catch (Exception e) {
            throw new IllegalStateException("应收应付面板配置序列化失败", e);
        }
    }
}
