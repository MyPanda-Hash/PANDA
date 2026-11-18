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
 * 计划分析引擎：一张销售订单 → 成品层 → BOM 展开 → 物料缺口 → 工艺路线 → 风险。
 *
 * <p>纯计算，不碰数据库：数据装载由 {@link PlanAnalyzeQueryService} 负责。
 * 这样分析结果可以用固定 fixture 断言，不依赖库里的种子数据。
 *
 * <p><b>关键语义</b>：成品自身库存为 0 不算阻塞 —— 排产的意义就是把它做出来。
 * 只有「物料不够 / 没 BOM / 没工艺路线」才影响能否顺利排产。
 */
@Service
public class PlanAnalyzeService {

    /** 必须显式配置才能排产的阻塞码。 */
    private static final Set<String> BLOCKING_CODES = Set.of("NO_BOM", "NO_ITEM");

    /** 工艺路线可驱动排产的状态。 */
    private static final Set<String> APPROVED_ROUTE_STATUSES = Set.of("已审核", "启用");

    private static final BigDecimal HUNDRED = new BigDecimal("100");
    private static final int MAX_DEPTH = 8;

    // ---------- 输入 ----------

    /** 存货档案摘要：名称/规格/单位/属性（自制、外购、委外）/默认工艺路线。 */
    public record ItemMaster(String code, String name, String spec, String unit,
                             String attribute, String routeCode) {}

    /** BOM 一行：父件生产一个需要多少子件，考虑损耗率（百分数）。 */
    public record BomLine(String childCode, String childName, String unit,
                          BigDecimal unitQty, BigDecimal scrapRate) {}

    /** 一个父件的 BOM（一张 BOM 单的主子结构）。 */
    public record BomHeader(String bomCode, String parentCode, String routeCode, List<BomLine> children) {}

    /** 工艺路线一步。 */
    public record RouteStep(int seq, String opCode, String opName, String workType,
                            String workshop, boolean keyStep, BigDecimal stdPassRate) {}

    /** 销售订单表头。 */
    public record OrderHeader(String no, String customer, String orderDate,
                              String dueDate, String priority) {}

    /** 销售订单明细行。 */
    public record OrderLine(String itemCode, String itemName, String spec,
                            String unit, BigDecimal quantity, String dueDate) {}

    /**
     * 分析所需的全部数据。
     *
     * @param items          存货档案，键为存货编码
     * @param bom            父件编码 → BOM；无 BOM 的成品表示「不能自制」
     * @param routes         工艺路线编码 → 工序；空列表表示路线存在但没工序
     * @param stock          可用现存量（已扣减其他订单占用），键为存货编码
     * @param stockRecorded  有库存记录的存货编码；用来区分「库存 0」与「从没入过库」
     * @param moByOrder      销售订单号 → 已生成的加工单号
     */
    public record Data(Map<String, ItemMaster> items,
                       Map<String, BomHeader> bom,
                       Map<String, List<RouteStep>> routes,
                       Map<String, BigDecimal> stock,
                       Set<String> stockRecorded,
                       Map<String, List<String>> moByOrder,
                       Map<String, String> routeStatus) {

        /** 不传状态的构造：把传入的路线一律当作已审核（测试与无状态场景用）。 */
        public Data(Map<String, ItemMaster> items, Map<String, BomHeader> bom,
                    Map<String, List<RouteStep>> routes, Map<String, BigDecimal> stock,
                    Set<String> stockRecorded, Map<String, List<String>> moByOrder) {
            this(items, bom, routes, stock, stockRecorded, moByOrder, Map.of());
        }
    }

    // ---------- 输出 ----------

    /** 一条成品行。 */
    public record ProductLine(String code, String name, String spec, String unit,
                              BigDecimal demandQty, BigDecimal stockQty, boolean stockRecorded,
                              BigDecimal shortQty, boolean hasBom, String bomCode,
                              boolean routingConfigured, String routeCode, String routeStatus) {}

    /**
     * 一条物料行（BOM 展开后的节点）。
     *
     * @param path  从成品到本节点的链路，如 {@code CP001 > CL002 > CL003}
     * @param level 0 = 成品本身，1 = 直接子件，依此类推
     */
    public record MaterialLine(int level, String path, String code, String name, String spec,
                               String unit, BigDecimal grossQty, BigDecimal stockQty,
                               boolean stockRecordExists, BigDecimal netQty, String supplyType,
                               boolean shortage, boolean expandable) {}

