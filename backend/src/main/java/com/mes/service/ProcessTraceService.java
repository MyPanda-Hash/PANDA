package com.mes.service;

import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.ArrayDeque;
import java.util.Comparator;
import java.util.Deque;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 业务流程追踪：给定任意单号，沿生单关系正反向找全相关单据，判定每一步的状态与卡点。
 *
 * 关联关系来自实际数据里的字段（不是设计推演）：
 * - 表头「来源单号」：ARRIVAL_IN / INSPECTION / SALE_INV / OUTSOURCE_ORDER / TRANSFER
 * - 表头「销售订单号」：MANU_ORDER / SALE_OUT / MATERIAL_OUT 按订单号串联
 * - 表头「匹配来源单号」：采购入库到采购订单
 * - 明细「加工单号」「来源面板+来源单号」：材料出库到加工单
 *
 * 纯计算实现（不查库），便于单测；数据库读取在 ProcessTraceQueryService。
 */
@Service
public class ProcessTraceService {

    /**
     * 加工单上的一道工序。路线里每道工序对应一个「工序汇报单」环节。
     */
    public record Process(String code, String name) {}

    /**
     * 一张单据的追踪摘要。
     *
     * @param panelCode   面板编码
     * @param docNo       单号
     * @param status      单据状态（草稿/审批中/已审核/已完工等）
     * @param links       该单据引用的上游单号（来源单号/销售订单号/加工单号等）
     * @param processCode 工序编码（只有工序汇报单等工序类单据有值）
     * @param processName 工序名称（显示用，路线里跟着环节一起展示）
     * @param processes   该单据上的工序清单（生产加工单用，决定路线展开几个汇报环节）
     */
    public record Doc(String panelCode, String docNo, String status, List<String> links,
                      String processCode, String processName, List<Process> processes) {

        public Doc {
            links = links == null ? List.of() : links;
            processCode = processCode == null ? "" : processCode;
            processName = processName == null ? "" : processName;
            processes = processes == null ? List.of() : processes;
        }

        /** 不带工序信息的单据（销售订单、出入库单等）。 */
        public Doc(String panelCode, String docNo, String status, List<String> links) {
            this(panelCode, docNo, status, links, "", "", List.of());
        }
    }

    /**
     * 追踪结果的一步（实际存在的单据）。
     *
     * @param state 完成 / 进行中 / 待开始
     */
    public record Step(String panelCode, String panelName, String docNo, String status,
                       String state, String processCode, String processName, int seq) {}

    /**
     * 标准路线上的一个环节。
     *
     * <p>路线是固定的：有存货的销售订单直接出库，没有存货的才走生产加工。
     * 已经发生的环节带单号与状态，还没发生的环节 {@code present=false}，状态为「待生成」。
     */
    public record RouteNode(String panelCode, String panelName, String processCode, String processName,
                            String docNo, String status, String state, boolean present, int seq,
                            List<RouteNode> branches) {

        public RouteNode {
            branches = branches == null ? List.of() : branches;
        }
    }

    /**
     * 并行排产线：一个销售订单行对应一条线（一张加工单、一份排产）。
     *
     * <p>排产是按订单行做的，三行订单就并行三条线，各走各的加工单与后续单据；
     * 一条线上只放本行自己的环节，销售订单与出货开票在页面里共用。
     */
    public record Lane(String key, String moNo, String productCode, String productName, String sopNo,
                       List<RouteNode> nodes) {

        public Lane {
            nodes = nodes == null ? List.of() : nodes;
        }
    }

    /** 追踪结果。 */
    public record TraceResult(List<Step> steps, List<RouteNode> route, List<Lane> lanes,
                              String stalledDocNo, String stalledReason, String currentDocNo,
                              String message) {}

    /** 业务流程顺序权重：数字小的在上游。用于把追踪结果按业务先后排序。 */
    private static final Map<String, Integer> PANEL_ORDER = buildPanelOrder();

    private static Map<String, Integer> buildPanelOrder() {
        Map<String, Integer> m = new LinkedHashMap<>();
        int seq = 0;
        // 销售链
        for (String code : List.of("QUOTE_ORDER", "SO_ORDER", "OUTSOURCE_ORDER",
                "OUTSOURCE_ISSUE", "OUTSOURCE_IN", "OUTSOURCE_FEE")) {
            m.put(code, seq++);
        }
        // 采购链
        for (String code : List.of("PU_REQ", "PU_ORDER", "ARRIVAL_IN", "INSPECTION",
                "PURCHASE_IN", "PURCHASE_RETURN")) {
            m.put(code, seq++);
        }
        // 生产链：销售订单 → 工单排产 → 加工单 → 领料 → 材料出库 → 首件报检 → 工序汇报 → 入库
        for (String code : List.of("DISPATCH", "MANU_ORDER", "MATERIAL_REQ", "MATERIAL_OUT",
                "FIRST_INSPECT", "PROCESS_REPORT", "PROCESS_INSPECT_APPLY", "PROCESS_INSPECTION",
                "TRANSFER", "FINISH_INSPECT", "FINISH_IN")) {
            m.put(code, seq++);
        }
        // 出库与开票
        for (String code : List.of("SALE_OUT", "SALE_INV", "SALE_INVOICE", "SALE_RETURN",
                "RECEIPT", "RECEIPT_PAYABLE")) {
            m.put(code, seq++);
        }
        return Map.copyOf(m);
    }

