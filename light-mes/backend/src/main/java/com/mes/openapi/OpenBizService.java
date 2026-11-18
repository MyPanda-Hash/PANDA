package com.mes.openapi;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

import org.springframework.stereotype.Service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.entity.PanelConfig;
import com.mes.entity.StockBalance;
import com.mes.mapper.FormDataMapper;
import com.mes.mapper.PanelConfigMapper;
import com.mes.mapper.StockBalanceMapper;
import com.mes.service.PxService;

/**
 * 只读业务接口：单据追踪、单据详情、库存结存，供数字员工问答使用。
 *
 * <p><b>只读</b>：本类不注册任何写方法，也不经 {@code callButton}，天然无法改数据。
 *
 * <p><b>字段白名单来自面板配置</b>：单据详情只回 {@code panel_config} 里
 * {@code dataSchema.fields} 与 {@code detail.tabs[].fields} 声明过的字段——
 * 面板加了字段这里自动跟上，不再维护第二份字段清单。另有一层敏感词兜底
 * （成本/毛利/密码/银行/账号/token/密钥），即使配置里声明了也不出接口。
 *
 * <p><b>追踪直接复用</b> {@link PxService#traceProcess(String)}：AI 看到的环节数、
 * 卡点判定必须和 MES 页面完全一致，不另算一套。
 */
@Service
public class OpenBizService {

    private static final ObjectMapper JSON = new ObjectMapper();
    private static final DateTimeFormatter TIME_FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");
    /** 敏感字段兜底：配置里就算声明了也不出开放接口 */
    private static final Pattern SENSITIVE = Pattern.compile("成本|毛利|密码|银行|账号|token|secret|密钥", Pattern.CASE_INSENSITIVE);
    private static final int STOCK_MAX_LIMIT = 500;
    private static final int STOCK_DEFAULT_LIMIT = 100;

    private final PanelConfigMapper panelMapper;
    private final FormDataMapper formMapper;
    private final StockBalanceMapper stockMapper;
    private final PxService pxService;

    public OpenBizService(PanelConfigMapper panelMapper, FormDataMapper formMapper,
                          StockBalanceMapper stockMapper, PxService pxService) {
        this.panelMapper = panelMapper;
        this.formMapper = formMapper;
        this.stockMapper = stockMapper;
        this.pxService = pxService;
    }

    /** 单据追踪：与页面 `/api/px/trace` 同一份结果 */
    public Map<String, Object> trace(String docNo) {
        String no = docNo == null ? "" : docNo.trim();
        if (no.isEmpty()) throw new OpenApiException(400, "缺少 docNo");
        Map<String, Object> out = new LinkedHashMap<>(pxService.traceProcess(no));
        out.putIfAbsent("单号", no);
        return out;
    }