    /**
     * 工艺路线视图（按产品一条）。
     *
     * @param productCode 该路线所属产品的存货编码
     * @param routeStatus 路线单据状态（草稿/审批中/已审核），未配置时为空
     */
    public record Routing(boolean configured, String productCode, String productName,
                          String routeCode, String routeStatus, List<RouteStep> steps,
                          List<String> keySteps, List<String> outsourced) {}

    /** 风险项。 */
    public record Risk(String code, String item, String message) {}

    /** 汇总。 */
    public record Summary(boolean readyToPlan, int blockingCount, int shortageCount,
                          BigDecimal kitRate, int maxLevel) {}

    /** 分析结果。 */
    public record Result(OrderHeader order, List<ProductLine> products,
                         List<MaterialLine> materials,
                         Routing routing,            // 兼容：第一张已配置路线
                         List<Routing> routings,     // 每个成品一条，页面逐个展示
                         List<Risk> risks, Summary summary) {}

    // ---------- 主流程 ----------

    /** 分析一张销售订单。overrides 里的成品用覆盖后的工艺路线（多成品时逐个查）。 */
    public Result analyze(OrderHeader order, List<OrderLine> lines, Data data) {
        Data d = data == null ? emptyData() : data;
        List<Risk> risks = new ArrayList<>();

        // 1) 成品层
        List<ProductLine> products = new ArrayList<>();
        for (OrderLine line : lines == null ? List.<OrderLine>of() : lines) {
            String code = text(line.itemCode());
            if (code.isEmpty()) continue;
            ItemMaster item = d.items().get(code);
            if (item == null) {
                risks.add(new Risk("NO_ITEM", code, "存货档案里没有 " + code + "，无法确定单位与属性"));
            }
            BigDecimal stock = stockOf(d, code);
            BigDecimal demand = nz(line.quantity()).max(BigDecimal.ZERO);
            BigDecimal shortQty = demand.subtract(stock);
            if (shortQty.signum() < 0) shortQty = BigDecimal.ZERO;

            BomHeader bomHeader = d.bom().get(code);
            boolean hasBom = bomHeader != null && !bomHeader.children().isEmpty();
            String routeCode = resolveRouteCode(item, bomHeader);
            boolean hasSteps = routeCode != null && !routeCode.isBlank()
                    && d.routes().containsKey(routeCode)
                    && !d.routes().get(routeCode).isEmpty();
            // 工艺路线是审批面板：草稿/启用状态不能驱动排产，只有已审核算配置齐全
            String routeStatus = routeCode == null ? "" : text(d.routeStatus().get(routeCode));
            boolean routeApproved = routeStatus.isEmpty() ? hasSteps
                    : APPROVED_ROUTE_STATUSES.contains(routeStatus);
            boolean routingConfigured = hasSteps && routeApproved;

            products.add(new ProductLine(code, nameOf(item, line.itemName()), specOf(item, line.spec()),
                    unitOf(item, line.unit()), scale(demand), stock, d.stockRecorded().contains(code),
                    scale(shortQty), hasBom, bomHeader == null ? "" : bomHeader.bomCode(),
                    routingConfigured, routeCode == null ? "" : routeCode, routeStatus));

            if (!hasBom && "自制".equals(text(item == null ? "" : item.attribute()))) {
                risks.add(new Risk("NO_BOM", code,
                        nameOf(item, line.itemName()) + " 标记为自制但没维护物料清单，无法展开物料需求"));
            }
            if (!routingConfigured) {
                // 有路线但没审核 ≠ 没有路线：两者给的动作完全不同（去审核 / 去新建）
                risks.add(routeStatus.equals("草稿") || routeStatus.equals("审批中")
                        ? new Risk("ROUTE_DRAFT", code,
                                nameOf(item, line.itemName()) + " 的工艺路线 " + routeCode
                                        + " 还是" + routeStatus + "，审核后才能排产")
                        : new Risk("NO_ROUTE", code,
                                nameOf(item, line.itemName()) + " 未配置工艺路线，排产后无法按工序派工"));
            }
        }

        // 2) BOM 逐层展开
        List<MaterialLine> materials = explode(products, d, risks);

        // 3) 工艺路线：订单里每个产品各取自己那条路线，页面逐个产品展示
        List<Routing> routings = buildRoutings(products, d);
        Routing routing = routings.stream().filter(Routing::configured).findFirst()
                .orElse(new Routing(false, "", "", "", "", List.of(), List.of(), List.of()));

        // 4) 汇总：缺料数只算物料层（level ≥ 1）；成品层库存不足叫「待生产」，不是缺料
        int shortageCount = (int) materials.stream()
                .filter(MaterialLine::shortage)
                .filter(line -> line.level() > 0)
                .count();
        int blockingCount = (int) risks.stream().filter(r -> BLOCKING_CODES.contains(r.code())).count();
        int maxLevel = materials.stream().mapToInt(MaterialLine::level).max().orElse(0);
        BigDecimal kitRate = materials.isEmpty() ? HUNDRED : minFulfillRate(materials);
        boolean readyToPlan = blockingCount == 0;

        // 5) 已生成加工单提示
        List<String> moNos = d.moByOrder().getOrDefault(order == null ? "" : order.no(), List.of());

        OrderHeader head = order == null
                ? new OrderHeader("", "", "", "", "") : order;
        if (!moNos.isEmpty()) {
            risks.add(new Risk("HAS_MO", String.join("、", moNos),
                    "该订单已生成加工单，分析结果仅作参考；重复排产会产生重复需求"));
        }

        return new Result(head, products, materials, routing, routings, risks,
                new Summary(readyToPlan, blockingCount, shortageCount, kitRate, maxLevel));
    }