    /** 面板编码 → 中文名，追踪步骤直接展示。 */
    private static final Map<String, String> PANEL_NAMES = Map.ofEntries(
            Map.entry("QUOTE_ORDER", "报价单"),
            Map.entry("SO_ORDER", "销售订单"),
            Map.entry("OUTSOURCE_ORDER", "委外加工单"),
            Map.entry("OUTSOURCE_ISSUE", "委外发料单"),
            Map.entry("OUTSOURCE_IN", "委外入库单"),
            Map.entry("OUTSOURCE_FEE", "委外加工费用单"),
            Map.entry("PU_REQ", "请购单"),
            Map.entry("PU_ORDER", "采购订单"),
            Map.entry("ARRIVAL_IN", "到货单"),
            Map.entry("INSPECTION", "检验单"),
            Map.entry("PURCHASE_IN", "采购入库单"),
            Map.entry("PURCHASE_RETURN", "采购退货单"),
            Map.entry("MANU_ORDER", "生产加工单"),
            Map.entry("DISPATCH", "工单排产"),
            Map.entry("PROCESS_REPORT", "工序汇报单"),
            Map.entry("PROCESS_INSPECT_APPLY", "工序报检单"),
            Map.entry("PROCESS_INSPECTION", "生产过程检验单"),
            Map.entry("FIRST_INSPECT", "首件报检单"),
            Map.entry("MATERIAL_REQ", "领料申请单"),
            Map.entry("TRANSFER", "调拨单"),
            Map.entry("MATERIAL_OUT", "材料出库单"),
            Map.entry("FINISH_INSPECT", "成品报检单"),
            Map.entry("FINISH_IN", "产成品入库单"),
            Map.entry("SALE_OUT", "销售出库单"),
            Map.entry("SALE_INV", "销货单"),
            Map.entry("SALE_INVOICE", "销售发票"),
            Map.entry("SALE_RETURN", "销售退货单"),
            Map.entry("RECEIPT", "收款单"),
            Map.entry("RECEIPT_PAYABLE", "付款单"));

    /**
     * 追踪入口：从 seedNo 出发，正反向找全相关单据并判定状态。
     * 找不到任何单据时返回空 steps + 提示，不抛异常。
     *
     * @param needsProduction 该单是否需要生产（由库存判定，见 ProcessTraceQueryService）
     */
    public TraceResult trace(String seedNo, List<Doc> docs, boolean needsProduction) {
        String seed = seedNo == null ? "" : seedNo.trim();
        if (seed.isEmpty() || docs == null || docs.isEmpty()) {
            return new TraceResult(List.of(), List.of(), List.of(), "", "", "", "请输入单号");
        }

        Map<String, Doc> byNo = new LinkedHashMap<>();
        for (Doc d : docs) {
            if (d.docNo() != null && !d.docNo().isBlank()) byNo.putIfAbsent(d.docNo(), d);
        }
        if (!byNo.containsKey(seed)) {
            return new TraceResult(List.of(), List.of(), List.of(), "", "", "",
                    "未找到单号 " + seed + "，请确认是否为本系统单据");
        }

        // 反向索引：被谁引用。用于从下游单号向上找源单。
        Map<String, Set<String>> referrers = new LinkedHashMap<>();
        for (Doc d : docs) {
            for (String link : d.links()) {
                if (link == null || link.isBlank()) continue;
                referrers.computeIfAbsent(link, k -> new LinkedHashSet<>()).add(d.docNo());
            }
        }

        // BFS 双向扩散，visited 去重保证环不导致死循环
        Set<String> visited = new LinkedHashSet<>();
        Deque<String> queue = new ArrayDeque<>();
        visited.add(seed);
        queue.add(seed);
        while (!queue.isEmpty()) {
            String no = queue.poll();
            Doc d = byNo.get(no);
            if (d == null) continue;
            for (String link : d.links()) {
                if (link == null || link.isBlank()) continue;
                if (visited.add(link)) queue.add(link);
            }
            for (String child : referrers.getOrDefault(no, Set.of())) {
                if (visited.add(child)) queue.add(child);
            }
        }

        List<Step> steps = new ArrayList<>();
        for (String no : visited) {
            Doc d = byNo.get(no);
            if (d == null) continue;
            steps.add(new Step(d.panelCode(), panelName(d.panelCode()), no, d.status(),
                    stateOf(d.status()), d.processCode(), d.processName(), 0));
        }
        // 委外入口先确定「被追踪的是哪张委外加工单」，用于圈定追踪范围与首检分支
        Doc seedDoc = byNo.get(seed);
        String ooNo = seedDoc != null && String.valueOf(seedDoc.panelCode()).startsWith("OUTSOURCE")
                ? outsourceRootNo(seed, byNo) : "";
        boolean outsource = !ooNo.isEmpty();

        // 按业务顺序排，同顺序按单号稳定排序
        steps.sort(Comparator
                .comparingInt((Step s) -> PANEL_ORDER.getOrDefault(s.panelCode(), 999))
                .thenComparing(Step::docNo));
        if (outsource) {
            final String root = ooNo;
            steps.removeIf(s -> !onOutsourceChain(s, root, byNo));
        }
        List<Step> numbered = new ArrayList<>();
        for (int i = 0; i < steps.size(); i++) {
            Step s = steps.get(i);
            numbered.add(new Step(s.panelCode(), s.panelName(), s.docNo(), s.status(), s.state(),
                    s.processCode(), s.processName(), i + 1));
        }

        String current = numbered.isEmpty() ? "" : numbered.get(numbered.size() - 1).docNo();
        Stalled stalled = detectStalled(numbered);
        boolean production = needsProduction || numbered.stream()
                .anyMatch(s -> "MANU_ORDER".equals(s.panelCode()));
        // 委外入口：从委外加工单或它的发料/入库/费用单进来，一律按委外路线铺。
        // 同一张销售订单可能挂多张委外加工单（还有自制行），只跟被追踪那张加工单自己的链，
        // 否则一次追踪会把兄弟单和它们的首检全部拉进来（实测 33 个环节）。
        boolean purchase = !outsource && isPurchaseSeed(seedDoc) && numbered.stream()
                .anyMatch(s -> "PU_ORDER".equals(s.panelCode()) || "PU_REQ".equals(s.panelCode()));
        List<String> routeCodes = outsource ? OUTSOURCE_ROUTE
                : purchase ? PURCHASE_ROUTE
                : (production ? PRODUCTION_ROUTE : DIRECT_ROUTE);
        List<RouteNode> route = buildRoute(numbered, byNo, seed, routeCodes,
                outsource ? routeIndex(OUTSOURCE_ROUTE) : null, ooNo);
        List<Lane> lanes = buildLanes(numbered, byNo, production && !outsource);
        return new TraceResult(numbered, route, lanes, stalled.docNo(), stalled.reason(), current,
                numbered.size() + " 个环节");
    }

