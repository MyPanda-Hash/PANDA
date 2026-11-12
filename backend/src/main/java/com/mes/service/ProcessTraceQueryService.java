package com.mes.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.entity.StockBalance;
import com.mes.mapper.FormDataMapper;
import com.mes.mapper.StockBalanceMapper;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 业务流程追踪的数据读取层：把 form_data 里的单据转成 ProcessTraceService.Doc。
 *
 * 只读已审核与进行中的业务单据，跳过面板配置类（SYS_*）与基础档案，
 * 避免追踪结果被无关单据污染。
 *
 * 同时负责「这单要不要生产」的判定：销售订单的明细数量能被现有库存覆盖就直接发货，
 * 覆盖不了才需要生产加工单。判定结果决定标准路线走直接出库还是生产加工。
 */
@Service
public class ProcessTraceQueryService {

    /** 参与流程追踪的单据面板白名单。 */
    private static final Set<String> TRACE_PANELS = Set.of(
            "QUOTE_ORDER", "SO_ORDER", "OUTSOURCE_ORDER", "OUTSOURCE_ISSUE", "OUTSOURCE_IN", "OUTSOURCE_FEE",
            "PU_REQ", "PU_ORDER", "ARRIVAL_IN", "INSPECTION", "PURCHASE_IN", "PURCHASE_RETURN",
            "MANU_ORDER", "DISPATCH", "PROCESS_REPORT", "MATERIAL_REQ", "TRANSFER", "MATERIAL_OUT",
            "FINISH_INSPECT", "FINISH_IN", "SALE_OUT", "SALE_INV", "SALE_INVOICE",
            "SALE_RETURN", "RECEIPT", "RECEIPT_PAYABLE",
            // 工序检验链：报检单 → 过程检验单，以及挂加工单的首件报检单
            "PROCESS_INSPECT_APPLY", "PROCESS_INSPECTION", "FIRST_INSPECT");

    /** 供测试与文档核对：当前参与追踪的面板清单（排序后返回，便于断言）。 */
    public static List<String> tracePanels() {
        return TRACE_PANELS.stream().sorted().toList();
    }

    /** 上游引用字段：表头与明细里出现这些都视为"引用了一张上游单据"。 */
    private static final List<String> LINK_KEYS = List.of(
            "来源单号", "销售订单号", "匹配来源单号", "加工单号", "上游单号", "源单号", "原首检单号");

    /** 纯计算用的解析器（不依赖注入，便于单测直接调静态方法）。 */
    private static final ObjectMapper PLAIN = new ObjectMapper();

    private final FormDataMapper formMapper;
    private final ProcessTraceService traceService;
    private final StockBalanceMapper stockMapper;

    public ProcessTraceQueryService(FormDataMapper formMapper, ProcessTraceService traceService,
                                    StockBalanceMapper stockMapper) {
        this.formMapper = formMapper;
        this.traceService = traceService;
        this.stockMapper = stockMapper;
    }

    /** 按单号追踪。mapper 缺失时返回空结果的 TraceResult。 */
    public ProcessTraceService.TraceResult trace(String docNo) {
        if (formMapper == null) return traceService.trace(docNo, List.of());
        List<FormData> all = formMapper.selectList(new LambdaQueryWrapper<>());
        return traceService.trace(docNo, docsOf(all), needsProductionOf(docNo, all, stockOf()));
    }

    /** 读取全部参与追踪的单据并抽取引用关系。 */
    List<ProcessTraceService.Doc> loadDocs() {
        if (formMapper == null) return List.of();
        return docsOf(formMapper.selectList(new LambdaQueryWrapper<>()));
    }

    /** 把 form_data 行转成追踪用单据（带工序信息）。 */
    List<ProcessTraceService.Doc> docsOf(List<FormData> all) {
        List<ProcessTraceService.Doc> out = new ArrayList<>();
        for (FormData fd : all) {
            if (!TRACE_PANELS.contains(fd.getPanelCode())) continue;
            out.add(toDoc(fd));
        }
        return out;
    }

    private ProcessTraceService.Doc toDoc(FormData fd) {
        List<String> links = linksOf(fd);
        String panel = fd.getPanelCode();
        if ("PROCESS_REPORT".equals(panel) || "DISPATCH".equals(panel)) {
            Map<String, Object> row = firstRow(parse(fd.getDetailData()), "items");
            return new ProcessTraceService.Doc(panel, fd.getFormNo(), fd.getStatus(), links,
                    textOf(row, "工序编码"), textOf(row, "工序名称"), List.of());
        }
        if ("MANU_ORDER".equals(panel)) {
            return new ProcessTraceService.Doc(panel, fd.getFormNo(), fd.getStatus(), links,
                    "", "", processesOf(fd));
        }
        if ("FIRST_INSPECT".equals(panel)) {
            // 首件检验单是一工序一行：每行挂到对应工序汇报的分支上
            return new ProcessTraceService.Doc(panel, fd.getFormNo(), fd.getStatus(), links,
                    "", "", rowsOf(fd, "items"));
        }
        return new ProcessTraceService.Doc(panel, fd.getFormNo(), fd.getStatus(), links);
    }

    /** 加工单上的工序清单（按明细顺序）。 */
    private List<ProcessTraceService.Process> processesOf(FormData fd) {
        return rowsOf(fd, "processes");
    }