    // ---------- BOM 展开 ----------

    /**
     * 按层展开 BOM：每层聚合毛需求，扣可用库存得净需求，只有「缺料且自制」才继续往下拆。
     *
     * <p>为什么只拆缺料的：用户问的是「缺的商品还需要哪些物料」。全都拆开会让一张
     * 订单铺出成百行，真正要采购的那几行反而看不见。
     */
    private List<MaterialLine> explode(List<ProductLine> products, Data d, List<Risk> risks) {
        List<MaterialLine> out = new ArrayList<>();
        // 成品自身作为 level 0 行，让前端能按路径成树
        for (ProductLine p : products) {
            out.add(new MaterialLine(0, p.code(), p.code(), p.name(), p.spec(), p.unit(),
                    p.demandQty(), p.stockQty(), p.stockRecorded(), p.shortQty(),
                    attributeOf(d, p.code()), p.shortQty().signum() > 0, p.hasBom()));
        }

        // 待展开队列：父件编码 + 数量 + 父路径 + 层级
        List<Pending> queue = new ArrayList<>();
        for (ProductLine p : products) {
            if (p.hasBom() && p.shortQty().signum() > 0) {
                queue.add(new Pending(p.code(), p.shortQty(), p.code(), 1));
            }
        }

        // 同一路径不重复展开，防止 BOM 循环引用
        Set<String> visited = new LinkedHashSet<>();
        while (!queue.isEmpty()) {
            List<Pending> next = new ArrayList<>();
            // 同一层同一物料先聚合，避免多成品共用件各算各的
            Map<String, Aggregate> levelAgg = new LinkedHashMap<>();
            for (Pending pending : queue) {
                if (!visited.add(pending.path() + "|" + pending.parentCode())) continue;
                if (pending.level() > MAX_DEPTH) {
                    risks.add(new Risk("BOM_TOO_DEEP", pending.parentCode(),
                            "BOM 展开超过 " + MAX_DEPTH + " 层，已停止：" + pending.path()));
                    continue;
                }
                BomHeader header = d.bom().get(pending.parentCode());
                if (header == null) continue;
                for (BomLine child : header.children()) {
                    BigDecimal factor = nz(child.unitQty());
                    if (factor.signum() <= 0) continue;
                    BigDecimal scrap = nz(child.scrapRate());
                    BigDecimal withScrap = factor.multiply(
                            BigDecimal.ONE.add(scrap.divide(HUNDRED, 6, RoundingMode.HALF_UP)));
                    BigDecimal gross = pending.quantity().multiply(withScrap);
                    if (gross.signum() <= 0) continue;
                    String key = child.childCode() + "@" + pending.level() + "@" + pending.path();
                    Aggregate agg = levelAgg.computeIfAbsent(key, ignored -> new Aggregate(
                            child.childCode(), child.childName(), child.unit(),
                            pending.level(), pending.path() + " > " + child.childCode()));
                    agg.gross = agg.gross.add(gross);
                    agg.qtyForChildren = agg.qtyForChildren.add(gross);
                }
            }

            for (Aggregate agg : levelAgg.values()) {
                ItemMaster item = d.items().get(agg.code);
                BigDecimal stock = stockOf(d, agg.code);
                boolean recorded = d.stockRecorded().contains(agg.code);
                BigDecimal net = agg.gross.subtract(stock);
                if (net.signum() < 0) net = BigDecimal.ZERO;
                boolean shortage = net.signum() > 0;
                String attr = attributeOf(d, agg.code);
                boolean hasBom = d.bom().containsKey(agg.code)
                        && !d.bom().get(agg.code).children().isEmpty();
                // 外购件缺了是去买，不是自己做：有 BOM 也不往下拆（外购件可能挂 BOM 供比价）
                boolean selfMade = attr.isEmpty() || attr.contains("自制") || attr.contains("委外");
                boolean expandable = shortage && hasBom && selfMade;

                out.add(new MaterialLine(agg.level, agg.path, agg.code,
                        nameOf(item, agg.name), specOf(item, ""), unitOf(item, agg.unit),
                        scale(agg.gross), stock, recorded, scale(net),
                        attr.isEmpty() ? (hasBom ? "自制" : "采购") : attr,
                        shortage, expandable));

                if (shortage && !hasBom && "自制".equals(attr)) {
                    risks.add(new Risk("NO_BOM", agg.code,
                            nameOf(item, agg.name) + " 缺料且标记为自制，但没有维护物料清单"));
                }
                if (expandable) {
                    next.add(new Pending(agg.code, net, agg.path, agg.level + 1));
                }
            }
            queue = next;
        }
        return out;
    }