    /** 不需要库存判定时的入口（路线按实际单据推断）。 */
    public TraceResult trace(String seedNo, List<Doc> docs) {
        return trace(seedNo, docs, false);
    }

    /**
     * 标准路线模板。
     *
     * <p>有库存的销售订单直接发货，不经过生产；没有库存才排产（工单排产生成生产加工单），
     * 依次经过领料、材料出库、首件报检、工序汇报（每道工序一个环节）、成品检验、产成品入库，
     * 再出货开票。排产在加工单之前：排产按订单行生成加工单，生成即已审核。
     */
    private static final List<String> PRODUCTION_ROUTE = List.of(
            "SO_ORDER", "DISPATCH", "MANU_ORDER", "MATERIAL_OUT",
            "PROCESS_REPORT", "FINISH_INSPECT", "FINISH_IN", "SALE_OUT", "SALE_INV", "SALE_INVOICE");

    /**
     * 分支环节：不占主干位置，挂在所属主干环节下面，缺了标「待生成」。
     * 现在只有一种——首件检验单挂在每道工序汇报单下（一工序一次首检，硬门）。
     */
    private static final String BRANCH_OF_REPORT = "FIRST_INSPECT";

    private static final List<String> DIRECT_ROUTE = List.of(
            "SO_ORDER", "SALE_OUT", "SALE_INV", "SALE_INVOICE");

    /**
     * 采购路线：请购单（上游，有就显示）→ 采购订单 → 分批到货 → 到货检验 → 采购入库。
     * 采购单追踪不该出现销售出库/销货/发票——那是销售订单那条线的事。
     */
    private static final List<String> PURCHASE_ROUTE = List.of(
            "PU_REQ", "PU_ORDER", "ARRIVAL_IN", "INSPECTION", "PURCHASE_IN");

    /** 分批单据：一次到货一张、到货检验一张；多张时收成一个节点 + 分叉，不排一长串同名卡片。 */
    private static final Set<String> BATCH_PANELS = Set.of("ARRIVAL_IN", "INSPECTION");

    /**
     * 委外路线：销售订单（选单带出）→ 委外加工单 → 发料 → 回厂首检 → 委外入库 → 委外加工费用。
     * 首件报检单在委外是按工艺路线一道工序一张（一工序一单），所以直接作为路线上的环节展开。
     */
    private static final List<String> OUTSOURCE_ROUTE = List.of(
            "SO_ORDER", "OUTSOURCE_ORDER", "OUTSOURCE_ISSUE", "OUTSOURCE_IN", "OUTSOURCE_FEE");

    /**
     * 委外单追踪时剔除的自制线单据：同一张销售订单可能既有自制行也有委外行，
     * 自制那条线（排产/加工单/领料/报工/成品报检/产成品入库）不属于委外链路，混进来会让人以为委外还要排产。
     */
    private static final Set<String> SELF_MADE_PANELS = Set.of(
            "DISPATCH", "MANU_ORDER", "MATERIAL_REQ", "MATERIAL_OUT", "PROCESS_REPORT",
            "PROCESS_INSPECT_APPLY", "PROCESS_INSPECTION", "FINISH_INSPECT", "FINISH_IN");

    private static final String PENDING = "待生成";

    /** 采购入口：采购侧单据进来按采购路线铺。 */
    private static boolean isPurchaseSeed(Doc seed) {
        if (seed == null) return false;
        return switch (String.valueOf(seed.panelCode())) {
            case "PU_REQ", "PU_ORDER", "ARRIVAL_IN", "INSPECTION", "PURCHASE_IN", "PURCHASE_RETURN" -> true;
            default -> false;
        };
    }

    /** 委外入口：从发料/入库/费用单进来时，顺着来源找到它所属的委外加工单。 */
    private static String outsourceRootNo(String seed, Map<String, Doc> byNo) {
        Doc d = byNo.get(seed);
        if (d == null) return "";
        if ("OUTSOURCE_ORDER".equals(d.panelCode())) return seed;
        for (String link : d.links()) {
            Doc up = byNo.get(link);
            if (up != null && "OUTSOURCE_ORDER".equals(up.panelCode())) return link;
        }
        // 兜底：拿第一张委外加工单，至少不让路线退化成生产模板
        for (Doc other : byNo.values()) {
            if ("OUTSOURCE_ORDER".equals(other.panelCode())) return other.docNo();
        }
        return "";
    }

    /** 该环节是否属于被追踪的那张委外加工单：销售订单（上游）、加工单本身、或来源单号指向它的单据。 */
    private static boolean onOutsourceChain(Step s, String ooNo, Map<String, Doc> byNo) {
        if (ooNo == null || ooNo.isBlank()) return true;
        if ("SO_ORDER".equals(s.panelCode())) return true;
        if ("OUTSOURCE_ORDER".equals(s.panelCode())) return ooNo.equals(s.docNo());
        Doc d = byNo.get(s.docNo());
        return d != null && d.links().contains(ooNo);
    }

