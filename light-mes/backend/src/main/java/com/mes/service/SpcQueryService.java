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

    /**
     * 一个统计口径：检验项目 + 指标。
     *
     * <p>必须带指标一起分组。只按检验项目分组时，「尺寸公差」既有实测值（10.0 上下）
     * 又有老数据的合格率（100），两者会混进同一个序列：实测算出的均值变成 25、
     * 标准差 36.7、Cpk 负数 —— 数字看着像算出来了，其实整条控制图都是错的。
     */
    record Bucket(String item, String metric) {}

    /** 按「检验项目 + 口径」汇总：含均值/控制限/越界点数，实测值带规格时附 Cp/Cpk。 */
    public List<Map<String, Object>> controlCharts() {
        Map<Bucket, List<BigDecimal>> samples = new LinkedHashMap<>();
        // 口径 → [规格下限, 规格上限]：只有实测值有公差，合格率没有
        Map<Bucket, BigDecimal[]> specLimits = new LinkedHashMap<>();

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
                    String metric = "实测值";
                    if (value == null) {
                        value = number(row.get("合格率"));
                        metric = "合格率%";
                    }
                    if (value == null) continue;
                    Bucket bucket = new Bucket(item, metric);
                    samples.computeIfAbsent(bucket, k -> new ArrayList<>()).add(value);
                    if ("实测值".equals(metric)) {
                        BigDecimal low = number(row.get("规格下限"));
                        BigDecimal high = number(row.get("规格上限"));
                        if (low != null && high != null && high.compareTo(low) > 0) {
                            specLimits.put(bucket, new BigDecimal[]{low, high});
                        }
                    }
                }
            }
        }

        List<Map<String, Object>> out = new ArrayList<>();
        for (Map.Entry<Bucket, List<BigDecimal>> e : samples.entrySet()) {
            Bucket bucket = e.getKey();
            QualityControlService.ControlChart chart =
                    qualityControlService.controlChart(e.getValue(), 3);
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("检验项目", bucket.item());
            row.put("特性值", bucket.metric());
            row.put("样本数", e.getValue().size());
            row.put("均值", chart.mean());
            row.put("标准差", chart.sigma());
            row.put("控制上限", chart.ucl());
            row.put("控制下限", chart.lcl());
            row.put("越界点数", chart.outOfControl().size());
            row.put("过程状态", chart.outOfControl().isEmpty() ? "受控" : "失控");
            appendCapability(row, e.getValue(), specLimits.get(bucket));
            out.add(row);
        }
        return out;
    }

    /**
     * 过程能力：Cp/Cpk 只在「实测值 + 规格上下限」齐全时才有意义。
     *
     * <p>合格率是比例数据（p 图口径），没有公差，硬套 Cpk 会算出无意义的数字，
     * 所以这里按口径判断，缺规格就不输出能力列，而不是给一个 0 让人误读。
     */
    private void appendCapability(Map<String, Object> row,
                                  List<BigDecimal> values, BigDecimal[] limits) {
        if (limits == null) return;
        QualityControlService.Capability capability =
                qualityControlService.capability(values, limits[0], limits[1]);
        row.put("规格下限", limits[0]);
        row.put("规格上限", limits[1]);
        row.put("Cp", capability.cp());
        row.put("Cpk", capability.cpk());
        row.put("过程能力", capabilityLabel(capability));
    }

    /** Cpk 分档：制造业通常以 1.33 为「过程能力充足」的门槛。 */
    static String capabilityLabel(QualityControlService.Capability capability) {
        if (capability == null) return "";
        BigDecimal cpk = capability.cpk();
        if (cpk == null || cpk.signum() <= 0) return "无规格";
        if (cpk.compareTo(new BigDecimal("1.33")) >= 0) return "充足";
        if (cpk.compareTo(BigDecimal.ONE) >= 0) return "尚可";
        return "不足";
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