    /** 明细里的「工序编码 / 工序名称」行（加工单工序、首件检验单的工序行）。 */
    private List<ProcessTraceService.Process> rowsOf(FormData fd, String key) {
        Map<String, Object> detail = parse(fd.getDetailData());
        Object raw = detail.get(key);
        if (!(raw instanceof List<?> rows)) return List.of();
        List<ProcessTraceService.Process> out = new ArrayList<>();
        for (Object row : rows) {
            if (!(row instanceof Map<?, ?> map)) continue;
            String code = textOf(map, "工序编码");
            String name = textOf(map, "工序名称");
            if (code.isEmpty() && name.isEmpty()) continue;
            out.add(new ProcessTraceService.Process(code, name));
        }
        return out;
    }

    /** 存货编码 → 现有库存数量（所有仓库合计）。 */
    Map<String, BigDecimal> stockOf() {
        if (stockMapper == null) return Map.of();
        Map<String, BigDecimal> out = new LinkedHashMap<>();
        for (StockBalance b : stockMapper.selectList(new LambdaQueryWrapper<>())) {
            String code = b.getItemCode();
            if (code == null || code.isBlank()) continue;
            BigDecimal qty = b.getQuantity() == null ? BigDecimal.ZERO : b.getQuantity();
            out.merge(code, qty, BigDecimal::add);
        }
        return out;
    }

    /** 追踪入口单据是不是需要生产的销售订单。 */
    private boolean needsProductionOf(String docNo, List<FormData> all, Map<String, BigDecimal> stock) {
        if (docNo == null || docNo.isBlank()) return false;
        String seed = docNo.trim();
        for (FormData fd : all) {
            if (!seed.equals(fd.getFormNo())) continue;
            if (!"SO_ORDER".equals(fd.getPanelCode())) return false;
            return needsProduction(fd, stock);
        }
        return false;
    }

    /**
     * 销售订单是否需要生产：明细里只要有一行存货的现有库存不够订单数量，就得下生产加工单。
     * 没有明细行时按「需要生产」处理，避免把未知情况显示成「库存够、不用生产」。
     */
    static boolean needsProduction(FormData so, Map<String, BigDecimal> stock) {
        if (so == null) return false;
        Map<String, Object> detail = parse(so.getDetailData());
        Object raw = detail.get("items");
        if (!(raw instanceof List<?> rows) || rows.isEmpty()) return true;
        for (Object row : rows) {
            if (!(row instanceof Map<?, ?> map)) continue;
            String code = textOf(map, "存货编码");
            if (code.isEmpty()) return true;
            BigDecimal need = decimalOf(map, "数量");
            BigDecimal have = stock.getOrDefault(code, BigDecimal.ZERO);
            if (have.compareTo(need) < 0) return true;
        }
        return false;
    }

    /** 从表头与明细里抽取所有上游单号（去重、跳过自身单号）。 */
    List<String> linksOf(FormData fd) {
        Set<String> links = new LinkedHashSet<>();
        String self = fd.getFormNo();
        collectLinks(parse(fd.getData()), links, self);
        collectLinks(parse(fd.getDetailData()), links, self);
        return new ArrayList<>(links);
    }

    @SuppressWarnings("unchecked")
    private void collectLinks(Object node, Set<String> links, String self) {
        if (node instanceof Map<?, ?> map) {
            for (Map.Entry<?, ?> entry : map.entrySet()) {
                String key = String.valueOf(entry.getKey());
                Object value = entry.getValue();
                if (LINK_KEYS.contains(key) && value != null) {
                    String text = String.valueOf(value).trim();
                    // 单号通常以字母开头；过滤空值与自身，避免把备注类文本当单号
                    if (!text.isEmpty() && !text.equals(self)) links.add(text);
                } else {
                    collectLinks(value, links, self);
                }
            }
        } else if (node instanceof List<?> list) {
            for (Object item : list) collectLinks(item, links, self);
        }
    }

    private static Map<String, Object> parse(String value) {
        if (value == null || value.isBlank()) return Map.of();
        try {
            return PLAIN.readValue(value, new TypeReference<Map<String, Object>>() {});
        } catch (Exception ignored) {
            return Map.of();
        }
    }

    /** 取明细里第一行（工序汇报单的工序信息就在这一行）。 */
    private static Map<String, Object> firstRow(Map<String, Object> detail, String key) {
        Object raw = detail.get(key);
        if (!(raw instanceof List<?> rows) || rows.isEmpty()) return Map.of();
        Object first = rows.get(0);
        if (!(first instanceof Map<?, ?> map)) return Map.of();
        Map<String, Object> out = new LinkedHashMap<>();
        for (Map.Entry<?, ?> e : map.entrySet()) {
            out.put(String.valueOf(e.getKey()), e.getValue());
        }
        return out;
    }

    private static String textOf(Map<?, ?> row, String key) {
        Object v = row.get(key);
        return v == null ? "" : String.valueOf(v).trim();
    }

    private static BigDecimal decimalOf(Map<?, ?> row, String key) {
        Object v = row.get(key);
        if (v instanceof Number n) return new BigDecimal(n.toString());
        if (v == null) return BigDecimal.ZERO;
        try {
            String text = String.valueOf(v).trim();
            return text.isEmpty() ? BigDecimal.ZERO : new BigDecimal(text);
        } catch (NumberFormatException e) {
            return BigDecimal.ZERO;
        }
    }
}