    /** 委外回厂首检单：按工序挂在委外入库节点下（一工序一张）。 */
    private static List<RouteNode> outsourceInspectBranches(List<Step> steps, Map<String, Doc> byNo,
                                                            String ooNo, Set<String> used) {
        List<RouteNode> out = new ArrayList<>();
        for (Step s : steps) {
            if (!"FIRST_INSPECT".equals(s.panelCode())) continue;
            Doc d = byNo.get(s.docNo());
            if (d == null || !d.links().contains(ooNo)) continue;
            if (!used.add(s.docNo())) continue;
            out.add(new RouteNode("FIRST_INSPECT", s.panelName(), s.processCode(), s.processName(),
                    s.docNo(), s.status(), s.state(), true, 0, List.of()));
        }
        return out;
    }

    /** 路线模板的环节下标（委外路线排序用）。 */
    private static Map<String, Integer> routeIndex(List<String> routeCodes) {
        Map<String, Integer> m = new LinkedHashMap<>();
        for (int i = 0; i < routeCodes.size(); i++) m.put(routeCodes.get(i), i);
        return m;
    }

    private static int orderOf(String panelCode) {
        return PANEL_ORDER.getOrDefault(panelCode, 999);
    }

    /**
     * 按模板铺出标准路线：模板里的每个环节，有实际单据就带单号，没有就标「待生成」。
     * 模板之外的关联单据（调拨、过程检验等）仍按业务顺序插回去，不隐藏真实发生的单据。
     */
    private List<RouteNode> buildRoute(List<Step> steps, Map<String, Doc> byNo, String seedNo,
                                       List<String> routeCodes, Map<String, Integer> forcedOrder,
                                       String outsourceNo) {
        Doc seed = byNo.get(seedNo);
        int seedOrder = seed == null ? 0 : orderOf(seed.panelCode());

        Map<String, List<Step>> byPanel = new LinkedHashMap<>();
        for (Step s : steps) byPanel.computeIfAbsent(s.panelCode(), k -> new ArrayList<>()).add(s);

        // 加工单上的工序清单：决定工序汇报单要展开成几个环节
        List<Process> processes = new ArrayList<>();
        String moNo = "";
        Set<String> seenProcess = new LinkedHashSet<>();
        for (Step s : byPanel.getOrDefault("MANU_ORDER", List.of())) {
            Doc mo = byNo.get(s.docNo());
            if (mo == null) continue;
            if (moNo.isEmpty()) moNo = s.docNo();
            // 多张加工单（多行订单）会有同名工序：主干是汇总视角，按工序编码去重，
            // 否则第二张单的「车削」会再生成一个空的「待生成」节点
            for (Process p : mo.processes()) {
                if (seenProcess.add(p.code())) processes.add(p);
            }
        }

        // 首件检验单：一工序一行，按「加工单 + 工序编码」挂到对应工序汇报下
        Map<String, List<Step>> inspections = new LinkedHashMap<>();
        for (Step s : byPanel.getOrDefault(BRANCH_OF_REPORT, List.of())) {
            Doc doc = byNo.get(s.docNo());
            if (doc == null) continue;
            String inspectMo = moOf(doc, byNo, moNo);
            for (Process p : doc.processes()) {
                inspections.computeIfAbsent(inspectMo + "|" + p.code(), k -> new ArrayList<>()).add(s);
            }
        }
        // 每张工序汇报单属于哪张加工单：分支要按加工单匹配，避免多张加工单串单
        Map<String, String> reportMo = new LinkedHashMap<>();
        for (Step s : byPanel.getOrDefault("PROCESS_REPORT", List.of())) {
            Doc doc = byNo.get(s.docNo());
            reportMo.put(s.docNo(), doc == null ? moNo : moOf(doc, byNo, moNo));
        }

        // 哪些工序已经报过工：排产分支据此区分「待排产」与「无批次报工」
        Set<String> reportedProcesses = new LinkedHashSet<>();
        for (Step s : byPanel.getOrDefault("PROCESS_REPORT", List.of())) {
            if (s.processCode() != null && !s.processCode().isBlank()) reportedProcesses.add(s.processCode());
        }
        Set<String> used = new LinkedHashSet<>();
        List<RouteNode> nodes = new ArrayList<>();
        for (String code : routeCodes) {
            // 工序排产与工序汇报是一工序一张单：按加工单工序逐条展开
            if ("PROCESS_REPORT".equals(code) || "DISPATCH".equals(code)) {
                nodes.addAll(processNodes(code, byPanel.getOrDefault(code, List.of()), processes,
                        seedOrder <= orderOf(code), used,
                        "PROCESS_REPORT".equals(code) ? inspections : null,
                        moNo, reportMo, reportedProcesses));
                continue;
            }
            List<Step> hits = byPanel.getOrDefault(code, List.of());
            if (hits.isEmpty()) {
                // 比入口单据更上游的环节不补：从加工单追踪时不该出现「待生成 销售订单」
                if (seedOrder <= orderOf(code)) {
                    // 采购入库已经有了、却没有检验单 → 到货行免检直入，这一环写「免检直入」而不是「待生成」
                    String status = "INSPECTION".equals(code)
                            && !byPanel.getOrDefault("PURCHASE_IN", List.of()).isEmpty() ? "免检直入" : PENDING;
                    nodes.add(pendingNode(code, "", "", status));
                }
                continue;
            }
            // 采购分批：多张到货单/检验单收成一个节点 + 分叉
            if (BATCH_PANELS.contains(code) && hits.size() > 1) {
                Step first = hits.get(0);
                List<RouteNode> branches = new ArrayList<>();
                for (int i = 1; i < hits.size(); i++) {
                    branches.add(presentNode(hits.get(i)));
                    used.add(hits.get(i).docNo());
                }
                RouteNode base = presentNode(first);
                nodes.add(new RouteNode(base.panelCode(), base.panelName(), base.processCode(),
                        base.processName(), base.docNo(), base.status(), base.state(), true, 0, branches));
                used.add(first.docNo());
                continue;
            }
            for (Step s : hits) {
                // 委外：首件报检单（一工序一张）挂到委外入库节点下当分支，不再各占一格
                List<RouteNode> branches = "OUTSOURCE_IN".equals(code) && !outsourceNo.isBlank()
                        ? outsourceInspectBranches(steps, byNo, outsourceNo, used)
                        : List.of();
                RouteNode base = presentNode(s);
                nodes.add(branches.isEmpty() ? base
                        : new RouteNode(base.panelCode(), base.panelName(), base.processCode(),
                        base.processName(), base.docNo(), base.status(), base.state(), true, 0, branches));
                used.add(s.docNo());
            }
        }
        // 模板外的关联单据照常展示；领料申请单不再进路线（现场领料直接在「生产领料」这一步出库）
        for (Step s : steps) {
            if ("MATERIAL_REQ".equals(s.panelCode())) continue;
            if (used.add(s.docNo())) nodes.add(presentNode(s));
        }
        // 稳定排序：保留模板顺序，同时把额外单据插到正确的业务位置。
        // 委外路线用模板下标（PANEL_ORDER 是生产链口径，会把首件报检排到入库之后）。
        nodes.sort(Comparator.comparingInt((RouteNode n) -> forcedOrder == null
                ? orderOf(n.panelCode())
                : forcedOrder.getOrDefault(n.panelCode(), 100 + orderOf(n.panelCode()))));
        List<RouteNode> numbered = new ArrayList<>();
        for (int i = 0; i < nodes.size(); i++) {
            RouteNode n = nodes.get(i);
            numbered.add(new RouteNode(n.panelCode(), n.panelName(), n.processCode(), n.processName(),
                    n.docNo(), n.status(), n.state(), n.present(), i + 1, n.branches()));
        }
        return numbered;
    }

