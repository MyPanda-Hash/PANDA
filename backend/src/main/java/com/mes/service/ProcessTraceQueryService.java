package com.mes.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.mapper.FormDataMapper;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 业务流程追踪的数据读取层：把 form_data 里的单据转成 ProcessTraceService.Doc。
 *
 * 只读已审核与进行中的业务单据，跳过面板配置类（SYS_*）与基础档案，
 * 避免追踪结果被无关单据污染。
 */
@Service
public class ProcessTraceQueryService {

    /** 参与流程追踪的单据面板白名单。 */
    private static final Set<String> TRACE_PANELS = Set.of(
            "QUOTE_ORDER", "SO_ORDER", "OUTSOURCE_ORDER", "OUTSOURCE_ISSUE", "OUTSOURCE_IN",
            "PU_REQ", "PU_ORDER", "ARRIVAL_IN", "INSPECTION", "PURCHASE_IN", "PURCHASE_RETURN",
            "MANU_ORDER", "PROCESS_REPORT", "MATERIAL_REQ", "TRANSFER", "MATERIAL_OUT",
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
            "来源单号", "销售订单号", "匹配来源单号", "加工单号", "上游单号", "源单号");

    private final FormDataMapper formMapper;
    private final ObjectMapper json;
    private final ProcessTraceService traceService;

    public ProcessTraceQueryService(FormDataMapper formMapper, ObjectMapper json,
                                    ProcessTraceService traceService) {
        this.formMapper = formMapper;
        this.json = json;
        this.traceService = traceService;
    }

    /** 按单号追踪。mapper 缺失时返回空结果的 TraceResult。 */
    public ProcessTraceService.TraceResult trace(String docNo) {
        return traceService.trace(docNo, loadDocs());
    }

    /** 读取全部参与追踪的单据并抽取引用关系。 */
    List<ProcessTraceService.Doc> loadDocs() {
        if (formMapper == null) return List.of();
        List<FormData> all = formMapper.selectList(new LambdaQueryWrapper<>());
        List<ProcessTraceService.Doc> out = new ArrayList<>();
        for (FormData fd : all) {
            if (!TRACE_PANELS.contains(fd.getPanelCode())) continue;
            out.add(new ProcessTraceService.Doc(fd.getPanelCode(), fd.getFormNo(),
                    fd.getStatus(), linksOf(fd)));
        }
        return out;
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

    private Map<String, Object> parse(String value) {
        if (value == null || value.isBlank()) return Map.of();
        try {
            return json.readValue(value, new TypeReference<Map<String, Object>>() {});
        } catch (Exception ignored) {
            return Map.of();
        }
    }
}
