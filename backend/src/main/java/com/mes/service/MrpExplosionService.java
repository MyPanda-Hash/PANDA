package com.mes.service;

import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * MRP 的 BOM 递归展开：从成品需求逐层展开到用料，扣减库存后给出净需求。
 *
 * 关键约束：
 * - 同一物料多层出现时数量累加（不取最后一次）
 * - 循环引用靠 visited 集合截断，不会无限递归
 * - 超过 maxDepth 停止展开，超出部分记为未展开
 * - 有 BOM 的节点标记「自制」，无 BOM 的叶子标记「采购」
 */
@Service
public class MrpExplosionService {

    /** 一条 BOM 行：父件用一个 X 需要 Y 个子件，考虑损耗率。 */
    public record BomLine(String parentCode, String childCode, String childName,
                          BigDecimal unitQty, BigDecimal scrapRate) {}

    /** 一条物料需求：毛需求、库存、净需求、供应方式。 */
    public record Requirement(String itemCode, String itemName, String supplyType,
                              BigDecimal grossQty, BigDecimal stockQty, BigDecimal netQty,
                              int level) {}

    /** 展开结果 + 是否因深度限制而未完全展开。 */
    public record ExplosionResult(List<Requirement> requirements, boolean fullyExploded,
                                  List<String> truncatedItems) {}

    private static final BigDecimal HUNDRED = new BigDecimal("100");

    public MrpExplosionService() {
    }

    /** 简化入口：不扣库存。 */
    public List<Requirement> explode(Map<String, BigDecimal> demand,
                                     Map<String, List<BomLine>> bom, int maxDepth) {
        return explode(demand, bom, maxDepth, Map.of());
    }

    /** 简化入口：不记录截断明细。 */
    public List<Requirement> explode(Map<String, BigDecimal> demand,
                                     Map<String, List<BomLine>> bom, int maxDepth,
                                     Map<String, BigDecimal> stock) {
        return explodeDetailed(demand, bom, maxDepth, stock).requirements();
    }

    /**
     * 完整展开：返回需求列表与展开完整性。
     *
     * @param demand   成品需求，键为物料编码
     * @param bom      父件编码 → BOM 行
     * @param maxDepth 最大展开层数
     * @param stock    现存量，键为物料编码
     */
    public ExplosionResult explodeDetailed(Map<String, BigDecimal> demand,
                                           Map<String, List<BomLine>> bom, int maxDepth,
                                           Map<String, BigDecimal> stock) {
        Map<String, Aggregate> gross = new LinkedHashMap<>();
        List<String> truncated = new ArrayList<>();
        if (demand != null) {
            for (Map.Entry<String, BigDecimal> e : demand.entrySet()) {
                BigDecimal qty = e.getValue();
                if (qty == null || qty.signum() <= 0) continue;
                walk(e.getKey(), qty, 0, maxDepth, bom == null ? Map.of() : bom,
                        new LinkedHashSet<>(), gross, truncated);
            }
        }

        List<Requirement> out = new ArrayList<>();
        for (Aggregate agg : gross.values()) {
            BigDecimal available = stock == null ? BigDecimal.ZERO
                    : stock.getOrDefault(agg.code, BigDecimal.ZERO);
            BigDecimal net = agg.qty.subtract(available);
            if (net.signum() < 0) net = BigDecimal.ZERO;
            out.add(new Requirement(agg.code, agg.name, agg.supplyType,
                    agg.qty.setScale(4, RoundingMode.HALF_UP),
                    available.setScale(4, RoundingMode.HALF_UP),
                    net.setScale(4, RoundingMode.HALF_UP),
                    agg.level));
        }
        return new ExplosionResult(out, truncated.isEmpty(), truncated);
    }

    /** 递归展开；同一物料再次出现时累加数量而非覆盖。 */
    private void walk(String code, BigDecimal qty, int depth, int maxDepth,
                      Map<String, List<BomLine>> bom, Set<String> path,
                      Map<String, Aggregate> gross, List<String> truncated) {
        List<BomLine> children = bom.get(code);
        boolean isLeaf = children == null || children.isEmpty();

        Aggregate agg = gross.computeIfAbsent(code,
                k -> new Aggregate(k, "", isLeaf ? "采购" : "自制", BigDecimal.ZERO, depth));
        agg.qty = agg.qty.add(qty);
        if (!isLeaf) agg.supplyType = "自制";
        if (agg.name.isEmpty() && !isLeaf && children != null) {
            agg.name = code; // 名称由调用方补充；此处保留编码占位
        }

        if (isLeaf) return;
        // 循环引用：当前路径上已出现过的物料不再展开
        if (path.contains(code)) return;
        if (depth >= maxDepth) {
            truncated.add(code);
            return;
        }

        Set<String> next = new LinkedHashSet<>(path);
        next.add(code);
        for (BomLine line : children) {
            BigDecimal factor = line.unitQty() == null ? BigDecimal.ZERO : line.unitQty();
            if (factor.signum() <= 0) continue;
            BigDecimal scrap = line.scrapRate() == null ? BigDecimal.ZERO : line.scrapRate();
            BigDecimal withScrap = factor.multiply(
                    BigDecimal.ONE.add(scrap.divide(HUNDRED, 6, RoundingMode.HALF_UP)));
            BigDecimal childQty = qty.multiply(withScrap);
            if (childQty.signum() <= 0) continue;
            // 记录子件名称，供结果展示
            boolean childLeaf = !bom.containsKey(line.childCode());
            Aggregate childAgg = gross.get(line.childCode());
            if (childAgg == null) {
                childAgg = new Aggregate(line.childCode(), line.childName(),
                        childLeaf ? "采购" : "自制", BigDecimal.ZERO, depth + 1);
                gross.put(line.childCode(), childAgg);
            } else if (childAgg.name.isEmpty()) {
                childAgg.name = line.childName();
            }
            walk(line.childCode(), childQty, depth + 1, maxDepth, bom, next, gross, truncated);
        }
    }

    /** 可变聚合体，便于逐层累加。 */
    private static final class Aggregate {
        private final String code;
        private String name;
        private String supplyType;
        private BigDecimal qty;
        private final int level;

        private Aggregate(String code, String name, String supplyType, BigDecimal qty, int level) {
            this.code = code;
            this.name = name == null ? "" : name;
            this.supplyType = supplyType;
            this.qty = qty == null ? BigDecimal.ZERO : qty;
            this.level = level;
        }
    }
}