    /**
     * 并行排产线：整条流程按「加工单（= 销售订单行）」拆成若干条并行线。
     *
     * <pre>
     *   销售订单 ─┬─ 工单排产 SO-…-1 ─ 生产加工单 SO-…-1 ─ 领料 ─ 出库 ─ 汇报 ─ 报检 ─ 入库
     *             ├─ 工单排产 SO-…-2 ─ 生产加工单 SO-…-2 ─ …
     *             └─ …                                                        → 销售出库 → 销货 → 发票
     * </pre>
     *
     * 排产节点按加工单聚成**一个**：一次排产就把该产品的全部工序排定，只是后道还没开工，
     * 所以工序以分支形式挂在排产节点下（已排的带批次单号，未开工的标「待生成」），
     * 不再一道工序拆一个节点。
     */
    private List<Lane> buildLanes(List<Step> steps, Map<String, Doc> byNo, boolean production) {
        if (!production) return List.of();
        Map<String, List<Step>> byPanel = new LinkedHashMap<>();
        for (Step s : steps) byPanel.computeIfAbsent(s.panelCode(), k -> new ArrayList<>()).add(s);
        List<Step> moSteps = byPanel.getOrDefault("MANU_ORDER", List.of());
        if (moSteps.isEmpty()) return List.of();

        Set<String> moNos = new LinkedHashSet<>();
        for (Step s : moSteps) moNos.add(s.docNo());
        List<Step> allBatches = byPanel.getOrDefault("DISPATCH", List.of());

        List<Lane> lanes = new ArrayList<>();
        int index = 0;
        for (Step moStep : moSteps) {
            index++;
            String moNo = moStep.docNo();
            Doc moDoc = byNo.get(moNo);
            List<Process> processes = moDoc == null ? List.of() : moDoc.processes();

            List<RouteNode> nodes = new ArrayList<>();
            // 排产：本加工单的全部批次聚成一个节点，工序挂分支
            List<Step> batches = new ArrayList<>();
            for (Step b : allBatches) {
                Doc bd = byNo.get(b.docNo());
                if (bd != null && moNo.equals(moOf(bd, byNo, moNos, moNo))) batches.add(b);
            }
            // 先算「哪些工序已经报过工」，排产节点要靠它区分「待排产」与「无批次报工」
            // 同一工序可能有多张汇报单（分批报工）：全部保留，已审核的排前面
            Map<String, List<Step>> reportsByProcess = new LinkedHashMap<>();
            List<Step> reports = hitsOf(byPanel.getOrDefault("PROCESS_REPORT", List.of()), byNo, moNos, moNo);
            for (Step r : reports) {
                if (r.processCode() == null || r.processCode().isBlank()) continue;
                reportsByProcess.computeIfAbsent(r.processCode(), k -> new ArrayList<>()).add(r);
            }
            for (List<Step> list : reportsByProcess.values()) {
                list.sort(Comparator
                        .comparingInt((Step r) -> "已审核".equals(r.status()) ? 0 : 1)
                        .thenComparing(Step::docNo));
            }
            nodes.add(planNode(processes, batches, byNo, reportsByProcess.keySet()));
            nodes.add(presentNode(moStep));

            // 领料申请单不再作为环节（现场领料直接在「生产领料」这一步出材料出库单）
            for (String code : List.of("MATERIAL_OUT")) {
                List<Step> hits = hitsOf(byPanel.getOrDefault(code, List.of()), byNo, moNos, moNo);
                if (hits.isEmpty()) nodes.add(pendingNode(code, "", ""));
                else for (Step h : hits) nodes.add(presentNode(h));
            }
            // 排产批次：按工序落批次（A 方案下报工必须先有批次），挂在工序分支的第一个位置
            Map<String, Step> batchByProcess = new LinkedHashMap<>();
            for (Step s : hitsOf(byPanel.getOrDefault("DISPATCH", List.of()), byNo, moNos, moNo)) {
                if (s.processCode() == null || s.processCode().isBlank()) continue;
                if ("已审核".equals(s.status())) batchByProcess.put(s.processCode(), s);
                else batchByProcess.putIfAbsent(s.processCode(), s);
            }
            Map<String, List<Step>> inspections = new LinkedHashMap<>();
            List<Step> inspectDocs = hitsOf(byPanel.getOrDefault(BRANCH_OF_REPORT, List.of()), byNo, moNos, moNo);
            for (Step s : inspectDocs) {
                // 作废的首检单是历史痕迹（返工后重检会作废旧单），画在分支上只会让人以为有多张在跑
                if ("作废".equals(s.status())) continue;
                Doc doc = byNo.get(s.docNo());
                if (doc == null) continue;
                for (Process p : doc.processes()) {
                    inspections.computeIfAbsent(p.code(), k -> new ArrayList<>()).add(s);
                }
            }
            // 工序汇报按加工单**收成一格**：单据多了逐道展开会把流程拉得没法看，
            // 所以这张加工单的汇报单挂在同一个节点下，逐道工序作为分支（已报带单号，未报写生产中/待生产）。
            List<RouteNode> reportBranches = new ArrayList<>();
            boolean prevReported = true;
            Step firstReport = null;
            int doneReports = 0;
            for (Process p : processes) {
                List<Step> hits = reportsByProcess.getOrDefault(p.code(), List.of());
                List<RouteNode> branchBranches = new ArrayList<>();
                Step batch = batchByProcess.get(p.code());
                branchBranches.add(batch != null
                        ? presentNode(batch)
                        : missingBatchBranch(p, reportsByProcess.keySet()));
                for (Step s : inspections.getOrDefault(p.code(), List.of())) branchBranches.add(presentNode(s));
                // 批次标签已经占了一个位置，这里要按「有没有首检标签」判断，不能看列表是否为空
                boolean hasInspect = branchBranches.stream()
                        .anyMatch(n -> BRANCH_OF_REPORT.equals(n.panelCode()));
                if (!hasInspect) {
                    branchBranches.add(pendingNode(BRANCH_OF_REPORT, p.code(), p.name()));
                }
                if (!hits.isEmpty()) {
                    // 分批报工：一张汇报单一枚分支，全部列出来
                    boolean first = true;
                    for (Step hit : hits) {
                        reportBranches.add(new RouteNode(hit.panelCode(), hit.panelName(), p.code(), p.name(),
                                hit.docNo(), hit.status(), hit.state(), true, 0,
                                first ? branchBranches : List.of()));
                        first = false;
                    }
                    if (firstReport == null) firstReport = hits.get(0);
                    if (hits.stream().anyMatch(r -> "已审核".equals(r.status()))) doneReports++;
                    prevReported = hits.stream().anyMatch(r -> "已审核".equals(r.status()));
                } else {
                    reportBranches.add(processPendingNode("PROCESS_REPORT", p, prevReported, branchBranches));
                    prevReported = false;
                }
            }
            if (!processes.isEmpty()) {
                int total = processes.size();
                boolean allReported = doneReports == total;
                boolean anyReported = firstReport != null;
                String reportStatus;
                String reportState;
                if (allReported) {
                    reportStatus = "全部报工完成";
                    reportState = "完成";
                } else if (anyReported) {
                    reportStatus = "部分报工";
                    reportState = "进行中";
                } else {
                    reportStatus = "生产中";
                    reportState = "进行中";
                }
                nodes.add(new RouteNode("PROCESS_REPORT", panelName("PROCESS_REPORT"), "", "",
                        firstReport == null ? "" : firstReport.docNo(), reportStatus, reportState,
                        anyReported, 0, reportBranches));
            }

            for (String code : List.of("FINISH_INSPECT", "FINISH_IN")) {
                List<Step> hits = hitsOf(byPanel.getOrDefault(code, List.of()), byNo, moNos, moNo);
                if (hits.isEmpty()) nodes.add(pendingNode(code, "", ""));
                else for (Step h : hits) nodes.add(presentNode(h));
            }

            lanes.add(new Lane("L" + index, moNo, "", "", moStep.docNo(), nodes));
        }
        return lanes;
    }

