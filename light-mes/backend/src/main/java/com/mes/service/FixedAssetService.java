package com.mes.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.mapper.FormDataMapper;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 固定资产：卡片读取、直线法折旧、折旧凭证生成。
 *
 * <p>卡片走通用面板 FA_CARD（存在 form_data），不新增业务表 —— 与系统「配置驱动」的
 * 一贯做法一致，也省掉一套自己的 CRUD。
 *
 * <p>折旧口径（写死在代码里并且只有一种，避免「这个月用直线法、下个月用别的」）：
 * <pre>
 *   月折旧额 = 原值 × (1 − 残值率%) ÷ 使用月数
 * </pre>
 * 累计折旧通过凭证反查：source_panel = FA_DEPRECIATION，partner = 资产编码，
 * 科目 1602。这样「已经提了多少」永远和账上一致，不会出现卡片与账簿两套数。
 */
@Service
public class FixedAssetService {

    private static final String PANEL = "FA_CARD";
    private static final String SOURCE_PANEL = "FA_DEPRECIATION";

    private final FormDataMapper formMapper;
    private final ObjectMapper json;
    private final AccountingService accountingService;
    private final AccountingPeriodService periodService;

    public FixedAssetService(FormDataMapper formMapper, ObjectMapper json,
                             AccountingService accountingService,
                             AccountingPeriodService periodService) {
        this.formMapper = formMapper;
        this.json = json;
        this.accountingService = accountingService;
        this.periodService = periodService;
    }

    /**
     * 可计提折旧的资产卡片。
     *
     * <p>资产卡片是档案不是单据，状态是「启用/停用」；但同一套面板框架也可能把它走成
     * 「草稿/已审核」。这里两种都认，否则档案类卡片永远计不到折旧（实测踩到）。
     */
    public List<Map<String, Object>> cards() {
        List<FormData> docs = formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, PANEL)
                .in(FormData::getStatus, List.of("已审核", "启用"))
                .orderByAsc(FormData::getId));
        List<Map<String, Object>> out = new ArrayList<>();
        for (FormData fd : docs) {
            Map<String, Object> head = parse(fd.getData());
            head.put("资产编码", head.getOrDefault("资产编码", fd.getFormNo()));
            head.put("累计折旧", depreciatedOf(str(head.get("资产编码"))));
            head.put("净值", decimal(head.get("原值")).subtract(decimal(head.get("累计折旧"))));
            out.add(head);
        }
        return out;
    }

    /**
     * 直线法月折旧额。使用月数 ≤ 0 或原值 ≤ 0 时返回 0，不给「负折旧」这种脏数。
     */
    public BigDecimal monthlyDepreciation(Map<String, Object> card) {
        BigDecimal original = decimal(card.get("原值"));
        BigDecimal residualRate = decimal(card.get("残值率%"));
        BigDecimal months = decimal(card.get("使用月数"));
        if (original.signum() <= 0 || months.signum() <= 0) return BigDecimal.ZERO;
        BigDecimal residual = original.multiply(residualRate).divide(new BigDecimal("100"), 4, RoundingMode.HALF_UP);
        BigDecimal depreciable = original.subtract(residual);
        if (depreciable.signum() <= 0) return BigDecimal.ZERO;
        return depreciable.divide(months, 2, RoundingMode.HALF_UP);
    }

    /**
     * 计提某期间折旧：逐卡片算当月应提额，生成「借 6602 / 贷 1602」的凭证。
     *
     * <p>幂等：source_no 用期间号，重复计提先删旧分录。已提满（累计 ≥ 可折旧额）的卡片跳过。
     *
     * @return 实际计提的卡片数与分录条数
     */
    public Map<String, Object> depreciate(String period) {
        String p = (period == null || period.isBlank()) ? latestPeriod() : period.trim();
        if (p.isBlank()) throw new IllegalStateException("没有可计提的会计期间");
        periodService.requireOpen(p, "计提折旧");

        LocalDate periodEnd = LocalDate.parse(p + "-01");
        periodEnd = periodEnd.withDayOfMonth(periodEnd.lengthOfMonth());

        List<AccountingService.Entry> entries = new ArrayList<>();
        int counted = 0;
        BigDecimal total = BigDecimal.ZERO;
        for (Map<String, Object> card : cards()) {
            String code = str(card.get("资产编码"));
            if (code.isBlank()) continue;
            // 开始使用日期晚于期末的资产，本期不提
            LocalDate start = date(card.get("开始使用日期"));
            if (start != null && start.isAfter(periodEnd)) continue;

            BigDecimal monthly = monthlyDepreciation(card);
            if (monthly.signum() <= 0) continue;

            BigDecimal original = decimal(card.get("原值"));
            BigDecimal residual = original.multiply(decimal(card.get("残值率%")))
                    .divide(new BigDecimal("100"), 4, RoundingMode.HALF_UP);
            BigDecimal depreciable = original.subtract(residual);
            BigDecimal already = decimal(card.get("累计折旧"));
            BigDecimal remain = depreciable.subtract(already);
            if (remain.signum() <= 0) continue;
            BigDecimal amount = monthly.min(remain);

            entries.add(new AccountingService.Entry("6602", "折旧费用", "借", amount, code));
            entries.add(new AccountingService.Entry("1602", "累计折旧", "贷", amount, code));
            total = total.add(amount);
            counted++;
        }

        if (entries.isEmpty()) {
            Map<String, Object> empty = new LinkedHashMap<>();
            empty.put("期间", p);
            empty.put("计提卡片", 0);
            empty.put("分录数", 0);
            empty.put("折旧额", BigDecimal.ZERO);
            empty.put("说明", "没有需要计提折旧的卡片（未审核或已提满）");
            return empty;
        }

        int posted = accountingService.postDepreciation(p, entries, "计提折旧 " + p);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("期间", p);
        out.put("计提卡片", counted);
        out.put("分录数", posted);
        out.put("折旧额", total);
        out.put("说明", "已生成折旧凭证：借 折旧费用 / 贷 累计折旧");
        return out;
    }

    /** 某资产已提折旧，从凭证反查，保证与账簿一致。 */
    public BigDecimal depreciatedOf(String assetCode) {
        if (assetCode == null || assetCode.isBlank()) return BigDecimal.ZERO;
        return accountingService.depreciatedOf(SOURCE_PANEL, assetCode);
    }

    private String latestPeriod() {
        return periodService.resolvePeriod(LocalDate.now());
    }

    private Map<String, Object> parse(String value) {
        if (value == null || value.isBlank()) return new LinkedHashMap<>();
        try {
            return json.readValue(value, new TypeReference<Map<String, Object>>() {});
        } catch (Exception ignored) {
            return new LinkedHashMap<>();
        }
    }

    private static String str(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }

    private static LocalDate date(Object value) {
        String s = str(value);
        if (s.length() < 10) return null;
        try {
            return LocalDate.parse(s.substring(0, 10));
        } catch (Exception e) {
            return null;
        }
    }

    private static BigDecimal decimal(Object value) {
        if (value instanceof BigDecimal b) return b;
        if (value instanceof Number n) return BigDecimal.valueOf(n.doubleValue());
        if (value == null) return BigDecimal.ZERO;
        try {
            return new BigDecimal(String.valueOf(value).trim());
        } catch (NumberFormatException e) {
            return BigDecimal.ZERO;
        }
    }
}