    /** 待展开节点。 */
    private record Pending(String parentCode, BigDecimal quantity, String path, int level) {}

    /** 同层聚合体。 */
    private static final class Aggregate {
        private final String code;
        private final String name;
        private final String unit;
        private final int level;
        private final String path;
        private BigDecimal gross = BigDecimal.ZERO;
        private BigDecimal qtyForChildren = BigDecimal.ZERO;

        private Aggregate(String code, String name, String unit, int level, String path) {
            this.code = code;
            this.name = name == null ? "" : name;
            this.unit = unit == null ? "" : unit;
            this.level = level;
            this.path = path;
        }
    }

    // ---------- 工艺路线 ----------

    /**
     * 工艺路线段只展示「已审核」的路线：草稿路线在成品行里已经有「去审核」入口，
     * 这里再显示一遍会让人以为排产条件已满足。
     */
    private List<Routing> buildRoutings(List<ProductLine> products, Data d) {
        List<Routing> out = new ArrayList<>();
        for (ProductLine product : products) {
            String code = product.routeCode() == null ? "" : product.routeCode().trim();
            List<RouteStep> steps = code.isEmpty() ? null : d.routes().get(code);
            String status = code.isEmpty() ? "" : text(d.routeStatus().get(code));
            if (steps == null || steps.isEmpty()) {
                out.add(new Routing(false, product.code(), product.name(), code, status,
                        List.of(), List.of(), List.of()));
                continue;
            }
            out.add(routingOf(code, status, steps, product));
        }
        return out;
    }

    private Routing routingOf(String code, String status, List<RouteStep> steps, ProductLine product) {
        List<String> key = new ArrayList<>();
        List<String> outsourced = new ArrayList<>();
        List<RouteStep> sorted = new ArrayList<>(steps);
        sorted.sort((a, b) -> Integer.compare(a.seq(), b.seq()));
        for (RouteStep step : sorted) {
            if (step.keyStep()) key.add(step.opName());
            if ("委外".equals(text(step.workType()))) outsourced.add(step.opName());
        }
        // 有路线但没审核：configured=false，页面提示「去审核」而不是「去新建」
        boolean configured = APPROVED_ROUTE_STATUSES.contains(status) || status.isEmpty();
        return new Routing(configured, product.code(), product.name(), code, status,
                sorted, key, outsourced);
    }

    private String resolveRouteCode(ItemMaster item, BomHeader bomHeader) {
        if (bomHeader != null && bomHeader.routeCode() != null && !bomHeader.routeCode().isBlank()) {
            return bomHeader.routeCode();
        }
        return item == null ? "" : text(item.routeCode());
    }