    /**
     * 本加工单的单据（按加工单号归属过滤）。
     *
     * <p>这里不能用「解析不到就用当前加工单」的兜底：同一张销售订单可能既有自制行也有委外行，
     * 委外的首件报检单指向的是委外加工单（不是 MANU_ORDER），兜底会把它算进每条自制线——
     * 结果每道工序下面都挂着一串别的加工单的首检单（2026-11-12 实测：车削/钻孔/抛光各挂了 5 张）。
     */
    private List<Step> hitsOf(List<Step> steps, Map<String, Doc> byNo, Set<String> moNos, String moNo) {
        List<Step> out = new ArrayList<>();
        for (Step s : steps) {
            Doc d = byNo.get(s.docNo());
            if (d == null) continue;
            String own = ownMoOf(d, byNo, moNos);
            if (own.startsWith(FOREIGN_MO)) continue;      // 明确属于委外/别的链
            if (!own.isEmpty()) {
                if (own.equals(moNo)) out.add(s);
                continue;
            }
            // 完全没有加工单引用：轨迹里只有一张加工单时才认它，多线时不猜
            if (moNos.size() == 1) out.add(s);
        }
        return out;
    }

    /** {@link #ownMoOf} 的哨兵：该单据引用了另一条链的加工单（委外）。 */
    private static final String FOREIGN_MO = "!";

