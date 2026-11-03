package com.mes.service;

import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * 齐套分析：判断某产品按需求数量能否开工，缺哪些料、缺多少。
 *
 * 与 MRP 的区别：
 * - MRP 递归展开到最底层，算全链净需求
 * - 齐套只看一层 BOM（直接子件），回答"这一单现在能不能开工"
 */
@Service
public class KitAnalyzeService {

    /** 一条子件齐套情况。 */
    public record KitLine(String itemCode, String itemName, BigDecimal requiredQty,
                          BigDecimal stockQty, BigDecimal shortageQty, BigDecimal fulfillRate) {}

    /** 齐套结论：是否可开工、缺料条数、最低满足率。 */
    public record KitResult(String productCode, BigDecimal demandQty, boolean ready,
                            List<KitLine> lines, int shortageCount, BigDecimal minFulfillRate) {}

    private static final BigDecimal HUNDRED = new BigDecimal("100");

    public KitAnalyzeService() {
    }

    /** 不看损耗、无库存的简化入口。 */
    public KitResult analyze(String productCode, BigDecimal demand) {
        return analyze(productCode, demand, Map.of(), Map.of());
    }

    /**
     * 齐套分析。
     *
     * @param productCode 成品编码
     * @param demand      需求数量
     * @param bom         父件 → 子件行
     * @param stock       现存量，键为物料编码
     */
    public KitResult analyze(String productCode, BigDecimal demand,
                             Map<String, List<MrpExplosionService.BomLine>> bom,
                             Map<String, BigDecimal> stock) {
        BigDecimal qty = demand == null ? BigDecimal.ZERO : demand;
        if (qty.signum() <= 0) {
            return new KitResult(productCode, qty, true, List.of(), 0, HUNDRED);
        }

        List<MrpExplosionService.BomLine> children =
                bom == null ? null : bom.get(productCode);

        // 无 BOM：把成品自身当成唯一需求项，按自身库存判断
        if (children == null || children.isEmpty()) {
            BigDecimal available = nz(stock == null ? null : stock.get(productCode));
            BigDecimal shortage = qty.subtract(available);
            if (shortage.signum() < 0) shortage = BigDecimal.ZERO;
            KitLine line = new KitLine(productCode, "", qty.setScale(4, RoundingMode.HALF_UP),
                    available.setScale(4, RoundingMode.HALF_UP),
                    shortage.setScale(4, RoundingMode.HALF_UP),
                    fulfillRate(qty, available));
            boolean ready = shortage.signum() == 0;
            return new KitResult(productCode, qty, ready, List.of(line),
                    ready ? 0 : 1, line.fulfillRate());
        }

        List<KitLine> lines = new ArrayList<>();
        int shortageCount = 0;
        BigDecimal minRate = HUNDRED;

        for (MrpExplosionService.BomLine child : children) {
            if (child.unitQty() == null || child.unitQty().signum() <= 0) continue;
            BigDecimal scrap = child.scrapRate() == null ? BigDecimal.ZERO : child.scrapRate();
            BigDecimal factor = child.unitQty().multiply(
                    BigDecimal.ONE.add(scrap.divide(HUNDRED, 6, RoundingMode.HALF_UP)));
            BigDecimal required = qty.multiply(factor).setScale(4, RoundingMode.HALF_UP);
            BigDecimal available = nz(stock == null ? null : stock.get(child.childCode()));
            BigDecimal shortage = required.subtract(available);
            if (shortage.signum() < 0) shortage = BigDecimal.ZERO;
            BigDecimal rate = fulfillRate(required, available);
            if (shortage.signum() > 0) shortageCount++;
            if (rate.compareTo(minRate) < 0) minRate = rate;
            lines.add(new KitLine(child.childCode(), child.childName(), required,
                    available.setScale(4, RoundingMode.HALF_UP),
                    shortage.setScale(4, RoundingMode.HALF_UP), rate));
        }

        boolean ready = shortageCount == 0;
        return new KitResult(productCode, qty, ready, lines, shortageCount,
                lines.isEmpty() ? HUNDRED : minRate);
    }

    /** 满足率 = 库存 / 需求 × 100，上限 100。 */
    private BigDecimal fulfillRate(BigDecimal required, BigDecimal available) {
        if (required == null || required.signum() <= 0) return HUNDRED;
        BigDecimal rate = available.multiply(HUNDRED).divide(required, 2, RoundingMode.HALF_UP);
        return rate.compareTo(HUNDRED) > 0 ? HUNDRED : rate;
    }

    private static BigDecimal nz(BigDecimal value) {
        return value == null ? BigDecimal.ZERO : value;
    }
}
