package com.mes.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.entity.StockBalance;
import com.mes.mapper.FormDataMapper;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 计划分析的数据装载与编排：把库里的销售订单/BOM/工艺路线/库存读出来，
 * 交给 {@link PlanAnalyzeService} 算，再拼成前端固定契约。
 *
 * <p><b>库存占用口径</b>（与 MpsQueryService 一致）：可用量 = 现存量 − 其它已审核销售订单
 * 尚未排产的数量。这样同一个物料被多张订单共用时，先分析的订单看到的是剩余可用量，
 * 不会出现「两张单各自看到全部库存，都判齐套」的假齐套。
 */
@Service
public class PlanAnalyzeQueryService {

    private static final Set<String> EFFECTIVE_STATUSES =
            Set.of("已审核", "生产中", "已完工");

    private final FormDataMapper formMapper;
    private final ObjectMapper json;
    private final PlanAnalyzeService engine;
    private final StockBalanceService stockBalanceService;

    public PlanAnalyzeQueryService(FormDataMapper formMapper, ObjectMapper json,
                                   PlanAnalyzeService engine,
                                   StockBalanceService stockBalanceService) {
        this.formMapper = formMapper;
        this.json = json;
        this.engine = engine;
        this.stockBalanceService = stockBalanceService;
    }

    /**
     * 可分析的销售订单列表：已审核 + 还有未排产数量。
     * 前端「选单」只需要单号、客户、交期与待产数量。
     */
    public List<Map<String, Object>> candidateOrders() {
        Map<String, List<String>> moByOrder = moByOrderIndex();
        Map<String, BigDecimal> scheduledByProduct = scheduledIndex();
        List<Map<String, Object>> out = new ArrayList<>();

        for (FormData fd : salesOrders()) {
            Map<String, Object> head = parse(fd.getData());
            Map<String, Object> detail = parse(fd.getDetailData());
            List<Map<String, Object>> items = rows(detail, "items");
            BigDecimal open = BigDecimal.ZERO;
            List<String> products = new ArrayList<>();
            for (Map<String, Object> item : items) {
                String code = firstText(item, "存货编码", "产品编码");
                if (code.isEmpty()) continue;
                BigDecimal qty = number(first(item, "数量", "销售数量"));
                BigDecimal already = scheduledByProduct.getOrDefault(key(code, fd.getFormNo()),
                        BigDecimal.ZERO);
                BigDecimal remain = qty.subtract(already);
                if (remain.signum() <= 0) continue;
                open = open.add(remain);
                if (!products.contains(code)) products.add(code);
            }
            List<String> mos = moByOrder.getOrDefault(fd.getFormNo(), List.of());
            out.add(mapOf(
                    "salesOrderNo", fd.getFormNo(),
                    "customer", firstText(head, "客户", "客户名称", "往来单位"),
                    "orderDate", firstText(head, "单据日期"),
                    "dueDate", dueDateOf(head, items),
                    "openQty", open,
                    "productCodes", String.join("、", products),
                    "manuOrderNos", String.join("、", mos),
                    "hasManuOrder", !mos.isEmpty(),
                    "status", fd.getStatus()));
        }
        return out;
    }

    /**
     * 分析一张销售订单。
     *
     * @param salesOrderNo 销售订单号
     */
    public Map<String, Object> analyze(String salesOrderNo) {
        Analyzed analyzed = analyzeTyped(salesOrderNo);
        return toContract(analyzed.result(), analyzed.data(), analyzed.lines());
    }

    /** 分析结果与它的装载数据：请购生单要复用同一份结果，不重复算两遍。 */
    private record Analyzed(PlanAnalyzeService.Result result,
                            PlanAnalyzeService.Data data,
                            List<PlanAnalyzeService.OrderLine> lines) {}