    /**
     * 单据自己声明的加工单号：先按本次追踪到的加工单集合认，再看引用文档是不是加工单/委外加工单。
     * 引用的是委外加工单时返回 {@code "!<单号>"}，表示它不属于自制线。
     */
    private static String ownMoOf(Doc doc, Map<String, Doc> byNo, Set<String> moNos) {
        for (String link : doc.links()) {
            if (moNos.contains(link)) return link;
        }
        for (String link : doc.links()) {
            Doc ref = byNo.get(link);
            if (ref == null) continue;
            if ("MANU_ORDER".equals(ref.panelCode())) return link;
            if ("OUTSOURCE_ORDER".equals(ref.panelCode())) return FOREIGN_MO + link;
        }
        for (String link : doc.links()) {
            if (link.startsWith("MO-")) return link;
        }
        return "";
    }

    /**
     * 还没报工的工序：轮到它了是「生产中」，前道还没报完是「待生产」。
     *
     * <p>工序节点不是「少一张单据」，而是生产进度不同，所以状态写生产口径，不写「待生成」。
     */
    private static RouteNode processPendingNode(String panelCode, Process p, boolean ready,
                                                List<RouteNode> branches) {
        return new RouteNode(panelCode, panelName(panelCode), p.code(), p.name(), "",
                ready ? "生产中" : "待生产", ready ? "进行中" : "待开始", false, 0, branches);
    }

    /** 排产节点：一次排产覆盖该产品全部工序，工序作为分支挂在下面。 */
    private RouteNode planNode(List<Process> processes, List<Step> batches, Map<String, Doc> byNo,
                               Set<String> reportedProcesses) {
        if (batches.isEmpty()) return pendingNode("DISPATCH", "", "");
        // 工序 → 该工序的全部排产批次（一道工序可以分批投产，不能只取第一条）
        Map<String, List<Step>> byProcess = new LinkedHashMap<>();
        for (Step b : batches) {
            Doc d = byNo.get(b.docNo());
            if (d == null) continue;
            String code = d.processCode();
            if (code == null || code.isBlank()) continue;
            byProcess.computeIfAbsent(code, k -> new ArrayList<>()).add(b);
        }
        List<RouteNode> branches = new ArrayList<>();
        if (processes.isEmpty()) {
            for (Step b : batches) branches.add(presentNode(b));
        } else {
            for (Process p : processes) {
                List<Step> hits = byProcess.getOrDefault(p.code(), List.of());
                if (hits.isEmpty()) {
                    branches.add(missingBatchBranch(p, reportedProcesses));
                    continue;
                }
                hits.sort(Comparator.comparing(Step::docNo));
                for (Step b : hits) {
                    String status = "中止".equals(b.status()) ? "已中止"
                            : "已审核".equals(b.status()) ? "已排产" : b.status();
                    branches.add(new RouteNode("DISPATCH", panelName("DISPATCH"), p.code(), p.name(),
                            b.docNo(), status, stateOf(b.status()), true, 0, List.of()));
                }
            }
        }
        Step first = batches.get(0);
        // 中止的批次不再参与排产，但也算「处置完毕」，不该让整条线看着像没排完
        boolean allSettled = batches.stream()
                .allMatch(b -> "已审核".equals(b.status()) || "中止".equals(b.status()));
        return new RouteNode("DISPATCH", panelName("DISPATCH"), "", "", first.docNo(),
                allSettled ? "已排产" : "部分排产", allSettled ? "完成" : "进行中", true, 0, branches);
    }

    /**
     * 工序没有排产批次时的分支：
     * 已经报过工的说明是老路径（加工单直接报工、没落批次）干的活，不能写「待排产」；
     * 真没干过的才写「待排产」。
     */
    private RouteNode missingBatchBranch(Process p, Set<String> reportedProcesses) {
        boolean reported = reportedProcesses != null && reportedProcesses.contains(p.code());
        return new RouteNode("DISPATCH", panelName("DISPATCH"), p.code(), p.name(),
                "", reported ? "无批次报工" : "待排产", reported ? "完成" : "待开始", reported, 0, List.of());
    }

    /**
     * 一工序一张单的环节（工序派工单 / 工序汇报单）：按加工单工序逐条展开，缺的标「待生成」。
     * 工序汇报单还要挂上首件检验分支：有单带单号，没检标「待生成」。
     */
    private List<RouteNode> processNodes(String panelCode, List<Step> hits, List<Process> processes,
                                         boolean includePending, Set<String> used,
                                         Map<String, List<Step>> inspections, String routeMo,
                                         Map<String, String> reportMo, Set<String> reportedProcesses) {
        List<RouteNode> out = new ArrayList<>();
        List<Step> pool = new ArrayList<>(hits);
        for (Process p : processes) {
            // 同一道工序可能排了多个批次（一批一张派工单、一张汇报单），全部展开，不能只取第一张
            List<Step> matched = new ArrayList<>();
            for (Iterator<Step> it = pool.iterator(); it.hasNext(); ) {
                Step s = it.next();
                boolean byCode = !p.code().isEmpty() && p.code().equals(s.processCode());
                boolean byName = !p.name().isEmpty() && p.name().equals(s.processName());
                if (byCode || byName) {
                    matched.add(s);
                    it.remove();
                }
            }
            if (matched.isEmpty()) {
                if (includePending) {
                    RouteNode pending = "DISPATCH".equals(panelCode)
                            ? missingBatchBranch(p, reportedProcesses)
                            : pendingNode(panelCode, p.code(), p.name());
                    out.add(withBranches(pending, p, inspections, includePending, used, routeMo, reportMo));
                }
                continue;
            }
            matched.sort(Comparator.comparing(Step::docNo));
            for (Step s : matched) {
                used.add(s.docNo());
                out.add(withBranches(presentNode(s), p, inspections, includePending, used, routeMo, reportMo));
            }
        }
        // 工序对不上档案、或加工单没有工序行时，剩余单据照常展示
        for (Step s : pool) {
            out.add(withBranches(presentNode(s), null, inspections, includePending, used, routeMo, reportMo));
        }
        if (out.isEmpty() && includePending) {
            out.add(withBranches(pendingNode(panelCode, "", ""), null, inspections, includePending, used,
                    routeMo, reportMo));
        }
        return out;
    }

