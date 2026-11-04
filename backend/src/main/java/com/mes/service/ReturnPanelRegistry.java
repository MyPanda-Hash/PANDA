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
 * 退货单面板：采购退货单、销售退货单。
 * 方向与正常单据相反——采购退货是出库、销售退货是入库，由 StockTxnService 的方向表决定。
 * 财务侧分别产生 AP_RETURN / AR_RETURN 冲减往来。
 */
@Component
public class ReturnPanelRegistry {

    static final String VERSION = "RT1.0";

    private final PanelConfigMapper panelMapper;
    private final ObjectMapper json;

    public ReturnPanelRegistry(PanelConfigMapper panelMapper, ObjectMapper json) {
        this.panelMapper = panelMapper;
        this.json = json;
    }

    @PostConstruct
    public void syncPanelConfigs() {
        upsert("PURCHASE_RETURN", "采购退货单", "单据",
                returnConfig("PURCHASE_RETURN", "采购退货单", "供应商", "采购退货", "AP_RETURN"));
        upsert("SALE_RETURN", "销售退货单", "单据",
                returnConfig("SALE_RETURN", "销售退货单", "客户", "销售退货", "AR_RETURN"));
    }

    private Map<String, Object> returnConfig(String code, String name, String partnerField,
                                             String bizType, String voucherType) {
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("panelCode", code);
        metadata.put("panelName", name);
        metadata.put("panelCategory", "单据");
        metadata.put("autoCodeField", "单据编号");
        metadata.put("bizType", bizType);
        metadata.put("voucherType", voucherType);
        metadata.put("panelState", Map.of("dataName", "单据状态", "dataType", "STRING",
                "defaultOptions", List.of("草稿", "已审核", "已中止")));
        metadata.put("panelButtons", List.of(
                Map.of("buttonName", "新增流程"),
                Map.of("buttonName", "保存"),
                Map.of("buttonName", "审核"),
                Map.of("buttonName", "弃审"),
                Map.of("buttonName", "冲抵"),
                Map.of("buttonName", "反冲抵"),
                Map.of("buttonName", "刷新")));
        metadata.put("buttonGroups", List.of(
                Map.of("name", "新增", "actions", List.of("新增", "引入常用单据")),
                Map.of("name", "保存", "actions", List.of("保存", "保存新增", "保存为草稿")),
                Map.of("name", "删除", "actions", List.of("删除", "删除单据")),
                Map.of("name", "审核", "actions", List.of("审核", "弃审")),
                // 退货单审掉的是往来侧的负数信用，要能拿去冲同一往来单位的未结应收/应付
                Map.of("name", "冲抵", "actions", List.of("冲抵", "反冲抵")),
                Map.of("name", "查找", "actions", List.of("查找", "刷新")),
                Map.of("name", "打印", "actions", List.of("打印", "预览", "导出")),
                Map.of("name", "更多", "actions", List.of("复制", "放弃", "附件", "消息"))));

        String codeName = "客户".equals(partnerField) ? "客户编码" : "供应商编码";
        List<Map<String, Object>> queryFields = List.of(
                Map.of("dataName", "单据日期", "dataType", "日期"),
                Map.of("dataName", "单据编号", "dataType", "文本"),
                Map.of("dataName", partnerField, "dataType", "文本"),
                Map.of("dataName", "仓库", "dataType", "文本"));
        Map<String, Object> tablePage = Map.of(
                "tableName", name + "列表",
                "queryFields", queryFields,
                "gridTabs", List.of(Map.of(
                        "label", "列表",
                        "rowSource", "rows",
                        "columns", List.of("单据日期", "单据编号", partnerField, "仓库",
                                "来源单号", "金额", "退货原因", "单据状态"),
                        "summaryItems", List.of())),
                "topBarBtn", List.of(Map.of("buttonName", "新增流程"),
                        Map.of("buttonName", "删除"), Map.of("buttonName", "刷新")),
                "rowOperationBarBtn", List.of(),
                "events", List.of());
        metadata.put("panelPageDto", Map.of(
                "tablePages", List.of(tablePage),
                "formPages", List.of(Map.of(
                        "formName", name,
                        "fieldNames", "单据日期,单据编号," + partnerField + "," + codeName
                                + ",仓库,来源单号,退货原因,退货日期,经手人,金额,已冲抵金额,未冲抵金额,冲抵日期,备注",
                        "bottomOperationBarBtn", List.of(Map.of("buttonName", "保存"),
                                Map.of("buttonName", "审核"), Map.of("buttonName", "放弃")),
                        "events", List.of()))));

        List<Map<String, Object>> schema = List.of(
                field("单据日期", "日期", true),
                Map.of("dataName", "单据编号", "dataType", "文本", "isRequired", true, "autoCode", true),
                field(partnerField, "文本", true),
                field(codeName, "文本", false),
                field("仓库", "文本", false),
                field("来源单号", "文本", false),
                select("退货原因", "质量问题", "规格不符", "客户取消", "其他"),
                field("退货日期", "日期", false),
                field("经手人", "文本", false),
                field("金额", "小数", false),
                field("已冲抵金额", "小数", false),
                field("未冲抵金额", "小数", false),
                field("冲抵日期", "日期", false),
                field("备注", "文本", false));

        List<Map<String, Object>> detailFields = List.of(
                field("仓库", "文本", false),
                field("存货编码", "文本", false),
                field("存货名称", "文本", true),
                field("规格型号", "文本", false),
                field("计量单位", "文本", false),
                quantity("实收数量", true),
                quantity("数量", false),
                amount("单价", false),
                amount("售价", false),
                amount("金额", false),
                amount("销售金额", false),
                field("批号", "文本", false),
                field("来源单号", "文本", false),
                field("来源行号", "整数", false),
                field("备注", "文本", false));
        Map<String, Object> tab = Map.of(
                "key", "items",
                "label", "明细",
                "isRequired", true,
                "fields", detailFields);

        Map<String, Object> config = new LinkedHashMap<>();
        config.put("metadata", metadata);
        config.put("dataSchema", Map.of("type", "object", "fields", schema));
        config.put("detail", Map.of("tabs", List.of(tab)));
        return config;
    }

    private Map<String, Object> field(String name, String type, boolean required) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("dataName", name);
        m.put("dataType", type);
        m.put("isRequired", required);
        return m;
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

    private Map<String, Object> quantity(String name, boolean required) {
        return amount(name, required);
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
            throw new IllegalStateException("退货单面板配置序列化失败", e);
        }
    }
}