    /** 单据详情：表头 + 明细页签，字段按面板配置过滤 */
    public Map<String, Object> form(String panelCode, String formNo) {
        String code = panelCode == null ? "" : panelCode.trim();
        String no = formNo == null ? "" : formNo.trim();
        if (code.isEmpty() || no.isEmpty()) throw new OpenApiException(400, "缺少 panelCode 或 formNo");

        PanelConfig panel = panelMapper.selectOne(new LambdaQueryWrapper<PanelConfig>()
                .eq(PanelConfig::getPanelCode, code));
        if (panel == null) throw new OpenApiException(404, "面板不存在：" + code);

        FormData fd = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, code)
                .eq(FormData::getFormNo, no));
        if (fd == null) throw new OpenApiException(404, "单据不存在：" + no);

        Map<String, Object> config = parse(panel.getConfig());
        List<TabSpec> tabs = tabsOf(config);
        Set<String> headerFields = headerFieldsOf(config);

        Map<String, Object> data = parse(fd.getData());
        Map<String, Object> detail = parse(fd.getDetailData());

        Map<String, Object> out = new LinkedHashMap<>();
        out.put("panelCode", code);
        out.put("panelName", panel.getPanelName());
        out.put("formNo", no);
        out.put("status", fd.getStatus());
        out.put("createBy", fd.getCreateBy());
        out.put("createTime", time(fd.getCreateTime()));
        out.put("updateTime", time(fd.getUpdateTime()));
        out.put("auditBy", fd.getAuditBy());
        out.put("auditTime", time(fd.getAuditTime()));
        out.put("fields", new ArrayList<>(headerFields));
        out.put("header", filter(data, headerFields));
        out.put("tabs", filterTabs(detail, tabs));
        out.put("link", "/panelx/form/" + code + "?code=" + no);
        return out;
    }

    /** 库存结存：按存货/仓库过滤，返回行 + 合计 */
    public Map<String, Object> stock(String itemCode, String warehouse, Integer limit) {
        int size = limit == null || limit <= 0 ? STOCK_DEFAULT_LIMIT : Math.min(limit, STOCK_MAX_LIMIT);
        LambdaQueryWrapper<StockBalance> q = new LambdaQueryWrapper<StockBalance>()
                .eq(itemCode != null && !itemCode.isBlank(), StockBalance::getItemCode, itemCode == null ? "" : itemCode.trim())
                .eq(warehouse != null && !warehouse.isBlank(), StockBalance::getWarehouse, warehouse == null ? "" : warehouse.trim())
                .orderByAsc(StockBalance::getItemCode)
                .orderByAsc(StockBalance::getWarehouse)
                .last("LIMIT " + size);
        List<StockBalance> rows = stockMapper.selectList(q);

        List<Map<String, Object>> out = new ArrayList<>();
        BigDecimal quantity = BigDecimal.ZERO;
        BigDecimal amount = BigDecimal.ZERO;
        for (StockBalance row : rows) {
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("存货编码", row.getItemCode());
            item.put("存货名称", row.getItemName());
            item.put("规格型号", row.getSpec());
            item.put("计量单位", row.getUnit());
            item.put("仓库", row.getWarehouse());
            item.put("库位", row.getLocation());
            item.put("批次", row.getBatchNo());
            item.put("数量", row.getQuantity());
            item.put("锁定量", row.getLockedQuantity());
            item.put("金额", row.getAmount());
            out.add(item);
            quantity = quantity.add(row.getQuantity() == null ? BigDecimal.ZERO : row.getQuantity());
            amount = amount.add(row.getAmount() == null ? BigDecimal.ZERO : row.getAmount());
        }

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("存货编码", itemCode == null ? "" : itemCode);
        body.put("仓库", warehouse == null ? "" : warehouse);
        body.put("数量合计", quantity);
        body.put("金额合计", amount);
        body.put("rows", out);
        return body;
    }

    // ---------- 配置解析与字段过滤 ----------

    private record TabSpec(String key, String label, Set<String> fields) {
    }

    private static Map<String, Object> parse(String json) {
        if (json == null || json.isBlank()) return Map.of();
        try {
            return JSON.readValue(json, new TypeReference<Map<String, Object>>() { });
        } catch (Exception e) {
            return Map.of();
        }
    }

    private static Set<String> headerFieldsOf(Map<String, Object> config) {
        Set<String> fields = new LinkedHashSet<>();
        Object schema = config.get("dataSchema");
        if (schema instanceof Map<?, ?> map && map.get("fields") instanceof List<?> list) {
            for (Object item : list) {
                if (item instanceof Map<?, ?> field && field.get("dataName") != null) {
                    fields.add(String.valueOf(field.get("dataName")));
                }
            }
        }
        return fields;
    }

    private static List<TabSpec> tabsOf(Map<String, Object> config) {
        List<TabSpec> tabs = new ArrayList<>();
        Object detail = config.get("detail");
        if (!(detail instanceof Map<?, ?> map) || !(map.get("tabs") instanceof List<?> list)) return tabs;
        int idx = 0;
        for (Object item : list) {
            if (!(item instanceof Map<?, ?> tab)) continue;
            String key = tab.get("key") == null ? "tab" + (idx + 1) : String.valueOf(tab.get("key"));
            String label = tab.get("label") == null ? key : String.valueOf(tab.get("label"));
            Set<String> fields = new LinkedHashSet<>();
            if (tab.get("fields") instanceof List<?> fieldList) {
                for (Object f : fieldList) {
                    if (f instanceof Map<?, ?> fm && fm.get("dataName") != null) fields.add(String.valueOf(fm.get("dataName")));
                }
            }
            tabs.add(new TabSpec(key, label, fields));
            idx++;
        }
        return tabs;
    }

    private static Map<String, Object> filter(Map<String, Object> source, Set<String> allowed) {
        Map<String, Object> out = new LinkedHashMap<>();
        if (source == null) return out;
        for (Map.Entry<String, Object> e : source.entrySet()) {
            if (!allowed.contains(e.getKey())) continue;
            if (SENSITIVE.matcher(e.getKey()).find()) continue;
            out.put(e.getKey(), e.getValue());
        }
        return out;
    }

    /**
     * 明细按配置页签输出；配置里没有声明的页签（历史数据多出来的键）只保留敏感词之外的字段，
     * 避免旧结构把成本类列漏出去。
     */
    private static List<Map<String, Object>> filterTabs(Map<String, Object> detail, List<TabSpec> specs) {
        List<Map<String, Object>> out = new ArrayList<>();
        Set<String> used = new LinkedHashSet<>();
        for (TabSpec spec : specs) {
            List<Map<String, Object>> rows = rowsOf(detail.get(spec.key()));
            if (rows.isEmpty() && !detail.containsKey(spec.key())) continue;
            List<Map<String, Object>> kept = new ArrayList<>();
            for (Map<String, Object> row : rows) kept.add(filter(row, spec.fields().isEmpty() ? row.keySet() : spec.fields()));
            out.add(tab(spec.key(), spec.label(), kept));
            used.add(spec.key());
        }
        for (Map.Entry<String, Object> e : detail.entrySet()) {
            if (used.contains(e.getKey())) continue;
            List<Map<String, Object>> rows = rowsOf(e.getValue());
            if (rows.isEmpty()) continue;
            List<Map<String, Object>> kept = new ArrayList<>();
            for (Map<String, Object> row : rows) kept.add(filter(row, row.keySet()));
            out.add(tab(e.getKey(), e.getKey(), kept));
        }
        return out;
    }

    private static Map<String, Object> tab(String key, String label, List<Map<String, Object>> rows) {
        Map<String, Object> tab = new LinkedHashMap<>();
        tab.put("key", key);
        tab.put("label", label);
        tab.put("rows", rows);
        return tab;
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> rowsOf(Object value) {
        if (!(value instanceof List<?> list)) return List.of();
        List<Map<String, Object>> rows = new ArrayList<>();
        for (Object item : list) {
            if (item instanceof Map<?, ?> map) rows.add((Map<String, Object>) map);
        }
        return rows;
    }

    private static String time(LocalDateTime value) {
        return value == null ? null : value.format(TIME_FMT);
    }
}