    /** 给工序汇报节点挂首件检验分支；分支命中的单据从「额外单据」里取掉，避免重复出现。 */
    private RouteNode withBranches(RouteNode node, Process process,
                                   Map<String, List<Step>> inspections, boolean includePending,
                                   Set<String> used, String routeMo, Map<String, String> reportMo) {
        if (inspections == null) return node;
        String mo = reportMo.getOrDefault(node.docNo(), routeMo);
        List<RouteNode> branches = new ArrayList<>();
        List<Step> hits = process == null
                ? List.of()
                : inspections.getOrDefault(mo + "|" + process.code(), List.of());
        for (Step s : hits) {
            branches.add(presentNode(s));
            used.add(s.docNo());
        }
        if (branches.isEmpty() && includePending) {
            branches.add(pendingNode(BRANCH_OF_REPORT, node.processCode(), node.processName()));
        }
        return new RouteNode(node.panelCode(), node.panelName(), node.processCode(), node.processName(),
                node.docNo(), node.status(), node.state(), node.present(), node.seq(), branches);
    }

    private static RouteNode presentNode(Step s) {
        return new RouteNode(s.panelCode(), s.panelName(), "", s.processName(), s.docNo(),
                s.status(), s.state(), true, 0, List.of());
    }

    /** 单据属于哪张加工单：优先取它引用的加工单号，取不到用路线里的那张。 */
    private static String moOf(Doc doc, Map<String, Doc> byNo, String fallback) {
        for (String link : doc.links()) {
            Doc ref = byNo.get(link);
            if (ref != null && "MANU_ORDER".equals(ref.panelCode())) return link;
        }
        for (String link : doc.links()) {
            if (link.startsWith("MO-")) return link;
        }
        return fallback;
    }

    /**
     * 同 {@link #moOf(Doc, Map, String)}，但按已知加工单号认单。
     *
     * <p>加工单号现在由销售订单排产生成，形如 {@code SO-20261110-001-1}，不再以 MO- 开头，
     * 光靠前缀认不出来，所以传一份当前追踪到的加工单号集合。
     */
    private static String moOf(Doc doc, Map<String, Doc> byNo, Set<String> moNos, String fallback) {
        for (String link : doc.links()) {
            if (moNos.contains(link)) return link;
            Doc ref = byNo.get(link);
            if (ref != null && "MANU_ORDER".equals(ref.panelCode())) return link;
        }
        for (String link : doc.links()) {
            if (link.startsWith("MO-")) return link;
        }
        return fallback;
    }

    private static RouteNode pendingNode(String panelCode, String processCode, String processName) {
        return pendingNode(panelCode, processCode, processName, PENDING);
    }

    /** 待生成但状态要写别的字（如「免检直入」：这一环没有单据，但流程确实走过了）。 */
    private static RouteNode pendingNode(String panelCode, String processCode, String processName,
                                         String status) {
        return new RouteNode(panelCode, panelName(panelCode), processCode, processName, "",
                status, PENDING, false, 0, List.of());
    }

    /** 状态 → 阶段：已审核类算完成，审批中/草稿/生产中算进行中。 */
    private static String stateOf(String status) {
        String s = status == null ? "" : status.trim();
        if (s.isEmpty()) return "待开始";
        return switch (s) {
            case "已审核", "已完工", "已关闭", "已核销" -> "完成";
            case "审批中", "草稿", "生产中", "部分核销" -> "进行中";
            case "中止", "已中止", "已驳回", "作废" -> "已中止";
            default -> "进行中";
        };
    }

    /** 卡点：最靠下游的「进行中」单据；全部完成则无卡点。 */
    private record Stalled(String docNo, String reason) {}

    private static Stalled detectStalled(List<Step> steps) {
        for (int i = steps.size() - 1; i >= 0; i--) {
            Step s = steps.get(i);
            // 作废单是历史痕迹，不算流程中断（它已经被后来重新开的单据取代了）
            if ("作废".equals(s.status())) continue;
            // 排产批次中止 = 主动调产能（未执行量已放回可排产量），不是流程卡住
            if ("中止".equals(s.status()) && "DISPATCH".equals(s.panelCode())) continue;
            if ("已中止".equals(s.state())) {
                return new Stalled(s.docNo(), s.panelName() + " " + s.docNo() + " 已中止，流程中断");
            }
            if ("进行中".equals(s.state())) {
                String reason = statusReason(s);
                return new Stalled(s.docNo(), s.panelName() + " " + s.docNo() + " 停在「"
                        + s.status() + "」，" + reason);
            }
        }
        return new Stalled("", "");
    }

    /** 把状态翻译成业务上该做什么，直接给操作员看。 */
    private static String statusReason(Step step) {
        String status = step.status();
        if ("草稿".equals(status)) return "需要提交审核";
        if ("审批中".equals(status)) return "等待审批通过";
        if ("生产中".equals(status)) return "车间尚未完工";
        if ("部分核销".equals(status)) return "尚有未核销金额";
        return "需要推进到已审核";
    }

    private static String panelName(String panelCode) {
        if (panelCode == null) return "";
        return PANEL_NAMES.getOrDefault(panelCode, panelCode);
    }
}
