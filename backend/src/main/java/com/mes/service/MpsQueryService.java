package com.mes.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.mapper.FormDataMapper;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * MPS 主生产计划的查询入口：扫已审核销售订单，算出待排产计划行。
 * 已排产数量按"生产加工单"里带销售订单号的明细反查。
 */
@Service
public class MpsQueryService {

    private final FormDataMapper formMapper;
    private final ObjectMapper json;
    private final MpsService mpsService;

    public MpsQueryService(FormDataMapper formMapper, ObjectMapper json, MpsService mpsService) {
        this.formMapper = formMapper;
        this.json = json;
        this.mpsService = mpsService;
    }

    /** 生成 MPS 计划行列表：已审核销售订单 - 已排产 = 待排产。 */
    public List<Map<String, Object>> planRows() {
        Map<String, Map<String, Object>> merged = new LinkedHashMap<>();

        List<FormData> orders = formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "SO_ORDER")
                .eq(FormData::getStatus, "已审核"));
        for (FormData fd : orders) {
            Map<String, Object> head = parse(fd.getData());
            Map<String, Object> detail = parse(fd.getDetailData());
            // 已排产按该销售订单号反查，不能用全局累加：不同订单的同一产品必须分别计算
            Map<String, BigDecimal> scheduled = scheduledOfOrder(fd.getFormNo());
            for (MpsService.PlanLine line : mpsService.buildPlanLines(fd.getFormNo(), head, detail, scheduled)) {
                String key = line.itemCode().isEmpty() ? line.itemName() : line.itemCode();
                Map<String, Object> row = merged.get(key);
                if (row == null) {
                    row = new LinkedHashMap<>();
                    row.put("存货编码", line.itemCode());
                    row.put("存货名称", line.itemName());
                    row.put("规格型号", line.spec());
                    row.put("计量单位", line.unit());
                    row.put("待排产数量", BigDecimal.ZERO);
                    row.put("客户", line.customer());
                    row.put("需求来源", new ArrayList<String>());
                    merged.put(key, row);
                }
                row.put("待排产数量", ((BigDecimal) row.get("待排产数量")).add(line.planQty()));
                @SuppressWarnings("unchecked")
                List<String> sources = (List<String>) row.get("需求来源");
                if (!sources.contains(line.sourceNo())) sources.add(line.sourceNo());
                row.put("需求交期", line.dueDate());
                row.put("建议开工", line.suggestedStart());
                row.put("优先级", line.priority());
            }
        }

        List<Map<String, Object>> out = new ArrayList<>();
        for (Map<String, Object> row : merged.values()) {
            @SuppressWarnings("unchecked")
            List<String> sources = (List<String>) row.get("需求来源");
            row.put("需求来源", String.join("、", sources));
            out.add(row);
        }
        return out;
    }

    /**
     * 该销售订单的已排产数量：反查生产加工单时按销售订单号匹配。
     * 早期加工单可能没记销售订单号，此时不参与扣减（宁可多排，不可漏排）。
     */
    @SuppressWarnings("unchecked")
    private Map<String, BigDecimal> scheduledOfOrder(String salesOrderNo) {
        Map<String, BigDecimal> scheduled = new LinkedHashMap<>();
        if (salesOrderNo == null || salesOrderNo.isBlank()) return scheduled;
        List<FormData> orders = formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "MANU_ORDER")
                .in(FormData::getStatus, List.of("已审核", "生产中", "已完工")));
        for (FormData fd : orders) {
            Map<String, Object> head = parse(fd.getData());
            String linked = text(head.get("销售订单号"));
            if (!salesOrderNo.equals(linked)) continue;
            Map<String, Object> detail = parse(fd.getDetailData());
            Object products = detail.get("products");
            if (!(products instanceof List<?> list)) continue;
            for (Object item : list) {
                if (!(item instanceof Map<?, ?> raw)) continue;
                Map<String, Object> row = (Map<String, Object>) raw;
                String code = text(row.get("产品编码"));
                if (code.isEmpty()) continue;
                BigDecimal qty = number(row.get("数量"));
                scheduled.merge(code, qty, BigDecimal::add);
            }
        }
        return scheduled;
    }

    private Map<String, Object> parse(String value) {
        if (value == null || value.isBlank()) return new LinkedHashMap<>();
        try {
            return json.readValue(value, new com.fasterxml.jackson.core.type.TypeReference<Map<String, Object>>() {});
        } catch (Exception ignored) {
            return new LinkedHashMap<>();
        }
    }

    private static String text(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }

    private static BigDecimal number(Object value) {
        if (value == null) return BigDecimal.ZERO;
        if (value instanceof BigDecimal d) return d;
        if (value instanceof Number n) return new BigDecimal(n.toString());
        try {
            return new BigDecimal(String.valueOf(value).trim());
        } catch (NumberFormatException ignored) {
            return BigDecimal.ZERO;
        }
    }
}