    /** 取出类型化的分析结果（请购等后续动作复用）。 */
    private Analyzed analyzeTyped(String salesOrderNo) {
        if (salesOrderNo == null || salesOrderNo.isBlank()) {
            throw new IllegalArgumentException("请选择一张销售订单");
        }
        FormData order = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "SO_ORDER")
                .eq(FormData::getFormNo, salesOrderNo));
        if (order == null) throw new IllegalArgumentException("销售订单不存在：" + salesOrderNo);

        Map<String, Object> head = parse(order.getData());
        Map<String, Object> detail = parse(order.getDetailData());

        // 需求行扣掉「本单已排产」，与 MpsQueryService 同口径
        Map<String, BigDecimal> scheduled = scheduledIndexOfOrder(salesOrderNo);
        List<PlanAnalyzeService.OrderLine> lines = new ArrayList<>();
        for (Map<String, Object> item : rows(detail, "items")) {
            String code = firstText(item, "存货编码", "产品编码");
            if (code.isEmpty()) continue;
            BigDecimal qty = number(first(item, "数量", "销售数量"));
            BigDecimal already = scheduled.getOrDefault(code, BigDecimal.ZERO);
            BigDecimal open = qty.subtract(already);
            if (open.signum() <= 0) continue;
            lines.add(new PlanAnalyzeService.OrderLine(code,
                    firstText(item, "存货名称", "产品名称"),
                    firstText(item, "规格型号"),
                    firstText(item, "销售单位", "计量单位", "主单位"),
                    open,
                    firstText(item, "预计交货日期")));
        }

        PlanAnalyzeService.Data data = loadData(salesOrderNo, lines);

        String dueDate = dueDateOf(head, rows(detail, "items"));
        PlanAnalyzeService.OrderHeader header = new PlanAnalyzeService.OrderHeader(
                salesOrderNo,
                firstText(head, "客户", "客户名称", "往来单位"),
                firstText(head, "单据日期"),
                dueDate,
                priorityOf(dueDate));

        PlanAnalyzeService.Result result = engine.analyze(header, lines, data);
        return new Analyzed(result, data, lines);
    }

    /**
     * 计划分析 → 请购单明细：挑出外购件缺料行，组成 PU_REQ 的 items。
     *
     * <p>为什么在服务端重算而不是信前端传的数量：请购数量直接决定采购订单，
     * 必须与同一份分析结果一致，不能被浏览器改。
     *
     * @param codes 指定物料编码；空表示全部可请购行
     * @throws IllegalArgumentException 没有可请购行，或指定了不可请购的编码
     */
    public PurchaseReqPlan purchaseReqPlan(String salesOrderNo, List<String> codes) {
        Analyzed analyzed = analyzeTyped(salesOrderNo);
        List<PlanAnalyzeService.MaterialLine> picked =
                engine.purchasableLines(analyzed.result(), codes);

        List<Map<String, Object>> rows = new ArrayList<>();
        for (PlanAnalyzeService.MaterialLine line : picked) {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("存货编码", line.code());
            row.put("存货名称", line.name());
            row.put("规格型号", line.spec());
            row.put("采购单位", line.unit());
            row.put("数量", line.netQty().stripTrailingZeros());
            row.put("需求日期", analyzed.result().order().dueDate());
            row.put("现存量", line.stockQty().stripTrailingZeros());
            row.put("现存量说明", line.stockRecordExists() ? "" : "无库存记录");
            row.put("备注", "计划分析：" + salesOrderNo + " 缺 " + line.path());
            rows.add(row);
        }
        return new PurchaseReqPlan(salesOrderNo, analyzed.result().order().customer(),
                analyzed.result().order().dueDate(), rows);
    }

    /** 请购单生单素材。 */
    public record PurchaseReqPlan(String salesOrderNo, String customer, String dueDate,
                                  List<Map<String, Object>> rows) {}

    // ---------- 数据装载 ----------

    /** 装配引擎输入：存货档案、BOM、工艺路线、可用库存、已生成加工单。 */
    private PlanAnalyzeService.Data loadData(String salesOrderNo,
                                             List<PlanAnalyzeService.OrderLine> ownLines) {
        Map<String, PlanAnalyzeService.ItemMaster> items = itemIndex();
        Map<String, PlanAnalyzeService.BomHeader> bom = bomIndex();
        Map<String, List<PlanAnalyzeService.RouteStep>> routes = routeIndex(new LinkedHashMap<>());
        Map<String, BigDecimal> stock = availableStock(salesOrderNo, ownLines, bom);
        Set<String> recorded = recordedStockCodes();
        Map<String, List<String>> moByOrder = moByOrderIndex();
        Map<String, String> routeStatus = new LinkedHashMap<>();
        Map<String, List<PlanAnalyzeService.RouteStep>> routes2 = routeIndex(routeStatus);
        return new PlanAnalyzeService.Data(items, bom, routes2, stock, recorded, moByOrder, routeStatus);
    }

    /** 存货档案：INV 面板明细，键为存货编码。 */
    private Map<String, PlanAnalyzeService.ItemMaster> itemIndex() {
        Map<String, PlanAnalyzeService.ItemMaster> out = new LinkedHashMap<>();
        for (FormData fd : docs("INV")) {
            for (Map<String, Object> row : rows(parse(fd.getDetailData()), "items")) {
                String code = firstText(row, "存货编码");
                if (code.isEmpty()) continue;
                out.put(code, new PlanAnalyzeService.ItemMaster(code,
                        firstText(row, "存货名称"),
                        firstText(row, "规格型号"),
                        firstText(row, "计量单位", "主单位"),
                        firstText(row, "属性"),
                        firstText(row, "工艺路线")));
            }
        }
        return out;
    }

    /**
     * BOM 索引：父件编码 → 一张（默认）BOM。
     * 同一父件有多张 BOM 时优先取「默认BOM=是」，否则取第一张已审核的。
     */
    private Map<String, PlanAnalyzeService.BomHeader> bomIndex() {
        Map<String, PlanAnalyzeService.BomHeader> out = new LinkedHashMap<>();
        Map<String, Boolean> isDefault = new HashMap<>();
        for (FormData fd : docs("BOM")) {
            Map<String, Object> head = parse(fd.getData());
            Map<String, Object> detail = parse(fd.getDetailData());
            List<Map<String, Object>> children = rows(detail, "children");
            if (children.isEmpty()) continue;
            // 工艺路线编码挂在 BOM 表头（data），明细行里没有
            String headerRoute = firstText(head, "工艺路线编码");
            // 按父件分组：一张 BOM 单可能混多个父件
            Map<String, List<Map<String, Object>>> byParent = new LinkedHashMap<>();
            for (Map<String, Object> child : children) {
                String parent = firstText(child, "父件编码");
                if (parent.isEmpty()) continue;
                byParent.computeIfAbsent(parent, k -> new ArrayList<>()).add(child);
            }
            for (Map.Entry<String, List<Map<String, Object>>> entry : byParent.entrySet()) {
                String parent = entry.getKey();
                Map<String, Object> firstRow = entry.getValue().get(0);
                boolean isDef = Boolean.TRUE.equals(firstRow.get("默认BOM"))
                        || "是".equals(String.valueOf(firstRow.get("默认BOM")));
                Boolean existing = isDefault.get(parent);
                if (out.containsKey(parent) && !(isDef && !Boolean.TRUE.equals(existing))) continue;

                List<PlanAnalyzeService.BomLine> linesOut = new ArrayList<>();
                for (Map<String, Object> row : entry.getValue()) {
                    String child = firstText(row, "子件编码");
                    if (child.isEmpty()) continue;
                    linesOut.add(new PlanAnalyzeService.BomLine(child,
                            firstText(row, "子件名称"),
                            firstText(row, "子件计量单位", "计量单位"),
                            // 定额是按「生产数量」这个基准给的，先折成单件用量
                            PlanAnalyzeService.unitUsage(
                                    number(first(row, "定额数量")),
                                    number(first(row, "生产数量"))),
                            number(first(row, "损耗率%"))));
                }
                String route = headerRoute.isEmpty()
                        ? firstText(firstRow, "工艺路线编码") : headerRoute;
                // 用单据号而不是明细里的「物料清单编码」：两者在历史数据里可能不一致
                // （如单据号 BOMF-001 的明细写 BOM-003），跳转 BOM 表单必须按单据号定位。
                out.put(parent, new PlanAnalyzeService.BomHeader(
                        fd.getFormNo(), parent, route, linesOut));
                isDefault.put(parent, isDef);
            }
        }
        return out;
    }

    /** 工艺路线索引：路线编码 → 按加工顺序排好的工序。 */
    private Map<String, List<PlanAnalyzeService.RouteStep>> routeIndex(Map<String, String> statusOut) {
        Map<String, List<PlanAnalyzeService.RouteStep>> out = new LinkedHashMap<>();
        for (FormData fd : docsWithDrafts("ROUTE")) {
            // 主从结构改造后，路线编码在表头；老数据在明细行，两个都试
            Map<String, Object> head = parse(fd.getData());
            String headerCode = firstText(head, "工艺路线编码");
            if (headerCode.isEmpty()) continue;
            boolean stopped = Boolean.TRUE.equals(head.get("停用"))
                    || "是".equals(String.valueOf(head.get("停用")));
            if (stopped) continue;
            // 同一路线有多条（草稿 + 已审核）时，已审核的优先作为状态
            String prev = statusOut.get(headerCode);
            String cur = fd.getStatus();
            if (prev == null || !"已审核".equals(prev)) statusOut.put(headerCode, cur);
            List<PlanAnalyzeService.RouteStep> steps = out.computeIfAbsent(
                    headerCode, k -> new ArrayList<>());
            for (Map<String, Object> row : rows(parse(fd.getDetailData()), "processes")) {
                String rowCode = firstText(row, "工艺路线编码");
                // 明细自带编码且与表头不符时，以明细为准（兼容一张单混多条路线的老数据）
                if (!rowCode.isEmpty() && !rowCode.equals(headerCode)) {
                    out.computeIfAbsent(rowCode, k -> new ArrayList<>()).add(stepOf(row));
                    continue;
                }
                steps.add(stepOf(row));
            }
        }
        return out;
    }

    private static PlanAnalyzeService.RouteStep stepOf(Map<String, Object> row) {
        return new PlanAnalyzeService.RouteStep(
                (int) Math.round(number(first(row, "加工顺序")).doubleValue()),
                firstText(row, "工序编码"),
                firstText(row, "工序名称"),
                firstText(row, "加工方式"),
                firstText(row, "生产车间"),
                Boolean.TRUE.equals(row.get("关键工序"))
                        || "是".equals(String.valueOf(row.get("关键工序"))),
                number(first(row, "标准合格率%")));
    }

    /**
     * 可用库存：现存量 − 其它订单按交期先到先得占用后的余量。
     *
     * <p>为什么必须展开到物料层：两张销售订单共用同一个原材料时，如果各自都看到
     * 全部现存量，两张单会同时判「齐套」，实际只有一张能开工。这里按「交期早的先占」
     * 逐单扣减：先用 BOM 把每张单的成品需求折成物料需求，再按交期顺序从库存里扣，
     * 本单拿到的就是扣完前面订单后剩下的量。
     *
     * <p>只算已审核、还有未排产数量的订单；已生成加工单的部分已经被实际占用，
     * 不再从销售订单侧重复扣。
     */
    private Map<String, BigDecimal> availableStock(String salesOrderNo,
                                                   List<PlanAnalyzeService.OrderLine> ownLines,
                                                   Map<String, PlanAnalyzeService.BomHeader> bom) {
        Map<String, BigDecimal> stock = rawStock();

        // 其它订单按交期升序占用；本单排最后，看到的就是别人占完剩下的
        List<FormData> others = new ArrayList<>(salesOrders());
        others.removeIf(fd -> fd.getFormNo().equals(salesOrderNo));
        others.sort((a, b) -> {
            String da = dueDateOfOrder(a);
            String db = dueDateOfOrder(b);
            int c = da.compareTo(db);
            return c != 0 ? c : a.getFormNo().compareTo(b.getFormNo());
        });

        for (FormData fd : others) {
            for (PlanAnalyzeService.OrderLine line : linesOf(fd)) {
                deduct(stock, line.itemCode(), line.quantity(), bom, 0);
            }
        }
        // 本单自己的需求不扣：它就是这次要分析的对象
        return stock;
    }

    /** 从库存里扣掉一个成品需求，并按 BOM 展开扣它的物料。 */
    private void deduct(Map<String, BigDecimal> stock, String code, BigDecimal qty,
                        Map<String, PlanAnalyzeService.BomHeader> bom, int depth) {
        if (code == null || code.isBlank() || qty == null || qty.signum() <= 0) return;
        BigDecimal current = stock.getOrDefault(code, BigDecimal.ZERO);
        BigDecimal taken = current.min(qty);
        stock.put(code, current.subtract(taken));
        // 库存没覆盖的部分要靠生产/采购，其物料也要占
        BigDecimal remaining = qty.subtract(taken);
        if (remaining.signum() <= 0 || depth >= 8) return;
        PlanAnalyzeService.BomHeader header = bom.get(code);
        if (header == null) return;
        for (PlanAnalyzeService.BomLine child : header.children()) {
            BigDecimal factor = nz(child.unitQty());
            if (factor.signum() <= 0) continue;
            BigDecimal scrap = nz(child.scrapRate());
            BigDecimal withScrap = factor.multiply(BigDecimal.ONE.add(
                    scrap.divide(new BigDecimal("100"), 6, java.math.RoundingMode.HALF_UP)));
            deduct(stock, child.childCode(), remaining.multiply(withScrap), bom, depth + 1);
        }
    }

    /** 现存量：按存货编码聚合，不区分仓库（排产时还不知道从哪个仓领）。 */
    private Map<String, BigDecimal> rawStock() {
        Map<String, BigDecimal> out = new LinkedHashMap<>();
        for (StockBalance balance : stockBalanceService.list()) {
            String code = balance.getItemCode();
            if (code == null || code.isBlank()) continue;
            out.merge(code, nz(balance.getQuantity()), BigDecimal::add);
        }
        return out;
    }

    /** 某张销售订单里还没排产的需求行。 */
    private List<PlanAnalyzeService.OrderLine> linesOf(FormData order) {
        Map<String, BigDecimal> scheduled = scheduledIndexOfOrder(order.getFormNo());
        List<PlanAnalyzeService.OrderLine> out = new ArrayList<>();
        for (Map<String, Object> item : rows(parse(order.getDetailData()), "items")) {
            String code = firstText(item, "存货编码", "产品编码");
            if (code.isEmpty()) continue;
            BigDecimal qty = number(first(item, "数量", "销售数量"));
            BigDecimal open = qty.subtract(scheduled.getOrDefault(code, BigDecimal.ZERO));
            if (open.signum() <= 0) continue;
            out.add(new PlanAnalyzeService.OrderLine(code,
                    firstText(item, "存货名称", "产品名称"), "", "", open, ""));
        }
        return out;
    }

    private String dueDateOfOrder(FormData fd) {
        Map<String, Object> head = parse(fd.getData());
        String due = firstText(head, "预计交货日期", "交货日期", "预完工日");
        if (!due.isEmpty()) return due;
        return dueDateOf(head, rows(parse(fd.getDetailData()), "items"));
    }

    /** 有库存记录的存货编码集合：用来区分「库存 0」与「从没入过库」。 */
    private Set<String> recordedStockCodes() {
        Set<String> out = new LinkedHashSet<>();
        for (StockBalance balance : stockBalanceService.list()) {
            if (balance.getItemCode() != null) out.add(balance.getItemCode());
        }
        return out;
    }

    /** 已生成加工单：销售订单号 → 加工单号列表。 */
    private Map<String, List<String>> moByOrderIndex() {
        Map<String, List<String>> out = new LinkedHashMap<>();
        for (FormData fd : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "MANU_ORDER")
                .in(FormData::getStatus, EFFECTIVE_STATUSES))) {
            Map<String, Object> head = parse(fd.getData());
            String soNo = normalizeOrderNo(firstText(head, "销售订单号"));
            if (soNo.isEmpty()) continue;
            out.computeIfAbsent(soNo, k -> new ArrayList<>()).add(fd.getFormNo());
        }
        return out;
    }

    /** 全量已排产索引：存货编码 + 订单号 → 数量（本单内已排产）。 */
    private Map<String, BigDecimal> scheduledIndex() {
        Map<String, BigDecimal> out = new LinkedHashMap<>();
        for (FormData fd : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "MANU_ORDER")
                .in(FormData::getStatus, EFFECTIVE_STATUSES))) {
            Map<String, Object> head = parse(fd.getData());
            String soNo = normalizeOrderNo(firstText(head, "销售订单号"));
            if (soNo.isEmpty()) continue;
            for (Map<String, Object> row : rows(parse(fd.getDetailData()), "products")) {
                String code = firstText(row, "产品编码");
                if (code.isEmpty()) continue;
                out.merge(key(code, soNo), number(first(row, "数量")), BigDecimal::add);
            }
        }
        return out;
    }

    /** 某张销售订单的已排产数量，键为存货编码。 */
    private Map<String, BigDecimal> scheduledIndexOfOrder(String salesOrderNo) {
        Map<String, BigDecimal> out = new LinkedHashMap<>();
        for (FormData fd : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "MANU_ORDER")
                .in(FormData::getStatus, EFFECTIVE_STATUSES))) {
            Map<String, Object> head = parse(fd.getData());
            if (!salesOrderNo.equals(normalizeOrderNo(firstText(head, "销售订单号")))) continue;
            for (Map<String, Object> row : rows(parse(fd.getDetailData()), "products")) {
                String code = firstText(row, "产品编码");
                if (code.isEmpty()) continue;
                out.merge(code, number(first(row, "数量")), BigDecimal::add);
            }
        }
        return out;
    }

    // ---------- 输出装配 ----------

    /** 固定契约：前端只渲染，不再二次计算。 */
    private Map<String, Object> toContract(PlanAnalyzeService.Result result,
                                           PlanAnalyzeService.Data data,
                                           List<PlanAnalyzeService.OrderLine> lines) {
        List<Map<String, Object>> products = new ArrayList<>();
        for (PlanAnalyzeService.ProductLine line : result.products()) {
            products.add(mapOf(
                    "code", line.code(), "name", line.name(), "spec", line.spec(),
                    "unit", line.unit(), "demandQty", line.demandQty(),
                    "stockQty", line.stockQty(), "stockRecorded", line.stockRecorded(),
                    "shortQty", line.shortQty(), "bomCode", line.bomCode(),
                    "routeCode", line.routeCode(), "routeStatus", line.routeStatus(),
                    "routingConfigured", line.routingConfigured()));
        }

        List<Map<String, Object>> materials = new ArrayList<>();
        for (PlanAnalyzeService.MaterialLine line : result.materials()) {
            materials.add(mapOf(
                    "level", line.level(), "path", line.path(),
                    "code", line.code(), "name", line.name(), "spec", line.spec(),
                    "unit", line.unit(), "grossQty", line.grossQty(),
                    "stockQty", line.stockQty(), "stockRecordExists", line.stockRecordExists(),
                    "netQty", line.netQty(), "supplyType", line.supplyType(),
                    "shortage", line.shortage(), "expandable", line.expandable()));
        }

        // 每个成品一条路线：订单里有几个产品就返回几条，页面逐条展示
        java.util.function.Function<PlanAnalyzeService.Routing, Map<String, Object>> routingMap = routing -> {
            List<Map<String, Object>> routeSteps = new ArrayList<>();
            for (PlanAnalyzeService.RouteStep step : routing.steps()) {
                routeSteps.add(mapOf("seq", step.seq(), "opCode", step.opCode(), "opName", step.opName(),
                        "workType", step.workType(), "workshop", step.workshop(),
                        "keyStep", step.keyStep(), "stdPassRate", step.stdPassRate()));
            }
            return mapOf(
                    "configured", routing.configured(),
                    "productCode", routing.productCode(),
                    "productName", routing.productName(),
                    "routeCode", routing.routeCode(),
                    "routeStatus", routing.routeStatus(),
                    "steps", routeSteps,
                    "keySteps", routing.keySteps(),
                    "outsourced", routing.outsourced());
        };
        List<Map<String, Object>> routings = new ArrayList<>();
        for (PlanAnalyzeService.Routing routing : result.routings()) {
            routings.add(routingMap.apply(routing));
        }

        List<Map<String, Object>> risks = new ArrayList<>();
        for (PlanAnalyzeService.Risk risk : result.risks()) {
            risks.add(mapOf("code", risk.code(), "item", risk.item(), "message", risk.message()));
        }

        return mapOf(
                "order", mapOf(
                        "no", result.order().no(), "customer", result.order().customer(),
                        "orderDate", result.order().orderDate(), "dueDate", result.order().dueDate(),
                        "priority", result.order().priority(),
                        "linkedMo", data.moByOrder().getOrDefault(result.order().no(), List.of())),
                "products", products,
                "materials", materials,
                "routing", routingMap.apply(result.routing()),
                "routings", routings,
                "risks", risks,
                "summary", mapOf(
                        "readyToPlan", result.summary().readyToPlan(),
                        "blockingCount", result.summary().blockingCount(),
                        "shortageCount", result.summary().shortageCount(),
                        "kitRate", result.summary().kitRate(),
                        "maxLevel", result.summary().maxLevel()),
                "narrative", buildNarrative(result));
    }

    /**
     * 固定格式结论：规则模板生成，AI 可用时替换同结构的文案。
     *
     * <p>数字永远来自引擎，这里只组织句子，所以关掉 AI 页面也不会空。
     */
    private Map<String, Object> buildNarrative(PlanAnalyzeService.Result result) {
        List<String> blocked = new ArrayList<>();
        List<String> causes = new ArrayList<>();
        List<String> actions = new ArrayList<>();

        for (PlanAnalyzeService.Risk risk : result.risks()) {
            switch (risk.code()) {
                case "NO_BOM" -> {
                    blocked.add(risk.item() + " 没有物料清单，无法计算物料需求");
                    actions.add("补齐 " + risk.item() + " 的物料清单后再排产");
                }
                case "NO_ITEM" -> {
                    blocked.add(risk.item() + " 不在存货档案中");
                    actions.add("先在存货档案建档：" + risk.item());
                }
                case "NO_ROUTE" -> {
                    // 用 message（含存货名称），不要用 item（是编码）
                    causes.add(risk.message());
                    actions.add("为 " + risk.item() + " 维护工艺路线");
                }
                case "HAS_MO" -> causes.add("该订单已生成加工单（" + risk.item() + "），本次分析仅供核对");
                default -> causes.add(risk.message());
            }
        }

        // 成品自身库存不足是「待生产」，不是缺料：排产就是把它做出来。
        // 只有 BOM 展开出来的物料（level ≥ 1）才是真正要补的料。
        List<PlanAnalyzeService.MaterialLine> shortages = result.materials().stream()
                .filter(PlanAnalyzeService.MaterialLine::shortage)
                .filter(line -> line.level() > 0)
                .toList();
        if (!shortages.isEmpty()) {
            int shown = 0;
            for (PlanAnalyzeService.MaterialLine line : shortages) {
                if (shown++ >= 5) break;
                blocked.add(line.name() + "（" + line.code() + "）缺 "
                        + line.netQty().stripTrailingZeros().toPlainString() + " " + line.unit());
            }
            if (shortages.size() > 5) blocked.add("另有 " + (shortages.size() - 5) + " 项缺料");
            actions.add("先处理 " + shortages.size() + " 项缺料，或调整交期");
        }

        // 成品层的待生产量单独说清楚，避免和缺料混在一起
        List<PlanAnalyzeService.ProductLine> productions = result.products().stream()
                .filter(p -> p.shortQty().signum() > 0)
                .toList();
        if (!productions.isEmpty()) {
            StringBuilder sb = new StringBuilder("待生产：");
            int shown = 0;
            for (PlanAnalyzeService.ProductLine p : productions) {
                if (shown++ > 0) sb.append("、");
                sb.append(p.name()).append(" ")
                        .append(p.shortQty().stripTrailingZeros().toPlainString())
                        .append(" ").append(p.unit());
                if (shown >= 4) break;
            }
            causes.add(sb.toString());
        }

        if (blocked.isEmpty() && causes.isEmpty()) {
            blocked.add("无阻塞项");
        }
        if (actions.isEmpty()) {
            actions.add("按建议开工日安排排产");
        }

        return mapOf("source", "RULE", "blocked", blocked, "causes", causes, "actions", actions);
    }

    // ---------- 基础查询 ----------

    private List<FormData> salesOrders() {
        return formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "SO_ORDER")
                .eq(FormData::getStatus, "已审核"));
    }

    /** 档案类面板，含草稿（用于区分「没有」和「有草稿待审核」）。 */
    private List<FormData> docsWithDrafts(String panelCode) {
        return formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, panelCode)
                .in(FormData::getStatus, List.of("已审核", "草稿", "审批中", "启用")));
    }

    /** 已审核的档案类面板（BOM、ROUTE、INV）。 */
    private List<FormData> docs(String panelCode) {
        LambdaQueryWrapper<FormData> query = new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, panelCode);
        // INV 存货档案走「启用/停用」状态，不是审批状态，因此不按已审核过滤
        if (!"INV".equals(panelCode)) query.eq(FormData::getStatus, "已审核");
        return formMapper.selectList(query);
    }

    // ---------- 取值助手 ----------

    private Map<String, Object> parse(String value) {
        if (value == null || value.isBlank()) return new LinkedHashMap<>();
        try {
            return json.readValue(value,
                    new TypeReference<Map<String, Object>>() {});
        } catch (Exception ignored) {
            return new LinkedHashMap<>();
        }
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> rows(Map<String, Object> detail, String key) {
        if (detail == null) return List.of();
        Object value = detail.get(key);
        if (!(value instanceof List<?> list)) return List.of();
        List<Map<String, Object>> out = new ArrayList<>();
        for (Object item : list) {
            if (item instanceof Map<?, ?> map) out.add((Map<String, Object>) map);
        }
        return out;
    }

    /** 单号归一：去掉分隔符后比较，兼容 SO20260811-03 与 SO-2026-08-0001 两种写法。 */
    static String normalizeOrderNo(String raw) {
        if (raw == null) return "";
        return raw.trim().replace("-", "").replace(" ", "").toUpperCase();
    }

    private static String key(String code, String salesOrderNo) {
        return code + "|" + salesOrderNo;
    }

    private static String dueDateOf(Map<String, Object> head, List<Map<String, Object>> items) {
        String explicit = firstText(head, "预计交货日期", "交货日期", "预完工日");
        if (!explicit.isEmpty()) return explicit;
        for (Map<String, Object> item : items) {
            String lineDue = firstText(item, "预计交货日期", "交货日期");
            if (!lineDue.isEmpty()) return lineDue;
        }
        return "";
    }

    /** 优先级：剩余天数 ≤5 特急，≤10 紧急，其余正常。 */
    private static String priorityOf(String dueDate) {
        if (dueDate == null || dueDate.isBlank()) return "正常";
        try {
            java.time.LocalDate due = java.time.LocalDate.parse(
                    dueDate.length() > 10 ? dueDate.substring(0, 10) : dueDate);
            long days = java.time.temporal.ChronoUnit.DAYS.between(java.time.LocalDate.now(), due);
            if (days <= 5) return "特急";
            if (days <= 10) return "紧急";
            return "正常";
        } catch (Exception ignored) {
            return "正常";
        }
    }

    private static Object first(Map<String, Object> row, String... keys) {
        if (row == null) return null;
        for (String k : keys) {
            Object v = row.get(k);
            if (v != null && !String.valueOf(v).isBlank()) return v;
        }
        return null;
    }

    private static String firstText(Map<String, Object> row, String... keys) {
        Object v = first(row, keys);
        return v == null ? "" : String.valueOf(v).trim();
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

    private static BigDecimal nz(BigDecimal value) {
        return value == null ? BigDecimal.ZERO : value;
    }

    private static Map<String, Object> mapOf(Object... kv) {
        Map<String, Object> out = new LinkedHashMap<>();
        for (int i = 0; i + 1 < kv.length; i += 2) {
            out.put(String.valueOf(kv[i]), kv[i + 1]);
        }
        return out;
    }
}
