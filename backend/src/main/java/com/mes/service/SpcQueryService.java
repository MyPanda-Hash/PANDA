package com.mes.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.mapper.FormDataMapper;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * SPC 控制图查询：从检验单明细取可量化特性，按检验项目分组算控制限。
 * 只统计已审核检验单，避免未终判数据污染控制限。
 *
 * 取值优先级：实测值（若面板配置了检验项目实测值字段）→ 合格率。
 * 现有检验单没有"实测值"字段，因此实际走合格率，反映过程稳定性。
 */
@Service
public class SpcQueryService {

    private final FormDataMapper formMapper;
    private final ObjectMapper json;
    private final QualityControlService qualityControlService;

    public SpcQueryService(FormDataMapper formMapper, ObjectMapper json,
                           QualityControlService qualityControlService) {
        this.formMapper = formMapper;
        this.json = json;
        this.qualityControlService = qualityControlService;
    }

    /** 按检验项目汇总：每个项目一行，含均值/控制限/越界点数。 */
    public List<Map<String, Object>> controlCharts() {
        Map<String, List<BigDecimal>> byItem = new LinkedHashMap<>();
        Map<String, String> metricLabel = new LinkedHashMap<>();

        for (String panel : List.of("INSPECTION", "PROCESS_INSPECTION")) {
            List<FormData> docs = formMapper.selectList(new LambdaQueryWrapper<FormData>()
                    .eq(FormData::getPanelCode, panel)
                    .eq(FormData::getStatus, "已审核"));
            for (FormData fd : docs) {
                for (Map<String, Object> row : rowsOf(fd)) {
                    String item = text(row.get("检验项目"));
                    if (item.isBlank()) continue;
                    // 优先实测值；无该字段时回退到合格率
                    BigDecimal value = number(row.get("实测值"));
                    String label = "实测值";
                    if (value == null) {
                        value = number(row.get("合格率"));
                        label = "合格率%";
                    }
                    if (value == null) continue;
                    byItem.computeIfAbsent(item, k -> new ArrayList<>()).add(value);
                    metricLabel.putIfAbsent(item, label);
                }
            }
        }

        List<Map<String, Object>> out = new ArrayList<>();
        for (Map.Entry<String, List<BigDecimal>> e : byItem.entrySet()) {
            QualityControlService.ControlChart chart =
                    qualityControlService.controlChart(e.getValue(), 3);
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("检验项目", e.getKey());
            row.put("特性值", metricLabel.getOrDefault(e.getKey(), ""));
            row.put("样本数", e.getValue().size());
            row.put("均值", chart.mean());
            row.put("标准差", chart.sigma());
            row.put("控制上限", chart.ucl());
            row.put("控制下限", chart.lcl());
            row.put("越界点数", chart.outOfControl().size());
            row.put("过程状态", chart.outOfControl().isEmpty() ? "受控" : "失控");
            out.add(row);
        }
        return out;
    }

    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> rowsOf(FormData fd) {
        Map<String, Object> detail = parse(fd.getDetailData());
        List<Map<String, Object>> out = new ArrayList<>();
        for (Object value : detail.values()) {
            if (!(value instanceof List<?> list)) continue;
            for (Object item : list) if (item instanceof Map<?, ?> raw) out.add((Map<String, Object>) raw);
        }
        return out;
    }

    private Map<String, Object> parse(String value) {
        if (value == null || value.isBlank()) return new LinkedHashMap<>();
        try {
            return json.readValue(value, new TypeReference<Map<String, Object>>() {});
        } catch (Exception ignored) {
            return new LinkedHashMap<>();
        }
    }

    private static String text(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }

    private static BigDecimal number(Object value) {
        if (value == null) return null;
        if (value instanceof BigDecimal d) return d;
        if (value instanceof Number n) return new BigDecimal(n.toString());
        try {
            return new BigDecimal(String.valueOf(value).trim());
        } catch (NumberFormatException ignored) {
            return null;
        }
    }
}