    /**
     * 单件用量 = 定额数量 ÷ 生产数量。
     *
     * <p>物料清单的「生产数量」是这张 BOM 的基准产出（比如基准 10 件、定额 25kg），
     * 定额是按这个基准给的，所以实际需求要先折成单件用量再乘生产数量。
     * 基准缺省或为 0 时退回「定额即单件」，避免除零。
     */
    public static BigDecimal unitUsage(BigDecimal quotaQty, BigDecimal baseQty) {
        BigDecimal quota = nz(quotaQty);
        BigDecimal base = baseQty == null || baseQty.signum() <= 0 ? BigDecimal.ONE : baseQty;
        return quota.divide(base, 6, RoundingMode.HALF_UP);
    }

    // ---------- 后续动作：请购 ----------

    /**
     * 从分析结果里挑出「该去买」的物料行：外购件 + 有净需求。
     *
     * <p>自制件缺料不进采购：那是要自己生产的，走排产而不是请购。
     * 库存够的行也不进：没有净需求就没有采购理由。
     *
     * @param codes 指定物料编码；传空表示「全部可请购行」
     * @throws IllegalArgumentException 指定的编码不在可请购范围内
     */
    public List<MaterialLine> purchasableLines(Result result, List<String> codes) {
        Map<String, MaterialLine> purchasable = new LinkedHashMap<>();
        if (result != null && result.materials() != null) {
            for (MaterialLine line : result.materials()) {
                if (line.level() == 0) continue;          // 成品本身不采购
                if (!line.shortage()) continue;           // 没有净需求
                if (line.netQty().signum() <= 0) continue;
                if (!"外购".equals(text(line.supplyType()))) continue;
                purchasable.putIfAbsent(line.code(), line);
            }
        }

        List<String> wanted = codes == null ? List.of()
                : codes.stream().map(PlanAnalyzeService::text)
                        .filter(s -> !s.isEmpty()).distinct().toList();

        if (purchasable.isEmpty()) {
            throw new IllegalArgumentException("该订单没有需要请购的外购缺料物料");
        }
        if (wanted.isEmpty()) return new ArrayList<>(purchasable.values());

        List<MaterialLine> out = new ArrayList<>();
        List<String> rejected = new ArrayList<>();
        for (String code : wanted) {
            MaterialLine line = purchasable.get(code);
            if (line == null) {
                rejected.add(code);
                continue;
            }
            out.add(line);
        }
        if (!rejected.isEmpty()) {
            throw new IllegalArgumentException("以下物料不能生成请购单（非外购或缺料量为 0）："
                    + String.join("、", rejected));
        }
        return out;
    }

    // ---------- 取值助手 ----------

    private BigDecimal stockOf(Data d, String code) {
        return d.stock() == null ? BigDecimal.ZERO : d.stock().getOrDefault(code, BigDecimal.ZERO);
    }

    private String attributeOf(Data d, String code) {
        ItemMaster item = d.items() == null ? null : d.items().get(code);
        return item == null ? "" : text(item.attribute());
    }

    private static String nameOf(ItemMaster item, String fallback) {
        if (item != null && !text(item.name()).isEmpty()) return item.name();
        return fallback == null ? "" : fallback;
    }

    private static String specOf(ItemMaster item, String fallback) {
        if (item != null && !text(item.spec()).isEmpty()) return item.spec();
        return fallback == null ? "" : fallback;
    }

    private static String unitOf(ItemMaster item, String fallback) {
        if (item != null && !text(item.unit()).isEmpty()) return item.unit();
        return fallback == null ? "" : fallback;
    }

    /** 最低满足率：所有缺料行里最差的那个，作为整体齐套率。 */
    private static BigDecimal minFulfillRate(List<MaterialLine> materials) {
        BigDecimal min = HUNDRED;
        for (MaterialLine line : materials) {
            if (line.level() == 0) continue;
            BigDecimal rate = line.grossQty().signum() <= 0
                    ? HUNDRED
                    : line.stockQty().multiply(HUNDRED).divide(line.grossQty(), 2, RoundingMode.HALF_UP);
            if (rate.compareTo(HUNDRED) > 0) rate = HUNDRED;
            if (rate.compareTo(min) < 0) min = rate;
        }
        return min;
    }

    private static BigDecimal nz(BigDecimal value) {
        return value == null ? BigDecimal.ZERO : value;
    }

    private static BigDecimal scale(BigDecimal value) {
        return nz(value).setScale(4, RoundingMode.HALF_UP);
    }

    private static String text(String value) {
        return value == null ? "" : value.trim();
    }

    private static Data emptyData() {
        return new Data(Map.of(), Map.of(), Map.of(), Map.of(), Set.of(), Map.of());
    }
}
