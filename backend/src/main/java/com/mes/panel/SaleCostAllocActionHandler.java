package com.mes.panel;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.mapper.FormDataMapper;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 销售费用分摊的「执行分摊」动作：把分摊单里的金额按销售发票号回写到发票上。
 *
 * <p>此前这条链是断的：费用单审核后不产生会计凭证（`AccountingService` 没有 EXPENSE 模板），
 * 分摊单审批也不回写任何东西——分摊多少、摊到哪张票，只有分摊单自己知道，发票上看不到，
 * 「费用 → 分摊 → 发票毛利」闭环不成立。这里补最后一棒：
 * 分摊单审核后点「执行分摊」，按明细的销售发票号汇总，写回发票表头的「分摊费用」并标记执行状态。
 *
 * <p>幂等：分摊单只能执行一次（执行后状态置「已分摊」），重复点击直接拒绝，
 * 避免同一笔费用被累加两遍；发票侧用累加写入，支持一张票被多张分摊单分摊。
 */
@Component
public class SaleCostAllocActionHandler implements PanelActionHandler {

    static final String PANEL = "SALE_COST_ALLOC";
    static final String ACTION = "执行分摊";
    private static final String EXECUTED = "已分摊";

    private final FormDataMapper formMapper;
    private final ObjectMapper json;

    public SaleCostAllocActionHandler(FormDataMapper formMapper, ObjectMapper json) {
        this.formMapper = formMapper;
        this.json = json;
    }

    @Override
    public boolean supports(String panelCode, String action) {
        return PANEL.equals(panelCode) && "执行分摊".equals(action);
    }

    @Override
    public Map<String, Object> handle(PanelActionContext context) {
        Object no = context.formData() == null ? null : context.formData().get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        String formNo = String.valueOf(no);
        FormData alloc = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, PANEL).eq(FormData::getFormNo, formNo));
        if (alloc == null) throw new IllegalArgumentException("销售费用分摊单不存在：" + formNo);
        if (!"已审核".equals(alloc.getStatus())) {
            throw new IllegalStateException("分摊单 " + formNo + " 还没审核，审核后才能执行分摊");
        }
        Map<String, Object> head = parse(alloc.getData());
        if (EXECUTED.equals(text(head.get("分摊状态")))) {
            throw new IllegalStateException("分摊单 " + formNo + " 已经执行过分摊，不能重复执行");
        }
        List<Map<String, Object>> rows = detailRows(parse(alloc.getDetailData()));
        if (rows.isEmpty()) throw new IllegalStateException("分摊单没有分摊明细");

        // 按发票号汇总：一张分摊单可以摊到多张票，同一张票多行要合并
        Map<String, BigDecimal> byInvoice = new LinkedHashMap<>();
        BigDecimal unassigned = BigDecimal.ZERO;
        for (Map<String, Object> row : rows) {
            BigDecimal amount = number(first(row, "分摊金额"));
            if (amount.signum() == 0) continue;
            String invoiceNo = text(first(row, "销售发票号"));
            if (invoiceNo.isBlank() || "客户".equals(text(first(row, "分摊对象")))) {
                unassigned = unassigned.add(amount);
                continue;
            }
            byInvoice.merge(invoiceNo, amount, BigDecimal::add);
        }
        if (byInvoice.isEmpty()) {
            throw new IllegalStateException("分摊明细没有填「销售发票号」："
                    + (unassigned.signum() > 0
                    ? "按客户分摊的 " + plain(unassigned) + " 元没有落到票上，请改成按销售发票分摊"
                    : "请先选分摊对象为销售发票并填发票号"));
        }

        List<Map<String, Object>> applied = new ArrayList<>();
        for (Map.Entry<String, BigDecimal> entry : byInvoice.entrySet()) {
            FormData invoice = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                    .eq(FormData::getPanelCode, "SALE_INVOICE").eq(FormData::getFormNo, entry.getKey()));
            if (invoice == null) {
                throw new IllegalStateException("销售发票不存在：" + entry.getKey());
            }
            Map<String, Object> invoiceHead = parse(invoice.getData());
            BigDecimal before = number(invoiceHead.get("分摊费用"));
            BigDecimal after = before.add(entry.getValue());
            invoiceHead.put("分摊费用", after);
            invoiceHead.put("分摊单号", formNo);
            invoice.setData(toJson(invoiceHead));
            invoice.setUpdateTime(LocalDateTime.now());
            formMapper.updateById(invoice);
            Map<String, Object> one = new LinkedHashMap<>();
            one.put("销售发票号", entry.getKey());
            one.put("本次分摊", entry.getValue());
            one.put("累计分摊", after);
            applied.add(one);
        }

        head.put("分摊状态", EXECUTED);
        head.put("执行时间", LocalDateTime.now().withNano(0).toString());
        alloc.setData(toJson(head));
        alloc.setUpdateTime(LocalDateTime.now());
        formMapper.updateById(alloc);

        Map<String, Object> out = new LinkedHashMap<>();
        out.put("编号", formNo);
        out.put("分摊状态", EXECUTED);
        out.put("分摊发票数", applied.size());
        out.put("未分摊金额", unassigned);
        out.put("rows", applied);
        return out;
    }

    // ---------- 工具 ----------

    private Map<String, Object> parse(String raw) {
        if (raw == null || raw.isBlank()) return Map.of();
        try {
            return json.readValue(raw, new TypeReference<>() {});
        } catch (Exception e) {
            return Map.of();
        }
    }

    private String toJson(Object value) {
        try {
            return json.writeValueAsString(value);
        } catch (Exception e) {
            throw new IllegalStateException("分摊数据序列化失败", e);
        }
    }

    private static List<Map<String, Object>> detailRows(Map<String, Object> detail) {
        List<Map<String, Object>> out = new ArrayList<>();
        if (detail == null) return out;
        for (Object value : detail.values()) {
            if (!(value instanceof List<?> list)) continue;
            for (Object item : list) {
                if (item instanceof Map<?, ?> map) {
                    Map<String, Object> typed = new LinkedHashMap<>();
                    map.forEach((k, v) -> typed.put(String.valueOf(k), v));
                    out.add(typed);
                }
            }
        }
        return out;
    }

    private static Object first(Map<String, Object> row, String... keys) {
        for (String key : keys) {
            Object value = row.get(key);
            if (value != null && !String.valueOf(value).isBlank()) return value;
        }
        return null;
    }

    private static String text(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }

    private static BigDecimal number(Object value) {
        if (value == null) return BigDecimal.ZERO;
        try {
            return new BigDecimal(String.valueOf(value).trim());
        } catch (NumberFormatException e) {
            return BigDecimal.ZERO;
        }
    }

    private static String plain(BigDecimal value) {
        return value == null ? "0" : value.stripTrailingZeros().toPlainString();
    }
}
