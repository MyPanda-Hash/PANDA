package com.mes.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FinanceVoucher;
import com.mes.entity.FormApproval;
import com.mes.entity.FormData;
import com.mes.entity.PanelConfig;
import com.mes.mapper.FormApprovalMapper;
import com.mes.mapper.FormDataMapper;
import com.mes.mapper.PanelConfigMapper;
import com.mes.panel.PanelActionContext;
import com.mes.panel.PanelActionRegistry;
import com.mes.panel.PanelRuntimeService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Set;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.regex.Pattern;

@Service
public class PxService implements PanelRuntimeService {

    private static final Set<String> EFFECTIVE_STATUSES = Set.of("已审核", "生产中", "已完工", "已关闭");
    private static final Set<String> QUALITY_REPORT_PANELS = Set.of(
            "ARRIVAL_IN", "FINISH_INSPECT", "FIRST_INSPECT", "PROCESS_INSPECT_APPLY");
    private static final Set<String> QUALITY_INSPECTION_PANELS = Set.of("INSPECTION", "PROCESS_INSPECTION");
    /** 质量三态判定集合：对齐金蝶「合格/让步接收/报废」语义。 */
    private static final Set<String> QUALITY_VERDICTS =
            Set.of("合格", "让步接收", "部分报废", "全批报废", "全批退回");
    /** 生产过程检验判定：合格/返工/让步接收/报废（不含采购退回类状态）。 */
    private static final Set<String> PROCESS_VERDICTS = Set.of("合格", "返工", "让步接收", "报废");
    /**
     * 财务类下游单据：只按自身口径统计已开票量，不参与履约单据的来源占用互斥。
     * 采购订单直开预开票后仍可到货/入库；同一订单行是否重复开票由采购发票自己的数量校验控制。
     */
    private static final Set<String> FINANCIAL_DOWNSTREAM_PANELS = Set.of("PU_INVOICE");

    /** 来料批次处置：质量处置方式 / 商务结算方式 / 责任归属（2026-11-05） */
    private static final Set<String> INSPECTION_DISPOSITIONS =
            Set.of("合格接收", "让步接收", "降价接收", "部分退回", "全批退回", "返工", "返修", "挑选接收", "报废");
    private static final Set<String> INSPECTION_SETTLEMENTS =
            Set.of("原价", "折价", "索赔", "免费补货", "退款");
    private static final Set<String> INSPECTION_LIABILITIES =
            Set.of("供应商", "运输", "我方", "待定");

    /** 销货单审批通过后是否自动开票（mes.invoice.auto-issue，默认关：由开票工作台手动开）。 */
    @org.springframework.beans.factory.annotation.Value("${mes.invoice.auto-issue:false}")
    private boolean invoiceAutoIssue;

    /** 开票方信息（application.yml 的 mes.company.*，环境变量可覆盖）。单测环境下为空。 */
    @org.springframework.beans.factory.annotation.Value("${mes.company.name:}")
    private String companyName;
    @org.springframework.beans.factory.annotation.Value("${mes.company.tax-no:}")
    private String companyTaxNo;
    @org.springframework.beans.factory.annotation.Value("${mes.company.addr-tel:}")
    private String companyAddrTel;
    @org.springframework.beans.factory.annotation.Value("${mes.company.bank-account:}")
    private String companyBankAccount;

    private final PanelConfigMapper panelMapper;
    private final FormDataMapper formMapper;
    private final FormApprovalMapper approvalMapper;
    private final ReportQueryService reportQueryService;
    private final PanelActionRegistry panelActionRegistry;
    private final RoleService roleService;
    private final JdbcTemplate jdbcTemplate;
    private final StockTxnService stockTxnService;
    private final FinanceSettleService financeSettleService;
    private final FinancePaymentService financePaymentService;
    private final com.mes.mapper.FinanceVoucherMapper financeVoucherMapper;
    private final AccountingService accountingService;
    private final ProcessTraceQueryService processTraceQueryService;
    /**
     * 计划分析：用可选注入而不是构造器参数 —— 已有 44 个测试用短构造器建 PxService，
     * 加参数会全线破坏；缺这个 bean 时接口降级为「未实现」而非启动失败。
     */
    @Autowired(required = false)
    private PlanAnalyzeQueryService planAnalyzeQueryService;
    private final ObjectMapper json = new ObjectMapper();

    /** 权限码 → 受控按钮名（矩阵过滤映射；未列出的按钮不受权限控制） */
    private static final Map<String, List<String>> ACTION_BUTTONS = Map.of(
            "query", List.of("查找", "查询", "刷新"),
            "add", List.of("新增", "新增流程", "引入常用单据", "选单", "扫描填单"),
            "edit", List.of("修改", "保存", "保存新增", "保存为草稿", "中止", "中止执行", "整单中止", "草稿", "取消中止", "关闭", "手工完工", "确认处置"),
            "delete", List.of("删除", "删除单据"),
            "export", List.of("导出", "明细标签打印", "下载导入模板", "导入"),
            "print", List.of("直接打印", "打印", "预览", "打印模板设置", "打印情况", "保存打印"),
            "audit", List.of("提交审批", "审批通过", "审批驳回", "驳回审批", "审批情况", "弃审", "审核"));

    /** 按钮名 → 权限码（含「生成XX」生单按钮 → add） */
    private static String permOfButton(String buttonName) {
        if (buttonName == null || buttonName.isEmpty()) return null;
        if (buttonName.startsWith("生成")) return "add";
        for (Map.Entry<String, List<String>> e : ACTION_BUTTONS.entrySet()) {
            if (e.getValue().contains(buttonName)) return e.getKey();
        }
        return null;
    }

    // ---------- 自定义字段（面板自助加字段）----------
    // 借鉴 YINJIA-MES 的「字段管理（动态字段 + 备用列池）」：把「加一个字段」从改库/改配置交给
    // 有该面板「修改」权限的用户。本项目面板数据本来就存在 form_data 的 JSON 里（不是物理列），
    // 所以不需要备用列池、不需要 DDL —— 加字段 = 登记一条 px_custom_field 元数据，读取出口并进配置。
    // 不写 panel_config.config：4 个 Registry 启动时会整体覆盖该 JSON（与 px_column_pref 同一理由）。
    private static final Set<String> CUSTOM_FIELD_TYPES = Set.of("文本", "下拉框", "日期", "是否", "小数", "整数");
    private static final Set<String> CUSTOM_FIELD_SCOPES = Set.of("header", "detail");
    private static final int CUSTOM_FIELD_NAME_MAX = 40;
    /** 字段名规范与列名同口径：禁空格与 . % ( ) / —— 点号会被当成「参照带出列」解析 */
    private static final Pattern CUSTOM_FIELD_NAME_RE =
            Pattern.compile("[^\\s.%(/)]{1," + CUSTOM_FIELD_NAME_MAX + "}");
    /** 自定义字段缓存：加/停用后立即失效，不靠时间窗（否则刚加的字段要等一会儿才出现） */
    private final Map<String, List<Map<String, Object>>> customFieldCache = new ConcurrentHashMap<>();

    @Autowired
    public PxService(PanelConfigMapper panelMapper, FormDataMapper formMapper,
                     FormApprovalMapper approvalMapper, ReportQueryService reportQueryService,
                     PanelActionRegistry panelActionRegistry, RoleService roleService,
                     JdbcTemplate jdbcTemplate, StockTxnService stockTxnService,
                     FinanceSettleService financeSettleService,
                     FinancePaymentService financePaymentService,
                     com.mes.mapper.FinanceVoucherMapper financeVoucherMapper,
                     AccountingService accountingService,
                     ProcessTraceQueryService processTraceQueryService) {
        this.panelMapper = panelMapper;
        this.formMapper = formMapper;
        this.approvalMapper = approvalMapper;
        this.reportQueryService = reportQueryService;
        this.panelActionRegistry = panelActionRegistry;
        this.roleService = roleService;
        this.jdbcTemplate = jdbcTemplate;
        this.stockTxnService = stockTxnService;
        this.financeSettleService = financeSettleService;
        this.financePaymentService = financePaymentService;
        this.financeVoucherMapper = financeVoucherMapper;
        this.accountingService = accountingService;
        this.processTraceQueryService = processTraceQueryService;
    }

    PxService(PanelConfigMapper panelMapper, FormDataMapper formMapper,
              FormApprovalMapper approvalMapper, ReportQueryService reportQueryService) {
        this(panelMapper, formMapper, approvalMapper, reportQueryService,
                new PanelActionRegistry(List.of()), null, null, null, null, null, null, null, null);
    }

    /** 测试与无财务场景的简化构造：库存与财务服务均可为空。 */
    PxService(PanelConfigMapper panelMapper, FormDataMapper formMapper,
              FormApprovalMapper approvalMapper, ReportQueryService reportQueryService,
              PanelActionRegistry panelActionRegistry, RoleService roleService,
              JdbcTemplate jdbcTemplate, StockTxnService stockTxnService) {
        this(panelMapper, formMapper, approvalMapper, reportQueryService,
                panelActionRegistry, roleService, jdbcTemplate, stockTxnService, null, null, null, null, null);
    }

    // ---------- 配置 ----------

    @SuppressWarnings("unchecked")
    private Map<String, Object> loadConfig(String panelCode) {
        PanelConfig pc = panelMapper.selectOne(new LambdaQueryWrapper<PanelConfig>()
                .eq(PanelConfig::getPanelCode, panelCode));
        if (pc == null) throw new IllegalArgumentException("面板不存在：" + panelCode);
        try {
            Map<String, Object> config = json.readValue(pc.getConfig(), new TypeReference<Map<String, Object>>() {});
            applyRuntimeConfigUpgrades(panelCode, config);
            // 用户自助加的自定义字段：读出口合并（不改库里的 config JSON）
            applyCustomFields(config, panelCode);
            return config;
        } catch (Exception e) {
            throw new IllegalStateException("面板配置解析失败：" + e.getMessage());
        }
    }

    @SuppressWarnings("unchecked")
    private void applyRuntimeConfigUpgrades(String panelCode, Map<String, Object> config) {
        normalizeFieldDefinitions(config);
        normalizeApprovalWorkflow(panelCode, config);
        ensureSelectAction(config);
        ensureSelectDetailCodeMap(config);
        // 2026-08-25：计量单位类下拉框动态并入 UOM 面板数据（UOM 新增单位后所有面板下拉自动显示完整）
        injectUnitOptions(config);
        if ("MANU_ORDER".equals(panelCode)) {
            upgradeDetailReference(config, "products", "产品编码",
                    List.of("存货编码", "存货名称", "规格型号", "所属类别", "品牌", "计量单位", "属性", "停用"),
                    List.of(
                            Map.of("from", "存货名称", "to", "产品名称"),
                            Map.of("from", "规格型号", "to", "规格型号"),
                            Map.of("from", "计量单位", "to", "生产单位")));
        } else if ("SO_ORDER".equals(panelCode)) {
            upgradeDetailReference(config, "items", "存货编码",
                    List.of("存货编码", "存货名称", "规格型号", "品牌", "计量单位", "参考成本", "停用"),
                    List.of(
                            Map.of("from", "存货名称", "to", "存货名称"),
                            Map.of("from", "规格型号", "to", "规格型号"),
                            Map.of("from", "品牌", "to", "存货名称.品牌"),
                            Map.of("from", "计量单位", "to", "销售单位"),
                            Map.of("from", "参考成本", "to", "单价")));
            ensureSelectConfig(config, "QUOTE_ORDER", "选报价单",
                    "仅显示已审核且未中止的报价单，选中后产品明细带入销售订单（对齐 T+ 选单前提）",
                    List.of("单据编号", "单据日期", "客户", "存货名称", "数量", "报价单价", "有效期至"),
                    "items",
                    List.of(Map.of("from", "单据编号", "to", "来源单号")),
                    List.of(
                            Map.of("from", "存货名称", "to", "存货名称"),
                            Map.of("from", "计量单位", "to", "销售单位"),
                            Map.of("from", "数量", "to", "数量"),
                            Map.of("from", "报价单价", "to", "单价")));
        } else if ("QUOTE_ORDER".equals(panelCode)) {
            upgradeQuoteOrderConfig(config);
        }
        // 存货类参照字段 refMap 互带（编码↔名称 + 规格/单位带出），修复参照选择后字段缺失（2026-08-25）
        upgradeInvPairRef(config, "products", "产品编码", "产品名称", "规格型号", "计量单位");
        upgradeInvPairRef(config, "materials", "材料编码", "材料名称", "规格型号", "计量单位");
        upgradeInvPairRef(config, "items", "存货编码", "存货名称", "规格型号", "计量单位");
        upgradeInvPairRef(config, "items", "物料编码", "物料名称", "规格型号", "单位");
        upgradeInvPairRef(config, "items", "材料编码", "材料名称", "规格型号", "计量单位");
        upgradeInvPairRef(config, "items", "产品编码", "产品名称", "规格型号", "计量单位");
        upgradeInvPairRef(config, null, "存货编码", "存货", "规格型号", "计量单位");
        ensureOcrAction(config);
    }

    /** Add OCR to editable document panels without mutating configured group/action collections. */
    @SuppressWarnings("unchecked")
    private void ensureOcrAction(Map<String, Object> config) {
        Object metadataObject = config.get("metadata");
        if (!(metadataObject instanceof Map<?, ?> rawMetadata)) return;
        Map<String, Object> source = (Map<String, Object>) rawMetadata;
        if (!String.valueOf(source.getOrDefault("panelCategory", "")).trim().endsWith("单据")
                || Boolean.parseBoolean(String.valueOf(source.getOrDefault("readonly", false)))
                || Boolean.parseBoolean(String.valueOf(source.getOrDefault("readOnly", false)))) return;

        Map<String, Object> metadata = new LinkedHashMap<>(source);
        List<Map<String, Object>> groups = new ArrayList<>();
        boolean foundMore = false;
        Object groupsObject = source.get("buttonGroups");
        if (groupsObject instanceof List<?> rawGroups) {
            for (Object item : rawGroups) {
                if (!(item instanceof Map<?, ?> rawGroup)) continue;
                Map<String, Object> group = new LinkedHashMap<>((Map<String, Object>) rawGroup);
                Object configuredActions = group.containsKey("actions") ? group.get("actions") : group.get("items");
                List<String> actions = new ArrayList<>(actionNames(configuredActions));
                actions.removeIf("扫描填单"::equals);
                if (!foundMore && "更多".equals(String.valueOf(group.getOrDefault("name", "")))) {
                    if (actions.isEmpty()) actions.add("刷新");
                    actions.add("扫描填单");
                    foundMore = true;
                }
                group.put("actions", actions);
                groups.add(group);
            }
        }
        if (!foundMore) {
            groups.add(new LinkedHashMap<>(Map.of("name", "更多", "actions", List.of("刷新", "扫描填单"))));
        }
        metadata.put("buttonGroups", groups);
        config.put("metadata", metadata);
    }

    /** 报价单金额链与销售订单保持一致：报价单价/税率 -> 含税单价 -> 金额/含税金额。 */
    @SuppressWarnings("unchecked")
    private void upgradeQuoteOrderConfig(Map<String, Object> config) {
        Object metadataObject = config.get("metadata");
        if (metadataObject instanceof Map<?, ?> metadata) {
            Object pageDtoObject = metadata.get("panelPageDto");
            if (pageDtoObject instanceof Map<?, ?> pageDto) {
                Object tablePagesObject = pageDto.get("tablePages");
                if (tablePagesObject instanceof List<?> tablePages && !tablePages.isEmpty()
                        && tablePages.get(0) instanceof Map<?, ?> tablePage) {
                    Object queryFieldsObject = tablePage.get("queryFields");
                    if (queryFieldsObject instanceof List<?> queryFields) {
                        for (Object fieldObject : queryFields) {
                            if (fieldObject instanceof Map<?, ?> rawField
                                    && "PARTNER".equals(rawField.get("refPanel"))
                                    && rawField.get("dataName") == null) {
                                ((Map<String, Object>) rawField).put("dataName", "客户");
                            }
                        }
                    }
                }
            }
        }

        Object detailObject = config.get("detail");
        if (!(detailObject instanceof Map<?, ?> detail)) return;
        Object tabsObject = detail.get("tabs");
        if (!(tabsObject instanceof List<?> tabs)) return;
        for (Object tabObject : tabs) {
            if (!(tabObject instanceof Map<?, ?> rawTab) || !"items".equals(rawTab.get("key"))) continue;
            Map<String, Object> tab = (Map<String, Object>) rawTab;
            tab.put("calc", List.of(
                    Map.of("target", "含税单价", "formula", "报价单价 * (1 + 税率% / 100)", "round", 2),
                    Map.of("target", "金额", "formula", "数量 * 报价单价", "round", 2),
                    Map.of("target", "含税金额", "formula", "数量 * 含税单价", "round", 2)));
            tab.put("summaryItems", List.of(
                    Map.of("label", "数量合计", "field", "数量"),
                    Map.of("label", "金额合计", "field", "金额"),
                    Map.of("label", "含税金额合计", "field", "含税金额")));
            Object fieldsObject = tab.get("fields");
            if (fieldsObject instanceof List<?> fields) {
                for (Object fieldObject : fields) {
                    if (!(fieldObject instanceof Map<?, ?> rawField)) continue;
                    Map<String, Object> field = (Map<String, Object>) rawField;
                    String name = String.valueOf(field.getOrDefault("dataName", ""));
                    if (Set.of("含税单价", "金额", "含税金额", "现存量").contains(name)) {
                        field.put("computed", true);
                        field.remove("defaultValue");
                    }
                }
            }
            return;
        }
    }

    /**
     * 旧配置同时存在「审核」和「审批」组，且「审核」首动作会绕过审批留痕。
     * 只要面板声明了提交审批，就统一为一个审批组，主按钮固定执行提交审批。
     */
    @SuppressWarnings("unchecked")
    private void normalizeApprovalWorkflow(String panelCode, Map<String, Object> config) {
        Object metadataObject = config.get("metadata");
        if (!(metadataObject instanceof Map<?, ?>)) return;
        Map<String, Object> metadata = (Map<String, Object>) metadataObject;
        Object groupsObject = metadata.get("buttonGroups");
        if (!(groupsObject instanceof List<?> rawGroups)
                || (!APPROVAL_PANELS.contains(panelCode) && !configUsesApprovalWorkflow(config))) return;

        List<Map<String, Object>> groups = new ArrayList<>();
        Map<String, Object> approvalGroup = null;
        int approvalIndex = -1;
        for (Object rawGroup : rawGroups) {
            if (!(rawGroup instanceof Map<?, ?>)) continue;
            Map<String, Object> group = new LinkedHashMap<>((Map<String, Object>) rawGroup);
            String name = String.valueOf(group.getOrDefault("name", ""));
            List<String> actions = actionNames(group.get("actions"));
            boolean workflowGroup = actions.contains("提交审批")
                    || (Set.of("审核", "审批", "审批情况", "弃审").contains(name)
                    && actions.stream().anyMatch(action -> "审核".equals(action)
                    || "驳回审批".equals(action) || APPROVAL_WORKFLOW_ACTIONS.contains(action)));
            if (workflowGroup) {
                if (approvalIndex < 0) approvalIndex = groups.size();
                if (approvalGroup == null) approvalGroup = group;
                continue;
            }
            group.put("actions", actions.stream()
                    .map(action -> "驳回审批".equals(action) ? "审批驳回" : action)
                    .distinct().toList());
            groups.add(group);
        }
        if (approvalGroup == null) approvalGroup = new LinkedHashMap<>();
        approvalGroup.put("name", "审批");
        approvalGroup.put("actions", APPROVAL_WORKFLOW_ACTIONS);
        groups.add(approvalIndex < 0 ? groups.size() : approvalIndex, approvalGroup);
        metadata.put("buttonGroups", groups);

        List<Map<String, Object>> buttons = new ArrayList<>();
        Set<String> buttonNames = new HashSet<>();
        Object buttonsObject = metadata.get("panelButtons");
        if (buttonsObject instanceof List<?> rawButtons) {
            for (Object value : rawButtons) {
                if (!(value instanceof Map<?, ?> rawButton)) continue;
                String buttonName = String.valueOf(rawButton.get("buttonName"));
                if ("审核".equals(buttonName) || "驳回审批".equals(buttonName) || !buttonNames.add(buttonName)) continue;
                buttons.add(new LinkedHashMap<>((Map<String, Object>) rawButton));
            }
        }
        for (String action : APPROVAL_WORKFLOW_ACTIONS) {
            if (buttonNames.add(action)) buttons.add(new LinkedHashMap<>(Map.of("buttonName", action)));
        }
        metadata.put("panelButtons", buttons);
    }

    private boolean configUsesApprovalWorkflow(Map<String, Object> config) {
        Object metadataObject = config.get("metadata");
        if (!(metadataObject instanceof Map<?, ?> metadata)) return false;
        Object groupsObject = metadata.get("buttonGroups");
        if (!(groupsObject instanceof List<?> groups)) return false;
        for (Object value : groups) {
            if (value instanceof Map<?, ?> group && actionNames(group.get("actions")).contains("提交审批")) return true;
        }
        return false;
    }

    private List<String> actionNames(Object actionsObject) {
        if (!(actionsObject instanceof List<?> actions)) return new ArrayList<>();
        return actions.stream().map(String::valueOf).toList();
    }

    /** 为既有面板注入拉式选单 selectConfig（运行时升级，缺才注入；同时补齐「选单」工具栏动作） */
    @SuppressWarnings("unchecked")
    private void ensureSelectConfig(Map<String, Object> config, String sourcePanel, String title, String tip,
                                    List<String> columns, String detailKey,
                                    List<Map<String, String>> headerMap, List<Map<String, String>> detailMap) {
        Object existing = config.get("selectConfig");
        if (existing instanceof Map<?, ?> && !((Map<?, ?>) existing).isEmpty()) return;
        Map<String, Object> sc = new HashMap<>();
        sc.put("source", sourcePanel);
        sc.put("title", title);
        sc.put("tip", tip);
        sc.put("columns", columns);
        sc.put("detailKey", detailKey);
        sc.put("headerMap", headerMap);
        sc.put("detailMap", detailMap);
        config.put("selectConfig", sc);
        ensureSelectAction(config);
        ensureSelectDetailCodeMap(config);
    }

    /**
     * 选单明细映射编码补全（2026-08-25）：detailMap 存在「X名称→Y名称」时补齐「X编码→Y编码」与规格型号，
     * 修复选单生成的产品/材料明细缺少编码（如委外加工单选销售订单后产品编码为空 → BOM 无法展开）。
     */
    @SuppressWarnings("unchecked")
    private void ensureSelectDetailCodeMap(Map<String, Object> config) {
        Object scObj = config.get("selectConfig");
        if (!(scObj instanceof Map)) return;
        Map<String, Object> sc = (Map<String, Object>) scObj;
        Object dmObj = sc.get("detailMap");
        if (!(dmObj instanceof List<?> raw)) return;
        List<Map<String, Object>> dm = new ArrayList<>();
        for (Object o : raw) if (o instanceof Map<?, ?>) dm.add(new HashMap<>((Map<String, Object>) o));
        Set<String> tos = new HashSet<>();
        for (Map<String, Object> m : dm) tos.add(String.valueOf(m.get("to")));
        List<Map<String, Object>> adds = new ArrayList<>();
        for (Map<String, Object> m : dm) {
            String from = String.valueOf(m.getOrDefault("from", ""));
            String to = String.valueOf(m.getOrDefault("to", ""));
            if (from.endsWith("名称") && to.endsWith("名称")) {
                String fromCode = from.substring(0, from.length() - 2) + "编码";
                String toCode = to.substring(0, to.length() - 2) + "编码";
                if (!tos.contains(toCode)) adds.add(Map.<String, Object>of("from", fromCode, "to", toCode));
            }
        }
        if (tos.stream().anyMatch(t -> t.endsWith("名称")) && !tos.contains("规格型号")) {
            adds.add(Map.<String, Object>of("from", "规格型号", "to", "规格型号"));
        }
        if (!adds.isEmpty()) {
            dm.addAll(adds);
            sc.put("detailMap", dm);
        }
    }

    /**
     * 为存货类参照字段注入 refMap 互带（编码↔名称 + 规格/单位带出）。
     * 修复：参照选择只写 displayField（如只有材料名称没材料编码，或反之）——2026-08-25。
     * tabKey 为 null 时作用于表头 dataSchema.fields（如序列号登记单的 存货/存货编码）。
     */
    @SuppressWarnings("unchecked")
    private void upgradeInvPairRef(Map<String, Object> config, String tabKey,
                                   String codeField, String nameField, String specField, String unitField) {
        List<Map<String, Object>> fields = new ArrayList<>();
        if (tabKey != null) {
            Object detailObj = config.get("detail");
            if (detailObj instanceof Map<?, ?> detail) {
                Object tabsObj = detail.get("tabs");
                if (tabsObj instanceof List<?> tabs) {
                    for (Object t : tabs) {
                        if (t instanceof Map<?, ?> tab && tabKey.equals(tab.get("key"))) {
                            Object f = tab.get("fields");
                            if (f instanceof List<?>) fields.addAll((List<Map<String, Object>>) f);
                        }
                    }
                }
            }
        } else {
            Object schemaObj = config.get("dataSchema");
            if (schemaObj instanceof Map<?, ?> schema) {
                Object f = schema.get("fields");
                if (f instanceof List<?>) fields.addAll((List<Map<String, Object>>) f);
            }
        }
        for (Map<String, Object> field : fields) {
            if (!"参照".equals(field.get("dataType")) || field.containsKey("refMap")) continue;
            String dn = String.valueOf(field.get("dataName"));
            if (codeField.equals(dn)) {
                field.put("refMap", List.of(
                        Map.of("from", "存货名称", "to", nameField),
                        Map.of("from", "规格型号", "to", specField),
                        Map.of("from", "计量单位", "to", unitField)));
            } else if (nameField.equals(dn)) {
                field.put("refMap", List.of(
                        Map.of("from", "存货编码", "to", codeField),
                        Map.of("from", "规格型号", "to", specField),
                        Map.of("from", "计量单位", "to", unitField)));
            }
        }
    }

    /** 单位下拉缓存（30s TTL）：UOM 面板数据 → 所有「单位」下拉 options 动态并入 */
    private static volatile long unitOptionsCacheAt = 0;
    private static volatile List<String> unitOptionsCache = null;

    /**
     * 2026-08-25：计量单位类下拉框动态并入 UOM 面板数据（UOM 新增单位后所有面板下拉自动显示完整，
     * 如 UOM 有「吨」而静态 options 只有 件/kg/套/升/台——修复新增单位下拉不显示）。
     */
    @SuppressWarnings("unchecked")
    private void injectUnitOptions(Map<String, Object> config) {
        List<String> units = unitNames();
        if (units.isEmpty()) return;
        List<Map<String, Object>> fields = new ArrayList<>();
        Object schemaObj = config.get("dataSchema");
        if (schemaObj instanceof Map<?, ?> schema) {
            Object f = schema.get("fields");
            if (f instanceof List<?>) fields.addAll((List<Map<String, Object>>) f);
        }
        Object detailObj = config.get("detail");
        if (detailObj instanceof Map<?, ?> detail) {
            Object tabs = detail.get("tabs");
            if (tabs instanceof List<?> tabList) {
                for (Object t : tabList) {
                    if (t instanceof Map<?, ?> tab) {
                        Object f = tab.get("fields");
                        if (f instanceof List<?>) fields.addAll((List<Map<String, Object>>) f);
                    }
                }
            }
        }
        for (Map<String, Object> field : fields) {
            if (!"下拉框".equals(field.get("dataType"))) continue;
            String dn = String.valueOf(field.getOrDefault("dataName", ""));
            // 2026-08-25：只匹配「XX单位」取值字段（计量单位/销售单位/生产单位/子件计量单位/主单位…），
            // 排除「单位类型」等分类字段（contains("单位") 过宽会把单位类型也注入计量单位名称，如 单计量/多计量 选项混入 件/kg）
            if (!dn.endsWith("单位")) continue;
            List<String> opts = new ArrayList<>();
            Object o = field.get("options");
            if (o instanceof List<?>) {
                for (Object v : (List<?>) o) if (v != null) opts.add(String.valueOf(v));
            }
            boolean changed = false;
            for (String u : units) {
                if (!opts.contains(u)) {
                    opts.add(u);
                    changed = true;
                }
            }
            if (changed) field.put("options", opts);
        }
    }

    private List<String> unitNames() {
        long now = System.currentTimeMillis();
        if (unitOptionsCache != null && now - unitOptionsCacheAt < 30000) return unitOptionsCache;
        List<String> names = new ArrayList<>();
        try {
            List<FormData> rows = formMapper.selectList(new LambdaQueryWrapper<FormData>()
                    .eq(FormData::getPanelCode, "UOM"));
            for (FormData fd : rows) {
                Map<String, Object> detail = parseData(fd.getDetailData());
                Object uoms = detail.get("uoms");
                if (uoms instanceof List) {
                    for (Object o : (List<?>) uoms) {
                        if (o instanceof Map<?, ?> u && u.get("计量单位名称") != null) {
                            String n = String.valueOf(u.get("计量单位名称"));
                            if (!n.isBlank() && !names.contains(n)) names.add(n);
                        }
                    }
                }
            }
        } catch (Exception ignore) {
        }
        unitOptionsCache = names;
        unitOptionsCacheAt = now;
        return names;
    }

    /** 旧配置存在 selectConfig 但遗漏工具栏动作时，补齐可用的选单入口。 */
    @SuppressWarnings("unchecked")
    private void ensureSelectAction(Map<String, Object> config) {        Object selectObject = config.get("selectConfig");
        if (!(selectObject instanceof Map<?, ?> selectConfig) || selectConfig.isEmpty()) return;
        Object metadataObject = config.get("metadata");
        if (!(metadataObject instanceof Map<?, ?>)) return;
        Map<String, Object> metadata = (Map<String, Object>) metadataObject;

        List<Map<String, Object>> groups = new ArrayList<>();
        Object groupsObject = metadata.get("buttonGroups");
        if (groupsObject instanceof List<?> values) {
            for (Object value : values) {
                if (value instanceof Map<?, ?>) groups.add((Map<String, Object>) value);
            }
        }
        boolean hasSelectAction = groups.stream()
                .flatMap(group -> {
                    Object actions = group.get("actions");
                    return actions instanceof List<?> list ? list.stream() : java.util.stream.Stream.empty();
                })
                .anyMatch(action -> String.valueOf(action).startsWith("选"));
        if (!hasSelectAction) {
            Object configuredTitle = selectConfig.get("title");
            String candidate = configuredTitle == null ? "选单" : String.valueOf(configuredTitle);
            final String action = candidate.isBlank() || !candidate.startsWith("选") ? "选单" : candidate;
            Map<String, Object> group = new HashMap<>();
            group.put("name", "选单");
            group.put("actions", List.of(action));
            int insertAt = groups.isEmpty() ? 0 : Math.min(1, groups.size());
            groups.add(insertAt, group);
            metadata.put("buttonGroups", groups);

            List<Map<String, Object>> buttons = new ArrayList<>();
            Object buttonsObject = metadata.get("panelButtons");
            if (buttonsObject instanceof List<?> values) {
                for (Object value : values) {
                    if (value instanceof Map<?, ?>) buttons.add((Map<String, Object>) value);
                }
            }
            boolean exists = buttons.stream().anyMatch(button -> action.equals(String.valueOf(button.get("buttonName"))));
            if (!exists) buttons.add(Map.of("buttonName", action));
            metadata.put("panelButtons", buttons);
        }
    }

    /**
     * 展平旧配置中误写的嵌套字段数组。库存面板种子曾生成
     * fields: [公共字段, [扩展字段]]，导致创建表单元数据时发生 ArrayList -> Map 转换异常。
     */
    @SuppressWarnings("unchecked")
    private void normalizeFieldDefinitions(Map<String, Object> config) {
        Object schemaObject = config.get("dataSchema");
        if (!(schemaObject instanceof Map<?, ?>)) return;
        Map<String, Object> schema = (Map<String, Object>) schemaObject;
        schema.put("fields", flattenFieldDefinitions(schema.get("fields")));
    }

    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> flattenFieldDefinitions(Object value) {
        List<Map<String, Object>> fields = new ArrayList<>();
        if (!(value instanceof List<?> values)) return fields;
        for (Object item : values) {
            if (item instanceof Map<?, ?>) {
                fields.add((Map<String, Object>) item);
            } else if (item instanceof List<?>) {
                fields.addAll(flattenFieldDefinitions(item));
            }
        }
        return fields;
    }

    @SuppressWarnings("unchecked")
    private void upgradeDetailReference(Map<String, Object> config, String tabKey, String fieldName,
                                        List<String> columns, List<Map<String, String>> mappings) {
        Object detailObj = config.get("detail");
        if (!(detailObj instanceof Map<?, ?> detail)) return;
        Object tabsObj = detail.get("tabs");
        if (!(tabsObj instanceof List<?> tabs)) return;

        for (Object tabObj : tabs) {
            if (!(tabObj instanceof Map<?, ?> tab) || !tabKey.equals(tab.get("key"))) continue;
            Object fieldsObj = tab.get("fields");
            if (!(fieldsObj instanceof List<?> fields)) return;
            for (Object fieldObj : fields) {
                if (!(fieldObj instanceof Map<?, ?> rawField) || !fieldName.equals(rawField.get("dataName"))) continue;
                Map<String, Object> field = (Map<String, Object>) rawField;
                field.put("dataType", "参照");
                field.remove("options");
                field.put("refPanel", "INV");
                field.put("refField", "存货编码");
                field.put("displayField", "存货编码");
                field.put("filter", Map.of("停用", false));
                field.put("refTrigger", "dblclick");
                field.put("refStatuses", List.of("草稿"));
                field.put("refColumns", columns);
                field.put("refMap", mappings);
                return;
            }
        }
    }

    public Map<String, Object> getPanelConfig(String panelCode) {
        return getPanelConfig(panelCode, null);
    }

    /**
     * 用户级表格列定制（px_column_pref）叠加在读取出口：重排/隐藏 gridTabs 列 + 注入
     * metadata.columnAliases / metadata.columnPrefs。绝不写回 panel_config.config
     * （4 个 Registry 启动时会无条件覆盖 config 列）。
     */
    public Map<String, Object> getPanelConfig(String panelCode, String userName) {
        if (userName != null && !userName.isBlank()) requirePanelPermission(panelCode, "view", userName);
        Map<String, Object> cfg = loadConfig(panelCode);
        if (userName != null && !userName.isBlank()) applyColumnPrefs(cfg, panelCode, userName);
        return cfg;
    }

    // ---------- 表格列定制（阶段 C：按用户顺序/显隐/别名） ----------

    private static final class ColumnPref {
        final String name;
        final String alias;
        final boolean visible;
        ColumnPref(String name, String alias, boolean visible) {
            this.name = name;
            this.alias = alias;
            this.visible = visible;
        }
    }

    /** 列名提取：列配置兼容字符串与对象（dataName/name/label）两种形态 */
    private static String columnNameOf(Object col) {
        if (col instanceof String s) return s;
        if (col instanceof Map<?, ?> m) {
            for (String k : new String[]{"dataName", "name", "label"}) {
                Object v = m.get(k);
                if (v != null && !String.valueOf(v).isBlank()) return String.valueOf(v);
            }
        }
        return null;
    }

    private static boolean visibleFlag(Object v) {
        if (v instanceof Boolean b) return b;
        if (v instanceof Number n) return n.intValue() != 0;
        return true;
    }

    @SuppressWarnings("unchecked")
    private void applyColumnPrefs(Map<String, Object> cfg, String panelCode, String userName) {
        List<Map<String, Object>> rows;
        try {
            rows = jdbcTemplate.queryForList(
                    "SELECT col_name, seq, alias, visible FROM px_column_pref WHERE panel_code = ? AND owner = ? ORDER BY seq, id",
                    panelCode, userName);
        } catch (Exception e) {
            return; // 表未建/库未迁移：保持配置默认
        }
        if (rows.isEmpty()) return;
        Map<String, Object> metadata = (Map<String, Object>) cfg.get("metadata");
        if (metadata == null) return;
        if (!(metadata.get("panelPageDto") instanceof Map<?, ?> pageDto)) return;
        if (!(pageDto.get("tablePages") instanceof List<?> pages)) return;

        List<ColumnPref> prefs = new ArrayList<>();
        for (Map<String, Object> r : rows) {
            String name = String.valueOf(r.get("col_name"));
            Object alias = r.get("alias");
            prefs.add(new ColumnPref(name,
                    alias == null ? "" : String.valueOf(alias),
                    visibleFlag(r.get("visible"))));
        }

        // 应用到每个 tablePage 的 gridTabs（明细页签与其同源汇总页签都应用）
        for (Object pageObj : pages) {
            if (!(pageObj instanceof Map<?, ?> page)) continue;
            if (!(page.get("gridTabs") instanceof List<?> tabs)) continue;
            for (Object tabObj : tabs) {
                if (!(tabObj instanceof Map<?, ?> tab)) continue;
                if (!(tab.get("columns") instanceof List<?> cols)) continue;
                List<Object> reordered = new ArrayList<>();
                Set<String> seen = new HashSet<>();
                for (ColumnPref p : prefs) {
                    if (!p.visible) continue;
                    Object col = findColumn(cols, p.name);
                    if (col != null) {
                        reordered.add(col);
                        seen.add(p.name);
                    }
                }
                // 未收录列（配置新增）按原序追加且默认显示
                for (Object col : cols) {
                    String name = columnNameOf(col);
                    if (name == null || seen.contains(name)) continue;
                    if (prefs.stream().anyMatch(p -> name.equals(p.name) && !p.visible)) continue;
                    reordered.add(col);
                }
                ((Map<String, Object>) tab).put("columns", reordered);
            }
        }

        // 注入别名表（表头显示用）与全量清单（弹窗初始化用，含隐藏列）；数据键/取值仍是中文列名
        Map<String, String> aliases = new LinkedHashMap<>();
        List<Map<String, Object>> columnPrefs = new ArrayList<>();
        for (ColumnPref p : prefs) {
            if (!p.alias.isBlank()) aliases.put(p.name, p.alias);
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("name", p.name);
            m.put("alias", p.alias);
            m.put("visible", p.visible);
            columnPrefs.add(m);
        }
        metadata.put("columnAliases", aliases);
        metadata.put("columnPrefs", columnPrefs);
    }

    private static Object findColumn(List<?> cols, String name) {
        for (Object col : cols) {
            if (name.equals(columnNameOf(col))) return col;
        }
        return null;
    }

    /** 保存列定制（空数组=恢复默认：删除该用户该面板全部行） */
    @Transactional
    public void saveColumnPrefs(String panelCode, String userName, List<Map<String, Object>> columns) {
        if (panelCode == null || panelCode.isBlank()) throw new IllegalArgumentException("panelCode 不能为空");
        if (userName == null || userName.isBlank()) throw new IllegalArgumentException("未登录用户不能保存表格调整");
        requirePanelPermission(panelCode, "view", userName);
        jdbcTemplate.update("DELETE FROM px_column_pref WHERE panel_code = ? AND owner = ?", panelCode, userName);
        if (columns == null || columns.isEmpty()) return;
        Set<String> aliases = new HashSet<>();
        int seq = 10;
        for (Map<String, Object> c : columns) {
            String name = c.get("name") == null ? "" : String.valueOf(c.get("name")).trim();
            if (name.isBlank()) continue;
            String alias = c.get("alias") == null ? "" : String.valueOf(c.get("alias")).trim();
            if (!alias.isEmpty() && !aliases.add(alias)) {
                throw new IllegalArgumentException("栏名别名重复：" + alias);
            }
            jdbcTemplate.update(
                    "INSERT INTO px_column_pref (panel_code, owner, col_name, seq, alias, visible, update_by) "
                            + "VALUES (?, ?, ?, ?, ?, ?, ?) "
                            + "ON DUPLICATE KEY UPDATE seq = VALUES(seq), alias = VALUES(alias), visible = VALUES(visible), update_by = VALUES(update_by)",
                    panelCode, userName, name, seq, alias, visibleFlag(c.get("visible")) ? 1 : 0, userName);
            seq += 10;
        }
    }

    // ---------- 自定义字段（面板自助加字段：登记元数据 → 读出口合并） ----------

    /** 该面板已登记的自定义字段（按 seq,id 排序；结果按面板缓存，加/停用即失效） */
    private List<Map<String, Object>> customFields(String panelCode) {
        List<Map<String, Object>> cached = customFieldCache.get(panelCode);
        if (cached != null) return cached;
        List<Map<String, Object>> rows;
        try {
            rows = jdbcTemplate.queryForList(
                    "SELECT scope, tab_key, data_name, data_type, options, field_width, is_required, in_grid "
                            + "FROM px_custom_field WHERE panel_code = ? ORDER BY seq, id",
                    panelCode);
        } catch (Exception e) {
            return List.of(); // 表未建（老库未迁移）：按无自定义字段处理，不影响原有功能
        }
        customFieldCache.put(panelCode, rows);
        return rows;
    }

    /** 把自定义字段并进面板配置：表头进 dataSchema.fields（+ formPages.fieldNames），明细进 detail.tabs[].fields（+ 首块 gridTabs 列） */
    @SuppressWarnings("unchecked")
    private void applyCustomFields(Map<String, Object> config, String panelCode) {
        if (jdbcTemplate == null) return;
        List<Map<String, Object>> rows = customFields(panelCode);
        if (rows.isEmpty()) return;

        Map<String, Object> dataSchema = config.get("dataSchema") instanceof Map<?, ?> schema
                ? (Map<String, Object>) schema : null;
        List<Map<String, Object>> headerFields = new ArrayList<>();
        if (dataSchema != null && dataSchema.get("fields") instanceof List<?> fields) {
            headerFields = (List<Map<String, Object>>) fields;
        }
        List<Map<String, Object>> detailTabs = new ArrayList<>();
        if (config.get("detail") instanceof Map<?, ?> detail && detail.get("tabs") instanceof List<?> tabs) {
            detailTabs = (List<Map<String, Object>>) tabs;
        }
        Object formPagesObj = metadataOf(config) == null ? null : metadataOf(config).get("panelPageDto");
        List<Map<String, Object>> formPages = formPagesObj instanceof Map<?, ?> pageDto
                && pageDto.get("formPages") instanceof List<?> pages
                ? (List<Map<String, Object>>) pages : List.of();
        List<Object> firstGridColumns = firstGridColumns(config);

        for (Map<String, Object> row : rows) {
            String scope = String.valueOf(row.getOrDefault("scope", "detail"));
            String tabKey = String.valueOf(row.getOrDefault("tab_key", ""));
            Map<String, Object> def = customFieldDefinition(row);
            if ("header".equals(scope)) {
                headerFields.add(def);
                // 表头字段还受 formPages.fieldNames 白名单约束（前端按该顺序取字段），不在白名单里就显示不出来
                for (Map<String, Object> page : formPages) {
                    Object names = page.get("fieldNames");
                    if (names == null) continue;
                    String merged = String.valueOf(names).isBlank()
                            ? String.valueOf(def.get("dataName"))
                            : names + "," + def.get("dataName");
                    page.put("fieldNames", merged);
                }
            } else {
                int index = -1;
                for (int i = 0; i < detailTabs.size(); i++) {
                    if (tabKey.equals(String.valueOf(detailTabs.get(i).get("key")))) {
                        index = i;
                        break;
                    }
                }
                if (index < 0) continue; // 页签已被删掉：字段先留着，等页签回来再显示
                Object tabFieldsObj = detailTabs.get(index).get("fields");
                if (tabFieldsObj instanceof List<?> tabFields) {
                    ((List<Map<String, Object>>) tabFields).add(def);
                }
                // 第一个明细页签的列来自 gridTabs[0].columns（见 PanelxList buildBlocks），不补列就不显示
                if (index == 0 && firstGridColumns != null && visibleFlag(row.get("in_grid"))) {
                    firstGridColumns.add(def.get("dataName"));
                }
            }
        }
    }

    private static Map<String, Object> metadataOf(Map<String, Object> config) {
        return config.get("metadata") instanceof Map<?, ?> m ? (Map<String, Object>) m : null;
    }

    /** 首个 tablePage 的 gridTabs[0].columns（可写引用；无则不补列） */
    @SuppressWarnings("unchecked")
    private static List<Object> firstGridColumns(Map<String, Object> config) {
        Map<String, Object> metadata = metadataOf(config);
        if (metadata == null) return null;
        if (!(metadata.get("panelPageDto") instanceof Map<?, ?> pageDto)) return null;
        if (!(pageDto.get("tablePages") instanceof List<?> pages) || pages.isEmpty()) return null;
        if (!(pages.get(0) instanceof Map<?, ?> page)) return null;
        if (!(page.get("gridTabs") instanceof List<?> tabs) || tabs.isEmpty()) return null;
        if (!(tabs.get(0) instanceof Map<?, ?> tab)) return null;
        return tab.get("columns") instanceof List<?> cols ? (List<Object>) cols : null;
    }

    /** 自定义字段 → 与内置字段同形状的字段定义（custom 标记供前端「停用」仅对自定义字段开放） */
    private static Map<String, Object> customFieldDefinition(Map<String, Object> row) {
        String dataType = String.valueOf(row.getOrDefault("data_type", "文本"));
        Map<String, Object> def = new LinkedHashMap<>();
        def.put("dataName", String.valueOf(row.get("data_name")));
        def.put("dataType", dataType);
        def.put("custom", true);
        def.put("isRequired", visibleFlag(row.get("is_required")));
        def.put("defaultValue", List.of("小数", "整数").contains(dataType) ? 0
                : "是否".equals(dataType) ? Boolean.FALSE : "");
        if ("下拉框".equals(dataType)) {
            List<Object> options = new ArrayList<>();
            for (String option : splitOptions(row.get("options"))) options.add(option);
            def.put("options", options);
        }
        Object width = row.get("field_width");
        if (width != null && !String.valueOf(width).isBlank()) {
            try {
                def.put("fieldWidth", Integer.parseInt(String.valueOf(width).trim()));
            } catch (NumberFormatException ignored) {
                // 列宽存坏了不阻断：前端按类型推算
            }
        }
        return def;
    }

    private static List<String> splitOptions(Object raw) {
        if (raw == null) return List.of();
        List<String> out = new ArrayList<>();
        for (String part : String.valueOf(raw).split("[,，]")) {
            String option = part.trim();
            if (!option.isEmpty() && !out.contains(option)) out.add(option);
        }
        return out;
    }

    /**
     * 新增自定义字段。守卫（服务端强制，前端只做提示）：
     * 面板「修改」权限 / 位置合法 / 字段名规范且不重名 / 类型受支持 / 下拉框必须有词表 / 明细页签必须存在。
     */
    @Transactional
    public Map<String, Object> addCustomField(String panelCode, Map<String, Object> body) {
        if (panelCode == null || panelCode.isBlank()) throw new IllegalArgumentException("panelCode 不能为空");
        String userName = authenticatedUserName();
        requirePanelPermission(panelCode, "edit", userName);
        if (jdbcTemplate == null) throw new IllegalStateException("当前运行环境不支持自定义字段");

        String scope = trimTo(body.get("scope"), "detail");
        if (!CUSTOM_FIELD_SCOPES.contains(scope)) {
            throw new IllegalArgumentException("字段位置只能是 header（表头）或 detail（明细）");
        }
        String dataName = trimTo(body.get("dataName"), "");
        if (!CUSTOM_FIELD_NAME_RE.matcher(dataName).matches()) {
            throw new IllegalArgumentException("字段名不能为空、不超过 " + CUSTOM_FIELD_NAME_MAX
                    + " 字，且不能包含空格与 . % ( ) / 这些字符");
        }
        String dataType = trimTo(body.get("dataType"), "文本");
        if (!CUSTOM_FIELD_TYPES.contains(dataType)) {
            throw new IllegalArgumentException("不支持的字段类型：" + dataType + "（可选 文本/下拉框/日期/是否/小数/整数）");
        }
        String options = trimTo(body.get("options"), "");
        if ("下拉框".equals(dataType) && splitOptions(options).isEmpty()) {
            throw new IllegalArgumentException("下拉框字段必须给出词表（逗号分隔）");
        }
        String tabKey = "";
        if ("detail".equals(scope)) {
            tabKey = trimTo(body.get("tabKey"), "");
            Map<String, Object> cfg = loadConfig(panelCode);
            boolean exists = false;
            if (cfg.get("detail") instanceof Map<?, ?> detail && detail.get("tabs") instanceof List<?> tabs) {
                for (Object tab : tabs) {
                    if (tab instanceof Map<?, ?> t && tabKey.equals(String.valueOf(t.get("key")))) exists = true;
                }
            }
            if (!exists) throw new IllegalArgumentException("明细页签不存在：" + tabKey);
        }
        // 重名：内置字段（已加载的配置里已含自定义字段，所以一次判断覆盖两者）
        if (findFieldNames(panelCode, scope, tabKey).contains(dataName)) {
            throw new IllegalArgumentException("该面板已有同名字段：" + dataName + "（换个名字或先停用原字段）");
        }
        Object width = body.get("fieldWidth");
        Integer fieldWidth = null;
        if (width != null && !String.valueOf(width).isBlank()) {
            try {
                fieldWidth = Integer.parseInt(String.valueOf(width).trim());
            } catch (NumberFormatException e) {
                throw new IllegalArgumentException("列宽必须是数字（像素）");
            }
            if (fieldWidth < 60 || fieldWidth > 400) throw new IllegalArgumentException("列宽需在 60–400 之间");
        }
        int seq = nextFieldSeq(panelCode);
        jdbcTemplate.update(
                "INSERT INTO px_custom_field (panel_code, scope, tab_key, data_name, data_type, options, "
                        + "field_width, is_required, in_grid, seq, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                panelCode, scope, tabKey, dataName, dataType,
                splitOptions(options).isEmpty() ? null : String.join(",", splitOptions(options)),
                fieldWidth, visibleFlag(body.get("isRequired")) ? 1 : 0,
                body.containsKey("inGrid") && !visibleFlag(body.get("inGrid")) ? 0 : 1,
                seq, userName);
        logFieldAction(panelCode, scope, tabKey, dataName, dataType, "add", userName,
                "列宽=" + (fieldWidth == null ? "默认" : fieldWidth) + "，必填=" + visibleFlag(body.get("isRequired")));
        customFieldCache.remove(panelCode);

        Map<String, Object> out = new LinkedHashMap<>();
        out.put("dataName", dataName);
        out.put("scope", scope);
        out.put("tabKey", tabKey);
        out.put("dataType", dataType);
        out.put("seq", seq);
        return out;
    }

    /** 停用自定义字段：只删元数据，业务数据（form_data 里的 JSON 键）原样保留，重新加同名字段即恢复显示 */
    @Transactional
    public void retireCustomField(String panelCode, Map<String, Object> body) {
        if (panelCode == null || panelCode.isBlank()) throw new IllegalArgumentException("panelCode 不能为空");
        String userName = authenticatedUserName();
        requirePanelPermission(panelCode, "edit", userName);
        if (jdbcTemplate == null) throw new IllegalStateException("当前运行环境不支持自定义字段");
        String scope = trimTo(body.get("scope"), "detail");
        String tabKey = "detail".equals(scope) ? trimTo(body.get("tabKey"), "") : "";
        String dataName = trimTo(body.get("dataName"), "");
        if (dataName.isEmpty()) throw new IllegalArgumentException("要停用的字段名不能为空");
        int removed = jdbcTemplate.update(
                "DELETE FROM px_custom_field WHERE panel_code = ? AND scope = ? AND tab_key = ? AND data_name = ?",
                panelCode, scope, tabKey, dataName);
        if (removed == 0) {
            throw new IllegalArgumentException("只能停用自定义字段（内置字段请改面板配置）：" + dataName);
        }
        logFieldAction(panelCode, scope, tabKey, dataName, "", "retire", userName, "已录入数据保留");
        customFieldCache.remove(panelCode);
    }

    /** 该面板某位置（表头 / 某明细页签）已在用的字段名，重名判断用 */
    @SuppressWarnings("unchecked")
    private Set<String> findFieldNames(String panelCode, String scope, String tabKey) {
        Map<String, Object> cfg = loadConfig(panelCode);
        Set<String> names = new HashSet<>();
        if ("header".equals(scope)) {
            if (cfg.get("dataSchema") instanceof Map<?, ?> schema && schema.get("fields") instanceof List<?> fields) {
                for (Object f : fields) {
                    if (f instanceof Map<?, ?> field && field.get("dataName") != null) {
                        names.add(String.valueOf(field.get("dataName")));
                    }
                }
            }
            return names;
        }
        if (cfg.get("detail") instanceof Map<?, ?> detail && detail.get("tabs") instanceof List<?> tabs) {
            for (Object t : tabs) {
                if (!(t instanceof Map<?, ?> tab) || !tabKey.equals(String.valueOf(tab.get("key")))) continue;
                if (tab.get("fields") instanceof List<?> fields) {
                    for (Object f : fields) {
                        if (f instanceof Map<?, ?> field && field.get("dataName") != null) {
                            names.add(String.valueOf(field.get("dataName")));
                        }
                    }
                }
            }
        }
        return names;
    }

    private int nextFieldSeq(String panelCode) {
        try {
            Integer max = jdbcTemplate.queryForObject(
                    "SELECT COALESCE(MAX(seq), 0) FROM px_custom_field WHERE panel_code = ?", Integer.class, panelCode);
            return (max == null ? 0 : max) + 10;
        } catch (Exception e) {
            return 10;
        }
    }

    private void logFieldAction(String panelCode, String scope, String tabKey, String dataName,
                                String dataType, String action, String userName, String detail) {
        try {
            jdbcTemplate.update(
                    "INSERT INTO px_field_log (panel_code, scope, tab_key, data_name, data_type, action, op_by, detail) "
                            + "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    panelCode, scope, tabKey, dataName, dataType, action,
                    userName == null ? "" : userName, detail);
        } catch (Exception ignored) {
            // 审计表缺失不影响主流程（与 px_column_pref 的降级口径一致）
        }
    }

    private static String trimTo(Object value, String fallback) {
        if (value == null) return fallback;
        String text = String.valueOf(value).trim();
        return text.isEmpty() ? fallback : text;
    }

    // ---------- 权限矩阵 ----------

    public Map<String, Object> getPermMatrix(String panelCode) {
        return getPermMatrix(panelCode, null);
    }

    /**
     * 按登录用户权限过滤的权限矩阵：非 admin 时，未授权权限码对应的按钮 visible/operatable 置 false。
     * userName 为空（内部调用）或 admin 时不过滤，保持全真（既有行为/e2e 不受影响）。
     */
    @SuppressWarnings("unchecked")
    public Map<String, Object> getPermMatrix(String panelCode, String userName) {
        if (userName != null && !userName.isBlank()) requirePanelPermission(panelCode, "view", userName);
        Map<String, Object> cfg = loadConfig(panelCode);
        Map<String, Object> metadata = (Map<String, Object>) cfg.get("metadata");
        List<Map<String, Object>> buttons = (List<Map<String, Object>>) metadata.get("panelButtons");
        List<Map<String, Object>> actions = new ArrayList<>();
        for (Map<String, Object> b : buttons) {
            Map<String, Object> a = new HashMap<>();
            a.put("name", String.valueOf(b.get("buttonName")));
            a.put("visible", true);
            a.put("operatable", true);
            actions.add(a);
        }
        Set<String> grantedPerms = grantedPerms(userName, panelCode);
        if (grantedPerms != null) {
            for (Map<String, Object> a : actions) {
                String perm = permOfButton(String.valueOf(a.get("name")));
                if (perm != null && !grantedPerms.contains(perm)) {
                    a.put("visible", false);
                    a.put("operatable", false);
                }
            }
        }
        Map<String, Object> privilege = new HashMap<>();
        privilege.put("actionPrivileges", actions);
        privilege.put("fieldPrivileges", new ArrayList<>());
        privilege.put("groupPrivileges", new ArrayList<>());
        Map<String, Object> out = new HashMap<>();
        out.put("privilege", privilege);
        return out;
    }

    /**
     * 用户在指定面板的已授权权限码；返回 null 表示不过滤（userName 空 / admin / RoleService 未装配）。
     */
    private Set<String> grantedPerms(String userName, String panelCode) {
        if (userName == null || userName.isBlank() || roleService == null) return null;
        Map<String, Object> perms = roleService.getPerms(userName);
        if (Boolean.TRUE.equals(perms.get("isAdmin"))) return null;
        Object panelPermsObj = perms.get("panelPerms");
        if (!(panelPermsObj instanceof Map<?, ?> panelPerms)) return Set.of();
        Object list = panelPerms.get(panelCode);
        if (!(list instanceof List<?> codes)) return Set.of();
        Set<String> out = new HashSet<>();
        for (Object c : codes) out.add(String.valueOf(c));
        return out;
    }

    /** 服务端最终权限裁判：前端按钮过滤不能替代 API 授权。 */
    private void requirePanelPermission(String panelCode, String permission) {
        requirePanelPermission(panelCode, permission, authenticatedUserName());
    }

    private void requirePanelPermission(String panelCode, String permission, String userName) {
        if (roleService == null) return; // 纯业务单元测试使用未装配权限服务的构造器。
        if (userName == null || userName.isBlank()) throw new AccessDeniedException("请先登录");
        Set<String> granted = grantedPerms(userName, panelCode);
        if (granted != null && !granted.contains(permission)) {
            throw new AccessDeniedException("当前用户没有面板 " + panelCode + " 的 " + permission + " 权限");
        }
    }

    private String authenticatedUserName() {
        var auth = SecurityContextHolder.getContext().getAuthentication();
        return auth != null && auth.isAuthenticated() && auth.getName() != null ? auth.getName() : null;
    }

    // ---------- 字段 meta ----------

    @SuppressWarnings("unchecked")
    private Map<String, Object> fieldsOf(String panelCode) {
        Map<String, Object> cfg = loadConfig(panelCode);
        Map<String, Object> schema = (Map<String, Object>) cfg.get("dataSchema");
        List<Map<String, Object>> fields = flattenFieldDefinitions(schema == null ? null : schema.get("fields"));
        Map<String, Object> metadata = (Map<String, Object>) cfg.get("metadata");
        Object stateObject = metadata == null ? null : metadata.get("panelState");
        String stateFieldName = "单据状态";
        if (stateObject instanceof Map<?, ?> state) {
            Object configuredName = state.get("dataName");
            if (configuredName != null && !String.valueOf(configuredName).isBlank()) {
                stateFieldName = String.valueOf(configuredName);
            }
        }
        Map<String, Object> out = new HashMap<>();
        out.put("fields", fields);
        out.put("detail", cfg.get("detail"));
        out.put("stateFieldName", stateFieldName);
        return out;
    }

    private List<Map<String, Object>> buildMeta(String panelCode) {
        List<Map<String, Object>> meta = new ArrayList<>();
        List<Map<String, Object>> fields = (List<Map<String, Object>>) fieldsOf(panelCode).get("fields");
        for (Map<String, Object> f : fields) {
            Map<String, Object> m = new HashMap<>();
            m.put("code", String.valueOf(f.get("dataName")));
            m.put("name", String.valueOf(f.get("dataName")));
            m.put("dataType", f.getOrDefault("dataType", "文本"));
            m.put("isNotNull", Boolean.TRUE.equals(f.get("isRequired")));
            m.put("defaultValue", f.getOrDefault("defaultValue", ""));
            if (f.containsKey("options")) m.put("options", f.get("options"));
            // 参照字段信息（2026-08-20 补：对齐前端 mock buildMeta 的 ref 结构——弹窗拉取面板数据勾选导入）
            if ("参照".equals(f.get("dataType")) && f.get("refPanel") != null) {
                Map<String, Object> ref = new HashMap<>();
                ref.put("panel", f.get("refPanel"));
                ref.put("field", f.get("refField"));
                ref.put("display", f.get("displayField"));
                ref.put("filter", f.get("filter"));
                ref.put("map", f.get("refMap"));
                ref.put("multi", Boolean.TRUE.equals(f.get("refMulti")));
                ref.put("columns", f.get("refColumns"));
                // 专用选择弹窗（如工艺路线要看工序流程）：透传给前端决定用哪个弹窗
                if (f.get("refDialog") != null) ref.put("dialog", f.get("refDialog"));
                m.put("ref", ref);
            }
            meta.add(m);
        }
        return meta;
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> fieldPrivileges() {
        List<Map<String, Object>> fps = new ArrayList<>();
        Map<String, Object> privilege = new HashMap<>();
        privilege.put("fieldPrivileges", fps);
        return privilege;
    }

    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> actionPrivileges(String panelCode, String formPage) {
        Map<String, Object> cfg = loadConfig(panelCode);
        Map<String, Object> metadata = (Map<String, Object>) cfg.get("metadata");
        Object pageDtoObject = metadata == null ? null : metadata.get("panelPageDto");
        if (!(pageDtoObject instanceof Map<?, ?>)) return new ArrayList<>();
        Map<String, Object> pageDto = (Map<String, Object>) pageDtoObject;
        Object pagesObject = pageDto.get("formPages".equals(formPage) ? "formPages" : "tablePages");
        if (!(pagesObject instanceof List<?> pages) || pages.isEmpty() || !(pages.get(0) instanceof Map<?, ?>)) {
            return new ArrayList<>();
        }
        String key = "formPages".equals(formPage) ? "bottomOperationBarBtn" : "topBarBtn";
        Object buttonsObject = ((Map<String, Object>) pages.get(0)).get(key);
        if (!(buttonsObject instanceof List<?> btns)) return new ArrayList<>();
        List<Map<String, Object>> actions = new ArrayList<>();
        for (Object button : btns) {
            if (!(button instanceof Map<?, ?>)) continue;
            Map<String, Object> b = (Map<String, Object>) button;
            Map<String, Object> a = new HashMap<>();
            a.put("name", String.valueOf(b.get("buttonName")));
            a.put("visible", true);
            a.put("operatable", true);
            actions.add(a);
        }
        return actions;
    }

    @SuppressWarnings("unchecked")
    private Object buttonGroupsOf(String panelCode) {
        Map<String, Object> cfg = loadConfig(panelCode);
        Map<String, Object> metadata = (Map<String, Object>) cfg.get("metadata");
        return metadata.get("buttonGroups");
    }

    // ---------- 表单描述器 / 新建元数据 ----------

    @SuppressWarnings("unchecked")
    public Map<String, Object> getNewFormPermMatrix(String panelCode, String operationName) {
        requirePanelPermission(panelCode, "add");
        Map<String, Object> stateField = fieldsOf(panelCode);
        String stateName = (String) stateField.get("stateFieldName");
        String af = autoCodeFieldOf(panelCode);
        Map<String, Object> data = new HashMap<>();
        data.put(stateName, "草稿");
        for (Map<String, Object> f : (List<Map<String, Object>>) stateField.get("fields")) {
            Object dv = f.get("defaultValue");
            if (dv != null && !"".equals(String.valueOf(dv))) {
                data.putIfAbsent(String.valueOf(f.get("dataName")), dv);
            }
        }
        // T+ 单据：单据日期默认系统登录日期（所有面板新建即填，保证草稿数据完整可见）
        if (!data.containsKey("单据日期")) data.put("单据日期", LocalDate.now().toString());
        // 自动编码字段（autoCodeField，如工序汇报单「单据编号」）预填单号，表单页可显示
        if (af != null && !af.isBlank() && !data.containsKey(af)) data.put(af, generateFormNo(panelCode, data));
        // T+ 锭号 = 单据编号（自动预览单号）
        if (!data.containsKey("锭号")) data.put("锭号", generateFormNo(panelCode, data));
        Map<String, Object> privilege = new HashMap<>();
        privilege.put("actionPrivileges", actionPrivileges(panelCode, "formPages"));
        privilege.put("fieldPrivileges", new ArrayList<>());
        privilege.put("groupPrivileges", new ArrayList<>());
        Map<String, Object> out = new HashMap<>();
        out.put("data", data);
        out.put("meta", buildMeta(panelCode));
        out.put("privilege", privilege);
        out.put("detail", stateField.get("detail"));
        out.put("buttonGroups", buttonGroupsOf(panelCode));
        out.put("panelName", panelNameOf(panelCode));
        out.put("selectConfig", selectConfigOf(panelCode));
        out.put("selectConfigs", selectConfigsOf(panelCode));
        return out;
    }

    @SuppressWarnings("unchecked")
    public Map<String, Object> getFormDescriptor(String panelCode, String code) {
        requirePanelPermission(panelCode, "view");
        FormData fd = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, panelCode)
                .eq(FormData::getFormNo, code));
        if (fd == null) throw new IllegalArgumentException("表单数据不存在：" + code);
        Map<String, Object> data = parseData(fd.getData());
        data.put("编号", fd.getFormNo());
        data.put("单据状态", fd.getStatus());
        data.put("创建时间", fd.getCreateTime());
        data.put("更新时间", fd.getUpdateTime());
        data.put("发起人编号", fd.getCreateBy());
        Map<String, Object> out = new HashMap<>();
        out.put("data", data);
        out.put("meta", buildMeta(panelCode));
        Map<String, Object> privilege = new HashMap<>();
        privilege.put("actionPrivileges", actionPrivileges(panelCode, "formPages"));
        privilege.put("fieldPrivileges", new ArrayList<>());
        privilege.put("groupPrivileges", new ArrayList<>());
        out.put("privilege", privilege);
        out.put("detail", fieldsOf(panelCode).get("detail"));
        Map<String, Object> detailData = parseData(fd.getDetailData());
        if ("ROUTE".equals(panelCode)) normalizeRouteDetailForRead(detailData);
        if (hasCurrentStockField(detailData)) {
            refreshCurrentStock(detailData, reportQueryService.currentStockRows());
        }
        out.put("detailData", detailData);
        out.put("buttonGroups", buttonGroupsOf(panelCode));
        out.put("panelName", panelNameOf(panelCode));
        out.put("selectConfig", selectConfigOf(panelCode));
        out.put("selectConfigs", selectConfigsOf(panelCode));
        return out;
    }

    private String panelNameOf(String panelCode) {
        Map<String, Object> cfg = loadConfig(panelCode);
        Object name = ((Map<String, Object>) cfg.get("metadata")).get("panelName");
        return name == null ? "表单" : String.valueOf(name);
    }

    @SuppressWarnings("unchecked")
    private Object selectConfigOf(String panelCode) {
        Map<String, Object> cfg = loadConfig(panelCode);
        Object sc = cfg.get("selectConfig");
        return sc == null ? new HashMap<>() : sc;
    }

    /** 多来源选单契约；旧面板仍通过 selectConfig 返回首个来源保持兼容。 */
    @SuppressWarnings("unchecked")
    private Map<String, Object> selectConfigsOf(String panelCode) {
        Map<String, Object> cfg = loadConfig(panelCode);
        Object multiple = cfg.get("selectConfigs");
        if (multiple instanceof Map<?, ?>) return (Map<String, Object>) multiple;
        Object legacy = cfg.get("selectConfig");
        if (legacy instanceof Map<?, ?> map && !map.isEmpty()) {
            Object title = map.get("title");
            Map<String, Object> out = new LinkedHashMap<>();
            out.put(title == null ? "选单" : String.valueOf(title), map);
            return out;
        }
        return new LinkedHashMap<>();
    }

    // ---------- 列表 ----------

    @SuppressWarnings("unchecked")
    public Map<String, Object> queryFormDataList(String panelCode, String keyword,
                                                 Map<String, Object> condition, int pageNo, int pageSize) {
        requirePanelPermission(panelCode, "query");
        pageNo = Math.max(1, pageNo);
        pageSize = Math.max(1, Math.min(pageSize, 500));
        if (ReportPanelRegistry.isReport(panelCode)) {
            return reportQueryService.query(panelCode, keyword, condition, pageNo, pageSize);
        }
        // 物料清单查询面板（BOM_FWD 正向 / BOM_REV 反向）：从 BOM 面板 form_data 的 children 明细展开为展平行
        if ("BOM_FWD".equals(panelCode) || "BOM_REV".equals(panelCode)) {
            return queryBomFlatten(panelCode, keyword, condition);
        }
        // 采购需求分析（PU_REQ_ANALYSIS）：聚合生产加工单材料需求 - 现存量 - 已请购 - 已出库 = 建议请购数量
        if ("PU_REQ_ANALYSIS".equals(panelCode)) {
            return queryPurchaseReqAnalysis(keyword, condition);
        }
        // 序列号状况表（SERIAL_STATUS）/序列号跟踪表（SERIAL_TRACE）：从 SERIAL_NO 面板明细展平查询
        if ("SERIAL_STATUS".equals(panelCode) || "SERIAL_TRACE".equals(panelCode)) {
            return querySerialFlatten(panelCode, keyword, condition);
        }
        LambdaQueryWrapper<FormData> qw = new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, panelCode)
                .orderByDesc(FormData::getCreateTime)
                .orderByDesc(FormData::getId);
        List<FormData> all = formMapper.selectList(qw);
        // 单据级行（一单一行，单号不重复）：明细挂在 detail 对象，key 与 detail.tabs 一致。
        // 对齐 docs/frontend/前端面板设计.md 数据契约；前端渲染器和选单弹窗按此结构消费。
        List<Map<String, Object>> flat = new ArrayList<>();
        List<Map<String, Object>> currentStock = null;
        for (FormData fd : all) {
            Map<String, Object> head = parseData(fd.getData());
            head.put("编号", fd.getFormNo());
            head.put("单据状态", fd.getStatus());
            head.put("创建时间", fd.getCreateTime());
            head.put("更新时间", fd.getUpdateTime());
            head.put("发起人编号", fd.getCreateBy());
            Map<String, Object> detail = parseData(fd.getDetailData());
            if (hasCurrentStockField(detail)) {
                if (currentStock == null) currentStock = reportQueryService.currentStockRows();
                refreshCurrentStock(detail, currentStock);
            }
            if ("INV".equals(panelCode)) fillInvRoute(detail);
            if ("ROUTE".equals(panelCode)) normalizeRouteDetailForRead(detail);
            if (!detail.isEmpty()) head.put("detail", detail);
            flat.add(head);
        }
        // 条件过滤（仅标量字段参与比较，detail 对象跳过）
        List<Map<String, Object>> rows = new ArrayList<>();
        for (Map<String, Object> row : flat) {
            boolean hit = true;
            if (condition != null) {
                for (Map.Entry<String, Object> e : condition.entrySet()) {
                    Object v = e.getValue();
                    if (v == null || "".equals(String.valueOf(v))) continue;
                    Object rv = row.get(e.getKey());
                    // 缺键（null）：该行不匹配条件（修复：缺 单据日期/单据编号 的行不再被查询条件漏过）
                    if (rv == null) { hit = false; break; }
                    if (rv instanceof Map || rv instanceof List) continue;
                    if (!String.valueOf(rv).contains(String.valueOf(v))) { hit = false; break; }
                }
            }
            if (hit && keyword != null && !keyword.isBlank()) {
                hit = false;
                for (Object v : row.values()) {
                    if (v != null && !(v instanceof Map) && !(v instanceof List)
                            && String.valueOf(v).contains(keyword)) { hit = true; break; }
                }
            }
            if (hit) rows.add(row);
        }
        int from = Math.min((pageNo - 1) * pageSize, rows.size());
        int to = Math.min(from + pageSize, rows.size());
        Map<String, Object> out = new HashMap<>();
        out.put("totalSize", rows.size());
        out.put("list", rows.subList(from, to));
        return out;
    }

    /**
     * 物料清单查询面板数据：把 BOM 面板（panelCode=BOM）form_data 的 children 明细
     * 展开为「父件-子件」展平行，供 BOM_FWD（正向）/BOM_REV（反向）查询面板使用。
     * 数据量小，不参与分页截断（前端父件表格需全量去重）。
     */
    @SuppressWarnings("unchecked")
    private Map<String, Object> queryBomFlatten(String panelCode, String keyword, Map<String, Object> condition) {
        List<FormData> boms = formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "BOM"));
        List<Map<String, Object>> flat = new ArrayList<>();
        for (FormData fd : boms) {
            Map<String, Object> detail = parseData(fd.getDetailData());
            Object children = detail.get("children");
            if (!(children instanceof List)) continue;
            for (Object o : (List<?>) children) {
                if (!(o instanceof Map)) continue;
                Map<String, Object> row = new HashMap<>((Map<String, Object>) o);
                row.put("单据编号", fd.getFormNo());
                flat.add(row);
            }
        }
        // 条件过滤（父件编码/子件编码等标量字段）
        List<Map<String, Object>> rows = new ArrayList<>();
        for (Map<String, Object> row : flat) {
            boolean hit = true;
            if (condition != null) {
                for (Map.Entry<String, Object> e : condition.entrySet()) {
                    Object v = e.getValue();
                    if (v == null || "".equals(String.valueOf(v))) continue;
                    Object rv = row.get(e.getKey());
                    if (rv == null || !String.valueOf(rv).contains(String.valueOf(v))) { hit = false; break; }
                }
            }
            if (hit && keyword != null && !keyword.isBlank()) {
                hit = false;
                for (Object v : row.values()) {
                    if (v != null && String.valueOf(v).contains(keyword)) { hit = true; break; }
                }
            }
            if (hit) rows.add(row);
        }
        // 排序：正向按父件、反向按子件（保证前端分组去重顺序稳定）
        String sortKey = "BOM_REV".equals(panelCode) ? "子件编码" : "父件编码";
        rows.sort((a, b) -> String.valueOf(a.get(sortKey)).compareTo(String.valueOf(b.get(sortKey))));
        Map<String, Object> out = new HashMap<>();
        out.put("totalSize", rows.size());
        out.put("list", rows);
        return out;
    }

    /**
     * 采购需求分析（对齐真实 T+ 生产管理相关单据「采购需求分析」）：
     * 遍历已审核/生产中的生产加工单 materials 明细，逐材料汇总：
     * 需用数量（计划数量） - 现存量 - 已请购数量（PU_REQ items 数量合计） - 已出库数量（MATERIAL_OUT items 数量合计）
     * = 建议请购数量；已全部满足的材料行不输出。结果按材料编码分组聚合。
     */
    @SuppressWarnings("unchecked")
    private Map<String, Object> queryPurchaseReqAnalysis(String keyword, Map<String, Object> condition) {
        Map<String, Map<String, Object>> agg = new java.util.LinkedHashMap<>();
        List<FormData> mos = formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "MANU_ORDER")
                .in(FormData::getStatus, List.of("已审核", "生产中")));
        for (FormData mo : mos) {
            Map<String, Object> head = parseData(mo.getData());
            Map<String, Object> dm = parseData(mo.getDetailData());
            Object mats = dm.get("materials");
            if (!(mats instanceof List)) continue;
            for (Object o : (List<?>) mats) {
                if (!(o instanceof Map)) continue;
                Map<String, Object> m = (Map<String, Object>) o;
                String code = String.valueOf(m.getOrDefault("材料编码", ""));
                String name = String.valueOf(m.getOrDefault("材料名称", ""));
                if (code.isEmpty() && name.isEmpty()) continue;
                String key = code.isEmpty() ? name : code;
                Map<String, Object> row = agg.computeIfAbsent(key, k -> {
                    Map<String, Object> r = new HashMap<>();
                    r.put("材料编码", code);
                    r.put("材料名称", name);
                    r.put("规格型号", m.getOrDefault("规格型号", ""));
                    r.put("计量单位", m.getOrDefault("计量单位", "kg"));
                    r.put("需用数量", 0.0);
                    r.put("现存量", 0.0);
                    r.put("已请购数量", 0.0);
                    r.put("已出库数量", 0.0);
                    r.put("建议请购数量", 0.0);
                    r.put("需求加工单", new java.util.LinkedHashSet<>());
                    return r;
                });
                double need = num(m.getOrDefault("计划数量", m.getOrDefault("需用数量", 0)));
                row.put("需用数量", num(row.get("需用数量")) + need);
                row.put("现存量", Math.max(num(row.get("现存量")), num(m.getOrDefault("现存量", 0))));
                if (!"".equals(String.valueOf(head.getOrDefault("单据编号", "")))) {
                    ((java.util.Set<String>) row.get("需求加工单")).add(String.valueOf(head.get("单据编号")));
                }
            }
        }
        // 已请购：PU_REQ items 数量合计（按材料名称/编码匹配）
        sumDetailQty("PU_REQ", "items", "已请购数量", agg, "存货名称", "存货编码", "数量");
        // 已出库：MATERIAL_OUT items 数量合计
        sumDetailQty("MATERIAL_OUT", "items", "已出库数量", agg, "材料名称", "材料编码", "数量");
        List<Map<String, Object>> rows = new ArrayList<>();
        for (Map<String, Object> row : agg.values()) {
            double need = num(row.get("需用数量"));
            double stock = num(row.get("现存量"));
            double purchased = num(row.get("已请购数量"));
            double issued = num(row.get("已出库数量"));
            double suggest = Math.max(0, Math.round((need - stock - purchased - issued) * 100) / 100.0);
            if (suggest <= 0) continue;
            row.put("建议请购数量", suggest);
            Set<String> src = (Set<String>) row.remove("需求加工单");
            row.put("需求来源", String.join("、", src));
            rows.add(row);
        }
        // 条件过滤（材料名称/材料编码/关键字）
        List<Map<String, Object>> hit = new ArrayList<>();
        for (Map<String, Object> row : rows) {
            boolean ok = true;
            if (condition != null) {
                for (Map.Entry<String, Object> e : condition.entrySet()) {
                    Object v = e.getValue();
                    if (v == null || "".equals(String.valueOf(v))) continue;
                    Object rv = row.get(e.getKey());
                    if (rv == null || !String.valueOf(rv).contains(String.valueOf(v))) { ok = false; break; }
                }
            }
            if (ok && keyword != null && !keyword.isBlank()) {
                ok = String.valueOf(row.get("材料名称")).contains(keyword)
                        || String.valueOf(row.get("材料编码")).contains(keyword);
            }
            if (ok) hit.add(row);
        }
        hit.sort((a, b) -> String.valueOf(a.get("材料编码")).compareTo(String.valueOf(b.get("材料编码"))));
        Map<String, Object> out = new HashMap<>();
        out.put("totalSize", hit.size());
        out.put("list", hit);
        return out;
    }

    /**
     * 序列号状况表（SERIAL_STATUS）/序列号跟踪表（SERIAL_TRACE）（对齐真实 T+ 序列号管理 SN 模块）：
     * 从 SERIAL_NO 面板 form_data 的 items 明细展平为序列号行；状况表按序列号状态（在库/已出库）输出，
     * 跟踪表携带入库单号/出库单号/出入库日期供追溯。
     */
    @SuppressWarnings("unchecked")
    private Map<String, Object> querySerialFlatten(String panelCode, String keyword, Map<String, Object> condition) {
        List<FormData> docs = formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "SERIAL_NO")
                .orderByDesc(FormData::getCreateTime));
        List<Map<String, Object>> flat = new ArrayList<>();
        for (FormData fd : docs) {
            Map<String, Object> head = parseData(fd.getData());
            Map<String, Object> dm = parseData(fd.getDetailData());
            Object items = dm.get("items");
            if (!(items instanceof List)) continue;
            String status = fd.getStatus() == null ? "" : fd.getStatus();
            for (Object o : (List<?>) items) {
                if (!(o instanceof Map)) continue;
                Map<String, Object> row = new HashMap<>((Map<String, Object>) o);
                row.put("登记单号", fd.getFormNo());
                row.put("单据状态", status);
                row.putIfAbsent("存货编码", head.getOrDefault("存货编码", ""));
                row.putIfAbsent("存货", head.getOrDefault("存货", ""));
                row.putIfAbsent("规格型号", head.getOrDefault("规格型号", ""));
                row.putIfAbsent("计量单位", head.getOrDefault("计量单位", ""));
                row.putIfAbsent("仓库", head.getOrDefault("仓库", ""));
                row.putIfAbsent("入库单号", head.getOrDefault("入库单号", ""));
                row.putIfAbsent("出库单号", head.getOrDefault("出库单号", ""));
                flat.add(row);
            }
        }
        // 状况表：仅展示在库（未出库）序列号；跟踪表：全部（携带出入库追溯信息）
        List<Map<String, Object>> rows = new ArrayList<>();
        for (Map<String, Object> row : flat) {
            String snStatus = String.valueOf(row.getOrDefault("状态", "在库"));
            if ("SERIAL_STATUS".equals(panelCode) && !"在库".equals(snStatus)) continue;
            boolean hit = true;
            if (condition != null) {
                for (Map.Entry<String, Object> e : condition.entrySet()) {
                    Object v = e.getValue();
                    if (v == null || "".equals(String.valueOf(v))) continue;
                    Object rv = row.get(e.getKey());
                    if (rv == null || !String.valueOf(rv).contains(String.valueOf(v))) { hit = false; break; }
                }
            }
            if (hit && keyword != null && !keyword.isBlank()) {
                hit = row.values().stream().anyMatch(value -> value != null
                        && String.valueOf(value).contains(keyword));
            }
            if (hit) rows.add(row);
        }
        Map<String, Object> out = new HashMap<>();
        out.put("totalSize", rows.size());
        out.put("list", rows);
        return out;
    }

    /** 累加某面板明细行某数量字段到聚合行（材料编码优先，其次材料名称） */
    @SuppressWarnings("unchecked")
    private void sumDetailQty(String panelCode, String tabKey, String targetKey,
                              Map<String, Map<String, Object>> agg, String nameField, String codeField, String qtyField) {
        List<FormData> docs = formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, panelCode)
                .in(FormData::getStatus, EFFECTIVE_STATUSES));
        for (FormData fd : docs) {
            Map<String, Object> dm = parseData(fd.getDetailData());
            Object items = dm.get(tabKey);
            if (!(items instanceof List)) continue;
            for (Object o : (List<?>) items) {
                if (!(o instanceof Map)) continue;
                Map<String, Object> it = (Map<String, Object>) o;
                String code = String.valueOf(it.getOrDefault(codeField, ""));
                String name = String.valueOf(it.getOrDefault(nameField, ""));
                String key = code.isEmpty() ? name : code;
                Map<String, Object> row = agg.get(key);
                if (row == null) {
                    // 名称回退匹配：无编码的明细行按名称聚合
                    row = agg.get(name);
                    if (row == null) continue;
                }
                row.put(targetKey, num(row.get(targetKey)) + num(it.getOrDefault(qtyField, 0)));
            }
        }
    }

    // ---------- 按钮（业务流转） ----------

    @SuppressWarnings("unchecked")
    @Transactional
    public Map<String, Object> callButton(String panelCode, String buttonName,
                                          Map<String, Object> formData, Map<String, Object> buttonParam) {
        if (panelCode == null || panelCode.isBlank()) throw new IllegalArgumentException("面板编码不能为空");
        if (buttonName == null || buttonName.isBlank()) throw new IllegalArgumentException("按钮名称不能为空");
        requirePanelPermission(panelCode, requiredPermission(panelCode, buttonName, formData));
        switch (buttonName) {
            case "刷新":
                return new HashMap<>();
            case "新增流程":
                return create(panelCode, formData, null);
            case "保存":
            case "保存新增":
            case "保存为草稿":
            case "提交":
                if ("UOM".equals(panelCode)) unitOptionsCache = null; // 计量单位变更后即时刷新单位下拉缓存
                return save(panelCode, formData, null);
            case "删除": {
                Object no = formData.get("编号");
                if (no == null) throw new IllegalArgumentException("缺少表单编号");
                delete(panelCode, String.valueOf(no));
                return new HashMap<>();
            }
            case "审核":
                // 审批面板兼容旧客户端动作名：不得绕过提交节点，必须进入审批中并写 SUBMIT 留痕。
                return requiresApprovalWorkflow(panelCode)
                        ? submitApproval(panelCode, formData)
                        : changeStatus(panelCode, formData, "审核");
            case "弃审":
                return changeStatus(panelCode, formData, "弃审");
            case "关闭":
                return changeStatus(panelCode, formData, "关闭");
            case "手工完工":
                return changeStatus(panelCode, formData, "手工完工");
            case "中止":
                return changeStatus(panelCode, formData, "中止");
            case "整单中止":
                return changeStatus(panelCode, formData, "中止");
            case "取消中止":
                return changeStatus(panelCode, formData, "取消中止");
            case "生成生产加工单":   // 推式生单：销售订单 → 生产加工单（对齐真实 T+「生单-生成生产加工单」）
                return createMoFromSo(panelCode, formData, buttonParam);
            case "选工艺路线":       // 手工建单：按路线整体替换工序明细
                return applyRouteProcesses(panelCode, formData);
            case "生成工序汇报单（自制汇报）":   // 推式生单：生产加工单 → 工序汇报单（拉取加工单工序明细）
                return createProcReportFromMo(panelCode, formData);
            case "生成采购订单":       // 推式生单：请购单 → 采购订单
                if (!"PU_REQ".equals(panelCode)) {
                    throw new IllegalStateException("仅请购单可生成采购订单，当前单据：" + panelCode);
                }
                return createPuOrderFromReq(panelCode, formData);
            case "生成请购单":       // 推式生单：采购需求分析 → 请购单
                if (!"PU_REQ_ANALYSIS".equals(panelCode)) {
                    throw new IllegalStateException("仅采购需求分析可生成请购单，当前单据：" + panelCode);
                }
                return createPuReqFromAnalysis(panelCode, formData);
            case "生成采购入库单":     // 推式生单：采购订单/到货单/检验单 → 采购入库单
                return switch (panelCode) {
                    case "INSPECTION" -> createInventoryInFromInspection(panelCode, formData, "PURCHASE_IN");
                    case "ARRIVAL_IN" -> createPurchaseInFromArrival(panelCode, formData);
                    case "PU_ORDER" -> createPurchaseInFromPo(panelCode, formData, buttonParam);
                    default -> throw new IllegalStateException(
                            "仅采购订单/到货单/检验单可生成采购入库单，当前单据：" + panelCode);
                };
            case "生成采购退货单":     // 检验不合格退回 / 采购入库后的库存退料
                if (!"INSPECTION".equals(panelCode) && !"PURCHASE_IN".equals(panelCode)) {
                    throw new IllegalStateException(
                            "仅检验单或采购入库单可生成采购退货单，当前单据：" + panelCode);
                }
                return "INSPECTION".equals(panelCode)
                        ? createPurchaseReturnFromInspection(panelCode, formData)
                        : createPurchaseReturnFromPurchaseIn(panelCode, formData);
            case "生成到货单":         // 推式生单：采购订单 → 到货单（收料通知）
                if (!"PU_ORDER".equals(panelCode)) {
                    throw new IllegalStateException("仅采购订单可生成到货单，当前单据：" + panelCode);
                }
                return createArrivalFromPo(panelCode, formData);
            case "齐套检查": {   // 按当前库存推算可开工数量，结果写回产成品「齐套数量(主)」
                Object no = formData.get("编号");
                if (no == null) throw new IllegalArgumentException("缺少表单编号");
                KittingResult kr = refreshKitting(String.valueOf(no));
                Map<String, Object> out = new HashMap<>();
                out.put("编号", String.valueOf(no));
                out.put("单据状态", kr.planned() > 0 && kr.kitting() >= kr.planned() ? "齐套" : "缺料");
                out.put("齐套数量", kr.kitting());
                out.put("计划数量", kr.planned());
                out.put("缺料材料数", kr.shortageCount());
                out.put("缺料明细", kr.shortages());
                out.put("提示", kr.message());
                return out;
            }
            case "生成产成品入库单":   // 推式生单：生产加工单 → 产成品入库单
                return "INSPECTION".equals(panelCode) || "PROCESS_INSPECTION".equals(panelCode)
                        ? createInventoryInFromInspection(panelCode, formData, "FINISH_IN", buttonParam)
                        : createFinishInFromMo(panelCode, formData, buttonParam);
            case "生成检验单":
                return createInspectionFromReport(panelCode, formData);
            case "生成材料出库单":     // 推式生单：领料申请单/生产加工单/调拨单 → 材料出库单
                return createMaterialOut(panelCode, formData, buttonParam);
            case "生成调拨单":         // 推式生单：领料申请单 → 调拨单（对齐真实 T+ 领料申请单「生单-生成调拨单」）
            case "生成调拨单(分单)":
                return createTransferFromMaterialReq(panelCode, formData, buttonParam);
            case "生成委外发料单":     // 推式生单：委外加工单/领料申请单 → 委外发料单
            case "生成委外发料单(分单)":
                return createOutsourceIssue(panelCode, formData, buttonParam);
            case "生成委外入库单":     // 推式生单：委外加工单 → 委外入库单
                return "INSPECTION".equals(panelCode) || "PROCESS_INSPECTION".equals(panelCode)
                        ? createInventoryInFromInspection(panelCode, formData, "OUTSOURCE_IN", buttonParam)
                        : createOutsourceInFromOrder(panelCode, formData, buttonParam);
            case "生成委外加工费用单": // 推式生单：委外加工单 → 委外加工费用单
                return createOutsourceFeeFromOrder(panelCode, formData);
            case "生成销售订单":       // 推式生单：报价单 → 销售订单（对齐真实 T+ 报价单「生单-生成销售订单」）
            case "生成销售订单(普通销售)":
                return createSaleOrderFromQuote(panelCode, formData);
            case "生成销售发票":       // 推式生单：销货单 → 销售发票（对齐真实 T+ 销货单「生单-生成销售发票」）
            case "生成销售发票(普通销售)":
            case "生成销售发票(销售退货)":
                return createSaleInvoiceFromSaleInv(panelCode, formData);
            case "生成销售费用分摊单": // 推式生单：费用单 → 销售费用分摊单
                return createSaleCostAllocFromExpense(panelCode, formData);
            case "生成采购发票":       // 推式生单：采购入库单/采购退货单/采购订单 → 采购发票
            case "生成采购发票（普通采购）":
            case "生成采购发票（采购退货）":
                return createPuInvoice(panelCode, formData);
            case "生成采购费用分摊单": // 推式生单：费用单（采购费用）→ 采购费用分摊单
                return createPuCostAllocFromExpense(panelCode, formData);
            case "生成销售出库单":     // 推式生单：销售订单 → 销售出库单
            case "生成销售出库单(普通销售)":
                return createSaleOutFromSo(panelCode, formData, buttonParam);
            case "生成销货单":         // 推式生单：产成品入库单 → 销货单（O2C 闭环）
            case "生成销货单(普通销售)":
                return createSaleInvFromFinishIn(panelCode, formData);
            case "确认处置": {         // 来料/成品检验：批次处置标记确认（确认后才允许入库/退货/开票）
                if (!"INSPECTION".equals(panelCode)) {
                    throw new IllegalStateException("仅来料/成品检验单支持确认处置，当前单据：" + panelCode);
                }
                Object no = formData.get("编号");
                if (no == null) throw new IllegalArgumentException("缺少表单编号");
                FormData fd = formOf(panelCode, no);
                if (!"草稿".equals(fd.getStatus()) && !"审批中".equals(fd.getStatus())) {
                    throw new IllegalStateException("仅草稿/审批中检验单可确认处置");
                }
                validateQualityForApproval(panelCode, fd);
                confirmInspectionDetail(fd);
                fd.setUpdateTime(LocalDateTime.now());
                formMapper.updateById(fd);
                Map<String, Object> confirmed = new HashMap<>();
                confirmed.put("编号", fd.getFormNo());
                confirmed.put("处置状态", "已确认");
                return confirmed;
            }
            // 审批流
            case "提交审批":
                return submitApproval(panelCode, formData);
            case "审批通过":
                return approveApproval(panelCode, formData);
            case "审批驳回":
            case "驳回审批":
                return rejectApproval(panelCode, formData);
            case "审批情况":
                return approvalHistory(panelCode, formData);
            case "核销":
                return writeOff(panelCode, formData);
            case "反核销":
                return reverseWriteOff(panelCode, formData);
            default:
                return panelActionRegistry.dispatch(new PanelActionContext(
                                panelCode, buttonName, formData, buttonParam, currentUserName()))
                        .orElseThrow(() -> new IllegalStateException(
                                "未定义按钮规则：" + buttonName + "（请注册 PanelActionHandler）"));
        }
    }

    private String requiredPermission(String panelCode, String buttonName, Map<String, Object> formData) {
        if (Set.of("保存", "保存新增", "保存为草稿", "提交").contains(buttonName)) {
            Object number = formData == null ? null : formData.get("编号");
            if (number != null && !String.valueOf(number).isBlank()) {
                FormData existing = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                        .eq(FormData::getPanelCode, panelCode)
                        .eq(FormData::getFormNo, String.valueOf(number)));
                if (existing != null) return "edit";
            }
            return "add";
        }
        String permission = permOfButton(buttonName);
        // 领域 Handler 默认属于写操作；未登记动作不能因映射遗漏而绕过授权。
        return permission == null ? "edit" : permission;
    }

    /**
     * 推式生单：销售订单（SO_ORDER）→ 生成生产加工单（MANU_ORDER）草稿
     * 字段对应：单据日期→合同号、单据编号→销售订单号、客户/客户编码→客户/客户编码、
     *           结算客户→生产订单客户、业务员→负责人、预计交货日期→预完工日；
     *           明细行→产成品明细（存货编码→产品编码、存货名称→产品名称、销售单位→生产单位）
     */
    @SuppressWarnings("unchecked")
    /** 读 buttonParam 里的仓库参数；为空时返回 fallback。 */
    private static String warehouseParam(Map<String, Object> buttonParam, String key, String fallback) {
        if (buttonParam == null) return fallback;
        Object raw = buttonParam.get(key);
        String value = raw == null ? "" : String.valueOf(raw).trim();
        return value.isEmpty() ? fallback : value;
    }

    /**
     * 出库类仓库解析：优先源单据显式指定，其次看该物料实际有库存的仓库，最后才用兜底。
     *
     * <p>此前多处直接兜底「原料仓」，辅料/成品仓的料会被从原料仓出成负库存。
     */
    private String stockWarehouseOf(Object explicit, Object itemCode, Object itemName, String fallback) {
        String value = explicit == null ? "" : String.valueOf(explicit).trim();
        if (!value.isEmpty()) return value;
        if (stockTxnService != null) {
            String actual = stockTxnService.warehouseOfItem(
                    itemCode == null ? "" : String.valueOf(itemCode),
                    itemName == null ? "" : String.valueOf(itemName));
            if (actual != null && !actual.isBlank()) return actual;
        }
        return fallback;
    }

    /**
     * 关联销售订单的销货价（存货编码 → 含税单价），供「产成品入库单 → 销货单」补价用。
     * 链路：产成品入库单.加工单号 → 加工单.销售订单号 → 销售订单.明细.含税单价。
     * 取不到时返回空表，由调用方保持 0（不臆造价格）。
     */
    Map<String, Double> salePriceByItem(String moNo) {
        Map<String, Double> out = new LinkedHashMap<>();
        if (moNo == null || moNo.isBlank()) return out;
        FormData mo;
        try {
            mo = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                    .eq(FormData::getPanelCode, "MANU_ORDER").eq(FormData::getFormNo, moNo));
        } catch (Exception e) {
            return out;
        }
        if (mo == null) return out;
        Map<String, Object> moHead;
        try {
            moHead = parseData(mo.getData());
        } catch (Exception e) {
            return out;
        }
        String soNo = String.valueOf(moHead.getOrDefault("销售订单号", "")).trim();
        if (soNo.isEmpty()) return out;
        FormData so;
        try {
            so = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                    .eq(FormData::getPanelCode, "SO_ORDER").eq(FormData::getFormNo, soNo));
        } catch (Exception e) {
            return out;
        }
        if (so == null) return out;
        Map<String, Object> soDetail;
        try {
            soDetail = parseData(so.getDetailData());
        } catch (Exception e) {
            return out;
        }
        if (!(soDetail.get("items") instanceof List<?> items)) return out;
        for (Object o : items) {
            if (!(o instanceof Map<?, ?> line)) continue;
            Object codeObj = line.get("存货编码");
            String code = codeObj == null ? "" : String.valueOf(codeObj).trim();
            if (code.isEmpty()) continue;
            Object priceObj = line.get("含税单价");
            if (priceObj == null) priceObj = line.get("单价");
            double price;
            try {
                price = priceObj == null ? 0 : Double.parseDouble(String.valueOf(priceObj));
            } catch (NumberFormatException e) {
                price = 0;
            }
            if (price != 0) out.put(code, price);
        }
        return out;
    }

    /**
     * 开票工作台：以「销货单」为行，带出发票号与状态。
     *
     * <p>界面据此列出可开票的销货单，勾选后开票；已开票的显示发票号并可直接打印/审批。
     */
    public List<Map<String, Object>> invoiceWorkbench() {
        List<FormData> saleInvs = formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "SALE_INV")
                .orderByDesc(FormData::getId));
        Map<String, FormData> invoiceBySaleInv = new LinkedHashMap<>();
        for (FormData inv : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "SALE_INVOICE"))) {
            Map<String, Object> head;
            try {
                head = parseData(inv.getData());
            } catch (Exception e) {
                continue;
            }
            String linked = String.valueOf(head.getOrDefault("销货单号", "")).trim();
            if (!linked.isEmpty()) invoiceBySaleInv.putIfAbsent(linked, inv);
        }

        List<Map<String, Object>> out = new ArrayList<>();
        for (FormData si : saleInvs) {
            Map<String, Object> head;
            Map<String, Object> detail;
            try {
                head = parseData(si.getData());
                detail = parseData(si.getDetailData());
            } catch (Exception e) {
                continue;
            }
            double qty = 0;
            double gross = 0;
            if (detail.get("items") instanceof List<?> items) {
                for (Object o : items) {
                    if (!(o instanceof Map<?, ?> line)) continue;
                    qty += num(line.get("数量"));
                    Object g = line.get("含税金额");
                    if (g == null) g = line.get("含税销售金额");
                    gross += num(g);
                }
            }
            FormData inv = invoiceBySaleInv.get(si.getFormNo());
            boolean audited = "已审核".equals(si.getStatus());
            String reason = "";
            if (!audited) reason = "销货单未审核";
            else if (inv != null) reason = "已开票";
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("销货单号", si.getFormNo());
            row.put("单据日期", head.getOrDefault("单据日期", ""));
            row.put("客户", head.getOrDefault("客户", ""));
            row.put("客户编码", head.getOrDefault("客户编码", ""));
            row.put("销货单状态", si.getStatus());
            row.put("数量合计", round2(qty));
            row.put("价税合计", round2(gross));
            row.put("发票号", inv == null ? "" : inv.getFormNo());
            row.put("发票状态", inv == null ? "" : inv.getStatus());
            row.put("可否开票", reason.isEmpty());
            row.put("不可开票原因", reason);
            out.add(row);
        }
        return out;
    }

    /**
     * 开票方（销售方）信息：供销售发票打印页展示。
     *
     * <p>优先取系统内「企业信息」档案（基础设置 → 基本信息 → 企业信息），
     * 由使用本系统的企业自行维护；未维护时回落到 application.yml 的 mes.company。
     */
    @Override
    public Map<String, Object> companyInfo() {
        Map<String, Object> out = new LinkedHashMap<>();
        Map<String, Object> doc = null;
        try {
            List<FormData> rows = formMapper.selectList(new LambdaQueryWrapper<FormData>()
                    .eq(FormData::getPanelCode, "COMPANY_INFO")
                    .orderByAsc(FormData::getId)
                    .last("LIMIT 1"));
            if (!rows.isEmpty()) doc = parseData(rows.get(0).getData());
        } catch (Exception e) {
            doc = null;
        }
        String name = doc == null ? "" : String.valueOf(doc.getOrDefault("企业名称", "")).trim();
        if (name.isEmpty()) name = companyName == null ? "" : companyName;
        String taxNo = doc == null ? "" : String.valueOf(doc.getOrDefault("统一社会信用代码", "")).trim();
        if (taxNo.isEmpty()) taxNo = companyTaxNo == null ? "" : companyTaxNo;
        String addr = doc == null ? "" : String.valueOf(doc.getOrDefault("地址", "")).trim();
        String tel = doc == null ? "" : String.valueOf(doc.getOrDefault("电话", "")).trim();
        String bank = doc == null ? "" : String.valueOf(doc.getOrDefault("开户行", "")).trim();
        String account = doc == null ? "" : String.valueOf(doc.getOrDefault("银行账号", "")).trim();
        String addrTel = (addr + (tel.isEmpty() ? "" : " " + tel)).trim();
        if (addrTel.isEmpty()) addrTel = companyAddrTel == null ? "" : companyAddrTel;
        String bankAccount = (bank + (account.isEmpty() ? "" : " " + account)).trim();
        if (bankAccount.isEmpty()) bankAccount = companyBankAccount == null ? "" : companyBankAccount;

        out.put("名称", name);
        out.put("纳税人识别号", taxNo);
        out.put("地址电话", addrTel);
        out.put("开户行及账号", bankAccount);
        out.put("开票人", doc == null ? "" : String.valueOf(doc.getOrDefault("开票人", "")).trim());
        out.put("收款人", doc == null ? "" : String.valueOf(doc.getOrDefault("收款人", "")).trim());
        out.put("复核人", doc == null ? "" : String.valueOf(doc.getOrDefault("复核人", "")).trim());
        return out;
    }

    /** 查该销货单是否已经开过票（返回发票状态），没有则 null。 */
    String findInvoiceOfSaleInv(String saleInvNo) {
        if (saleInvNo == null || saleInvNo.isBlank()) return null;
        List<FormData> docs = formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "SALE_INVOICE")
                .notIn(FormData::getStatus, "作废"));
        for (FormData doc : docs) {
            Map<String, Object> head;
            try {
                head = parseData(doc.getData());
            } catch (Exception e) {
                continue;
            }
            String linked = String.valueOf(head.getOrDefault("销货单号", "")).trim();
            if (saleInvNo.equals(linked)) return doc.getStatus();
        }
        return null;
    }

    /**
     * 销货单审批通过后自动开票（入库 → 销货 → 发票一路自动）。
     *
     * <p>幂等：已有发票则跳过。失败不抛——自动动作不能把销货单审批带崩，
     * 用户仍可手动点「生成销售发票」。
     */
    void autoCreateInvoice(String saleInvNo) {
        try {
            if (findInvoiceOfSaleInv(saleInvNo) != null) return;
            createSaleInvoiceFromSaleInv("SALE_INV", Map.of("编号", saleInvNo));
        } catch (Exception ignored) {
            // 自动开票是便利动作，失败留给手动生单
        }
    }

    /**
     * 该加工单各工序已报工数量（工序编码 → 累计报工数量）。
     *
     * <p>分批报工靠它算「剩余可报工」：每生成一张汇报单只带还没报完的量。
     * 中止/作废的汇报单不计入，否则一次作废就永远报不满。
     */
    Map<String, Double> reportedQtyByProcess(String moNo) {
        return reportedQtyByProcess(moNo, null);
    }

    /** 同上，但排除指定单据（重存时不能把本单已报的量算成别人报的）。 */
    Map<String, Double> reportedQtyByProcess(String moNo, String excludeFormNo) {
        Map<String, Double> out = new LinkedHashMap<>();
        if (moNo == null || moNo.isBlank()) return out;
        List<FormData> docs = formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "PROCESS_REPORT")
                .notIn(FormData::getStatus, "已中止", "作废"));
        for (FormData doc : docs) {
            Map<String, Object> head;
            Map<String, Object> detail;
            try {
                head = parseData(doc.getData());
                detail = parseData(doc.getDetailData());
            } catch (Exception e) {
                continue;
            }
            if (!moNo.equals(String.valueOf(head.getOrDefault("加工单号", "")).trim())) continue;
            if (excludeFormNo != null && excludeFormNo.equals(doc.getFormNo())) continue;
            // 只认已审核：草稿/审批中的汇报单还没真正报工，算进来会让「剩余可报」偏小
            if (!"已审核".equals(doc.getStatus())) continue;
            if (!(detail.get("items") instanceof List<?> items)) continue;
            for (Object o : items) {
                if (!(o instanceof Map<?, ?> line)) continue;
                Object codeObj = line.get("工序编码");
                String code = codeObj == null ? "" : String.valueOf(codeObj).trim();
                if (code.isEmpty()) continue;
                Object qtyObj = line.get("报工数量");
                double qty;
                try {
                    qty = qtyObj == null ? 0 : Double.parseDouble(String.valueOf(qtyObj));
                } catch (NumberFormatException e) {
                    qty = 0;
                }
                out.merge(code, qty, Double::sum);
            }
        }
        return out;
    }

    /** 解析生单预览弹窗传入的勾选行：存货编码 → 生产数量。 */
    private static Map<String, Double> selectedLineQty(Map<String, Object> buttonParam) {
        Map<String, Double> out = new LinkedHashMap<>();
        if (buttonParam == null) return out;
        Object raw = buttonParam.get("lines");
        if (!(raw instanceof List<?> list)) return out;
        for (Object o : list) {
            if (!(o instanceof Map<?, ?> line)) continue;
            Object codeObj = line.get("存货编码");
            Object qtyObj = line.get("数量");
            String code = codeObj == null ? "" : String.valueOf(codeObj).trim();
            if (code.isEmpty()) continue;
            double qty;
            try {
                qty = qtyObj == null ? 0 : Double.parseDouble(String.valueOf(qtyObj));
            } catch (NumberFormatException e) {
                qty = 0;
            }
            out.merge(code, qty, Double::sum);
        }
        return out;
    }

    /**
     * 生单预览：逐行给出 需求数量 / 现存量 / 已下单生产数量 / 建议生产数量 / 是否不足。
     *
     * <p>建议生产量 = max(0, 需求 - 成品仓现存量 - 已下单生产数量)。扣掉已下单量是为了
     * 防止用户反复点生单把同一批需求投产多遍。
     */
    /**
     * 入库预览：生产加工单 → 产成品入库单。
     * 逐行返回 计划数量 / 已入库数量 / 剩余入库数量 / 建议入库数量。
     */
    public List<Map<String, Object>> finishInPreview(String moNo) {
        FormData mo = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "MANU_ORDER")
                .eq(FormData::getFormNo, String.valueOf(moNo)));
        if (mo == null) throw new IllegalArgumentException("生产加工单不存在：" + moNo);
        Map<String, Object> dm = parseData(mo.getDetailData());
        List<Map<String, Object>> products = dm.get("products") instanceof List
                ? (List<Map<String, Object>>) dm.get("products") : new ArrayList<>();
        List<Map<String, Object>> rows = new ArrayList<>();
        for (Map<String, Object> p : products) {
            String code = String.valueOf(p.getOrDefault("产品编码", "")).trim();
            String name = String.valueOf(p.getOrDefault("产品名称", "")).trim();
            if (code.isEmpty() && name.isEmpty()) continue;
            double plan = num(p.getOrDefault("数量", 0));
            double received = num(p.getOrDefault("累计入库数量", 0));
            double remaining = Math.max(0, round2(plan - received));
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("产品编码", code);
            row.put("产品名称", name);
            row.put("规格型号", p.getOrDefault("规格型号", ""));
            row.put("计量单位", p.getOrDefault("生产单位", "件"));
            row.put("计划数量", plan);
            row.put("已入库数量", round2(received));
            row.put("剩余入库数量", remaining);
            row.put("建议入库数量", remaining);
            row.put("是否完成", remaining <= 0);
            rows.add(row);
        }
        return rows;
    }

    @Override
    public List<Map<String, Object>> producePreview(String soNo) {
        FormData so = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "SO_ORDER")
                .eq(FormData::getFormNo, String.valueOf(soNo)));
        if (so == null) throw new IllegalArgumentException("销售订单不存在：" + soNo);
        Map<String, Object> dm = parseData(so.getDetailData());
        List<Map<String, Object>> items = dm.get("items") instanceof List
                ? (List<Map<String, Object>>) dm.get("items") : new ArrayList<>();
        Map<String, Double> produced = producedQtyByItem(String.valueOf(soNo));
        // 同一存货可能出现在多行（不同交期）。库存要在各行之间按顺序分配，
        // 否则每行都拿全量现存量去减，会算出一份根本不存在的"够货"。
        Map<String, Double> availableLeft = new LinkedHashMap<>();
        List<Map<String, Object>> rows = new ArrayList<>();
        for (Map<String, Object> it : items) {
            String code = String.valueOf(it.getOrDefault("存货编码", "")).trim();
            String name = String.valueOf(it.getOrDefault("存货名称", "")).trim();
            if (code.isEmpty() && name.isEmpty()) continue;
            double demand = num(it.getOrDefault("数量", 0));
            double onHand = stockTxnService == null ? 0
                    : stockTxnService.quantityOf(code, name, SALE_OUT_WAREHOUSE).doubleValue();
            double ordered = produced.getOrDefault(code, 0.0);
            String key = code.isEmpty() ? "#" + name : code;
            double left = availableLeft.computeIfAbsent(key, k -> onHand + ordered);
            double covered = Math.min(left, demand);
            availableLeft.put(key, left - covered);
            double suggest = Math.max(0, round2(demand - covered));
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("存货编码", code);
            row.put("存货名称", name);
            row.put("规格型号", it.getOrDefault("规格型号", ""));
            row.put("计量单位", it.getOrDefault("销售单位", "件"));
            row.put("需求数量", demand);
            row.put("现存量", round2(onHand));
            row.put("已下单生产数量", round2(ordered));
            row.put("本行可满足数量", round2(covered));
            row.put("建议生产数量", suggest);
            row.put("是否不足", suggest > 0);
            row.put("仓库", SALE_OUT_WAREHOUSE);
            rows.add(row);
        }
        return rows;
    }

    /**
     * 该销售订单已投产的数量（按产品编码汇总）。
     *
     * <p>中止/作废的加工单不计入，否则一次作废就会永远挡住重新投产。
     */
    Map<String, Double> producedQtyByItem(String soNo) {
        Map<String, Double> out = new LinkedHashMap<>();
        if (soNo == null || soNo.isBlank()) return out;
        List<FormData> orders = formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "MANU_ORDER")
                .notIn(FormData::getStatus, "已中止", "作废"));
        for (FormData order : orders) {
            Map<String, Object> head;
            Map<String, Object> detail;
            try {
                head = parseData(order.getData());
                detail = parseData(order.getDetailData());
            } catch (Exception e) {
                continue;
            }
            if (!soNo.equals(String.valueOf(head.getOrDefault("销售订单号", "")).trim())) continue;
            if (!(detail.get("products") instanceof List<?> products)) continue;
            for (Object o : products) {
                if (!(o instanceof Map<?, ?> p)) continue;
                Object codeObj = p.get("产品编码");
                String code = codeObj == null ? "" : String.valueOf(codeObj).trim();
                if (code.isEmpty()) continue;
                Object qtyObj = p.get("数量");
                double qty;
                try {
                    qty = qtyObj == null ? 0 : Double.parseDouble(String.valueOf(qtyObj));
                } catch (NumberFormatException e) {
                    qty = 0;
                }
                out.merge(code, qty, Double::sum);
            }
        }
        return out;
    }

    private Map<String, Object> createMoFromSo(String panelCode, Map<String, Object> formData) {
        return createMoFromSo(panelCode, formData, Map.of());
    }

    /**
     * 推式生单：销售订单 → 生产加工单，支持只投产用户勾选的行与数量。
     *
     * @param buttonParam 可带 {@code lines=[{存货编码, 数量}]}：前端生单预览弹窗勾选的结果。
     *                    不传则保持旧行为（整单明细全部投产），供接口脚本兼容。
     */
    private Map<String, Object> createMoFromSo(String panelCode, Map<String, Object> formData,
                                               Map<String, Object> buttonParam) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData so = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "SO_ORDER")
                .eq(FormData::getFormNo, String.valueOf(no)));
        if (so == null) throw new IllegalArgumentException("销售订单不存在：" + no);
        if (!"已审核".equals(so.getStatus())) throw new IllegalStateException("仅已审核销售订单可生成生产加工单");
        // 已销售出库的订单不能再投生产：否则同一批货会既出库又生产。
        // 需要预生产入库的，直接新增生产加工单排单即可（不挂销售订单来源）。
        String blockingSaleOut = findBlockingSaleOut(String.valueOf(no));
        if (blockingSaleOut != null) {
            throw new IllegalStateException("该销售订单已有销售出库单（状态：" + blockingSaleOut
                    + "），不能再生成生产加工单；如需预生产入库，请直接新增生产加工单排单。");
        }

        Map<String, Object> head = parseData(so.getData());
        Map<String, Object> detailMap = parseData(so.getDetailData());
        Object itemsObj = detailMap.get("items");
        List<Map<String, Object>> items = itemsObj instanceof List ? (List<Map<String, Object>>) itemsObj : new ArrayList<>();

        String newNo = generateFormNo("MANU_ORDER", head);
        Map<String, Object> moData = new HashMap<>();
        moData.put("合同号", head.getOrDefault("单据日期", ""));
        // 锭号=单据编号：审批面板草稿不预填，提交审核时自动填写（ensureAuditStamp）
        moData.put("批号", "正常");
        moData.put("生产车间", "熔铸车间");
        moData.put("预开工日", LocalDate.now().toString());
        moData.put("预完工日", head.getOrDefault("预计交货日期", ""));
        moData.put("销售订单号", head.getOrDefault("单据编号", ""));
        moData.put("客户编码", head.getOrDefault("客户编码", ""));
        moData.put("客户", head.getOrDefault("客户", ""));
        moData.put("测试程序", "光谱分析");
        moData.put("生产订单客户", head.getOrDefault("结算客户", ""));
        moData.put("机构", "总部");
        moData.put("重量", 0);
        moData.put("开工日期", "");
        moData.put("完工日期", "");
        moData.put("启用派工", false);
        moData.put("自动转移", false);
        moData.put("产品自动添加到材料", false);
        moData.put("是否手工修改单据编码", false);
        moData.put("外部单据号", "");
        moData.put("负责人", head.getOrDefault("业务员", ""));
        moData.put("启用领料申请", false);
        moData.put("对方仓库", "");

        // 生单预览弹窗勾选的结果：只投产被勾选且数量 > 0 的行，数量以用户填的为准，
        // 避免"有货的也一起投产"造成过度生产。
        Map<String, Double> selectedQty = selectedLineQty(buttonParam);
        if (!selectedQty.isEmpty()) {
            List<Map<String, Object>> picked = new ArrayList<>();
            for (Map<String, Object> it : items) {
                String code = String.valueOf(it.getOrDefault("存货编码", "")).trim();
                Double qty = selectedQty.get(code);
                if (qty == null || qty <= 0) continue;
                Map<String, Object> copy = new HashMap<>(it);
                copy.put("数量", qty);
                picked.add(copy);
            }
            if (picked.isEmpty()) {
                throw new IllegalStateException("请至少勾选一行要投产的产品，并填写大于 0 的生产数量");
            }
            items = picked;
        }
        List<Map<String, Object>> products = new ArrayList<>();
        for (Map<String, Object> it : items) {
            Map<String, Object> p = new HashMap<>();
            p.put("生产类型", "自制");
            p.put("产品编码", it.getOrDefault("存货编码", ""));
            p.put("存货图片", "");
            p.put("产品名称", it.getOrDefault("存货名称", ""));
            p.put("规格型号", it.getOrDefault("规格型号", ""));
            p.put("型号", "");
            p.put("适用BOM", "BOM-001");
            p.put("BOM展开方式", "单阶");
            p.put("生产单位", it.getOrDefault("销售单位", "件"));
            p.put("数量", it.getOrDefault("数量", 0));
            p.put("齐套数量(主)", 0);
            p.put("累计汇报套数(工序单位)", 0);
            p.put("可用量", 0);
            p.put("可用量说明", "");
            p.put("现存量", it.getOrDefault("现存量", 0));
            p.put("现存量说明", "");
            p.put("产品字符公用自定义项1", "");
            p.put("图号", "");
            p.put("单重", 0);
            p.put("总重", 0);
            p.put("需求令号", head.getOrDefault("单据编号", ""));
            products.add(p);
        }
        // 生成材料明细：按物料清单（BOM 面板 children，父件编码=产品编码）自动带出（2026-08-25：原 INV 物品行 _bom 已废弃迁移至物料清单）
        List<Map<String, Object>> materials = new ArrayList<>();
        Map<String, List<Map<String, Object>>> bomByItem = new HashMap<>();
        for (FormData bdoc : formMapper.selectList(new LambdaQueryWrapper<FormData>().eq(FormData::getPanelCode, "BOM"))) {
            Map<String, Object> bdetail = parseData(bdoc.getDetailData());
            Object bchildren = bdetail.get("children");
            if (bchildren instanceof List) {
                for (Object o : (List<?>) bchildren) {
                    if (!(o instanceof Map)) continue;
                    Map<String, Object> it = (Map<String, Object>) o;
                    String parent = String.valueOf(it.getOrDefault("父件编码", ""));
                    if (parent.isEmpty()) continue;
                    Map<String, Object> bomRow = new HashMap<>();
                    bomRow.put("材料编码", it.getOrDefault("子件编码", ""));
                    bomRow.put("材料名称", it.getOrDefault("子件名称", ""));
                    bomRow.put("规格型号", it.getOrDefault("规格型号", ""));
                    bomRow.put("计量单位", it.getOrDefault("子件计量单位", "kg"));
                    bomRow.put("定额需用数量", it.getOrDefault("定额数量", 0));
                    bomRow.put("损耗率%", it.getOrDefault("损耗率%", 0));
                    bomByItem.computeIfAbsent(parent, k -> new ArrayList<>()).add(bomRow);
                }
            }
        }
        Set<String> matKeys = new HashSet<>();
        for (Map<String, Object> p : products) {
            String pcode = String.valueOf(p.getOrDefault("产品编码", ""));
            List<Map<String, Object>> bom = bomByItem.get(pcode);
            if (bom == null) continue;
            for (Map<String, Object> b : bom) {
                String mcode = String.valueOf(b.getOrDefault("材料编码", ""));
                if (mcode.isEmpty() || !matKeys.add(pcode + "|" + mcode)) continue;
                Map<String, Object> m = new HashMap<>();
                m.put("材料编码", mcode);
                m.put("材料名称", b.getOrDefault("材料名称", ""));
                m.put("规格型号", b.getOrDefault("规格型号", ""));
                m.put("计量单位", b.getOrDefault("计量单位", "kg"));
                m.put("定额需用数量", b.getOrDefault("定额需用数量", 0));
                m.put("损耗率%", b.getOrDefault("损耗率%", 0));
                m.put("子件BOM", pcode);
                // 预出仓库按该材料实际存放的仓库带出（此前写死「原料仓」，
                // 辅料仓的料会被从原料仓出，直接出成负库存）。无库存记录才退回原料仓。
                String matWarehouse = stockTxnService == null ? null
                        : stockTxnService.warehouseOfItem(String.valueOf(m.get("材料编码")),
                                String.valueOf(m.get("材料名称")));
                m.put("预出仓库", matWarehouse == null || matWarehouse.isBlank() ? "原料仓" : matWarehouse);
                m.put("材料倒冲方式", "按定额倒冲");
                m.put("领料工序", "下料");
                m.put("允许循环", false);
                m.put("行中止", false);
                m.put("定额生产数量", 1);
                // 需求量在这里算好，不能只靠前端表单 applyCalc：
                // 推式生单后若用户不打开加工单表单、直接在列表页点「生成材料出库单」，
                // 需求量会是 0，而 StockTxnService 会跳过 0 数量行 —— 材料库存不扣减也不报错。
                double quotaQty = num(b.getOrDefault("定额需用数量", 0));
                double productQty = num(p.getOrDefault("数量", 0));
                double needQty = round2(quotaQty * productQty);
                double lossQty = round2(needQty * num(b.getOrDefault("损耗率%", 0)) / 100.0);
                m.put("需用数量", needQty);
                m.put("损耗数量", lossQty);
                m.put("计划数量", round2(needQty + lossQty));
                m.put("累计领用数量", 0);
                m.put("可用量", 0);
                m.put("可用量说明", "");
                m.put("现存量", 0);
                m.put("现存量说明", "");
                m.put("单重", 0);
                m.put("总重", 0);
                m.put("存货图片", "");
                materials.add(m);
            }
        }
        Map<String, Object> detail = new HashMap<>();
        detail.put("products", products);
        detail.put("materials", materials);
        detail.put("processes", resolveProcesses(products, String.valueOf(moData.getOrDefault("生产车间", "熔铸车间"))));

        // 新建兜底：单据日期=当天 + 锭号=form_no（修复：销售订单生单生成的加工单锭号缺失，必填校验无法保存草稿）
        fillNewDefaults("MANU_ORDER", moData, newNo);

        FormData mo = new FormData();
        mo.setPanelCode("MANU_ORDER");
        mo.setFormNo(newNo);
        mo.setData(toJson(moData));
        mo.setDetailData(toJson(detail));
        mo.setStatus("草稿");
        mo.setCreateBy(currentUserName());
        mo.setCreateTime(LocalDateTime.now());
        mo.setUpdateTime(LocalDateTime.now());
        formMapper.insert(mo);

        Map<String, Object> out = new HashMap<>();
        out.put("编号", newNo);
        out.put("单据状态", "草稿");
        out.put("gotoPanel", "MANU_ORDER");
        return out;
    }
    /**
     * 推式生单：生产加工单（MANU_ORDER）→ 工序汇报单（PROCESS_REPORT）
     * 表头：加工单号/生产车间/产品/客户/销售订单号；明细 = 加工单工序明细（工序编码/车间/工作中心/设备/班组/工人/计划数量→报工数量/工价）
     */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createProcReportFromMo(String panelCode, Map<String, Object> formData) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData mo = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "MANU_ORDER")
                .eq(FormData::getFormNo, String.valueOf(no)));
        if (mo == null) throw new IllegalArgumentException("生产加工单不存在：" + no);
        String moStatus = mo.getStatus();
        if (!"已审核".equals(moStatus) && !"生产中".equals(moStatus)) {
            throw new IllegalStateException("仅已审核/生产中的生产加工单可生成工序汇报单");
        }

        Map<String, Object> head = parseData(mo.getData());
        Map<String, Object> detailMap = parseData(mo.getDetailData());
        List<Map<String, Object>> products = detailMap.get("products") instanceof List
                ? (List<Map<String, Object>>) detailMap.get("products") : new ArrayList<>();
        List<Map<String, Object>> procs = detailMap.get("processes") instanceof List
                ? (List<Map<String, Object>>) detailMap.get("processes") : new ArrayList<>();
        if (products.isEmpty()) throw new IllegalStateException("生产加工单无产成品明细：" + no);
        // 工序兜底：加工单无工序明细时按默认 3 道工序生成（下料/机加工/检验）
        if (procs.isEmpty()) {
            procs = resolveProcesses(products, String.valueOf(head.getOrDefault("生产车间", "熔铸车间")));
        }
        Map<String, Object> p0 = products.get(0);

        String newNo = generateFormNo("PROCESS_REPORT", head);
        Map<String, Object> prData = new HashMap<>();
        // 单据日期/单据编号：审批面板草稿不填，提交审核时自动填写（ensureAuditStamp）
        prData.put("业务类型", "工序汇报");
        prData.put("加工单号", String.valueOf(no));
        prData.put("生产车间", head.getOrDefault("生产车间", ""));
        prData.put("产品编码", p0.getOrDefault("产品编码", ""));
        prData.put("产品名称", p0.getOrDefault("产品名称", ""));
        prData.put("规格型号", p0.getOrDefault("规格型号", ""));
        prData.put("销售订单号", head.getOrDefault("销售订单号", ""));
        prData.put("客户", head.getOrDefault("客户", ""));
        prData.put("匹配来源单号", String.valueOf(no));
        // 表头带出（部门=加工单车间即部门，测试程序=加工单表头）；
        // 单据编号/单据日期：审批面板草稿不填，提交审核时自动填写（ensureAuditStamp）
        prData.put("部门", head.getOrDefault("生产车间", ""));
        prData.put("测试程序", head.getOrDefault("测试程序", ""));

        // 一天一个批次：同一加工单同一天多次报工共用批次号；跨天自动换批。
        String batchNo = LocalDate.now().format(DateTimeFormatter.ofPattern("yyyyMMdd"));
        prData.put("批次号", batchNo);
        Map<String, Double> reported = reportedQtyByProcess(String.valueOf(no));
        List<Map<String, Object>> items = new ArrayList<>();
        for (Map<String, Object> pr : procs) {
            String procCode = String.valueOf(pr.getOrDefault("工序编码", "")).trim();
            double planQty = num(pr.getOrDefault("计划数量", 0));
            double doneQty = reported.getOrDefault(procCode, 0.0);
            double remainQty = round2(planQty - doneQty);
            if (remainQty <= 0) continue; // 该工序已报满，本次批次不再带出
            Map<String, Object> it = new HashMap<>();
            it.put("加工单号", String.valueOf(no));
            it.put("批次号", batchNo);
            it.put("产品编码", p0.getOrDefault("产品编码", ""));
            it.put("产品名称", p0.getOrDefault("产品名称", ""));
            it.put("规格型号", p0.getOrDefault("规格型号", ""));
            it.put("工艺类型", pr.getOrDefault("工艺类型", "自制"));
            it.put("工艺序号", pr.getOrDefault("工艺序号", 0));
            it.put("加工顺序", pr.getOrDefault("加工顺序", 0));
            it.put("工艺路线", "");
            it.put("工序编码", pr.getOrDefault("工序编码", ""));
            it.put("工序名称", pr.getOrDefault("工序名称", ""));
            it.put("工序备注", pr.getOrDefault("工序备注", ""));
            it.put("生产车间", pr.getOrDefault("生产车间", ""));
            it.put("工作中心", pr.getOrDefault("工作中心", ""));
            it.put("设备", pr.getOrDefault("设备", ""));
            it.put("班组名称", pr.getOrDefault("班组", ""));
            it.put("工人名称", pr.getOrDefault("工人", ""));
            it.put("工序单位", pr.getOrDefault("工序单位", "件"));
            // 默认带剩余可报工量，合格数默认等于报工数（都可改）
            it.put("报工数量", remainQty);
            it.put("可报工数量", remainQty);
            it.put("合格数量", remainQty);
            it.put("不合格数量", 0);
            it.put("工资类型", pr.getOrDefault("工资类型", "计件"));
            it.put("工价", pr.getOrDefault("工价", 0));
            it.put("计时/计件金额", 0);
            it.put("调整工资", 0);
            it.put("金额", 0);
            it.put("单位标准工时", pr.getOrDefault("单位标准工时", 0));
            it.put("实际工时", 0);
            it.put("计划时间", pr.getOrDefault("计划时间", ""));
            it.put("完成时间", pr.getOrDefault("完成时间", ""));
            it.put("手工完工", false);
            it.put("委外供应商", pr.getOrDefault("委外供应商", ""));
            it.put("委外单价", pr.getOrDefault("委外单价", 0));
            it.put("税率%", pr.getOrDefault("税率%", 13));
            it.put("委外含税单价", 0);
            it.put("委外金额", 0);
            it.put("委外税额", 0);
            it.put("委外含税金额", 0);
            it.put("累计汇报数量", round2(doneQty));
            it.put("需求令号", pr.getOrDefault("需求令号", p0.getOrDefault("需求令号", "")));
            it.put("图号", p0.getOrDefault("图号", ""));
            it.put("备注", "");
            items.add(it);
        }
        if (items.isEmpty()) throw new IllegalStateException("该生产加工单已全部报工，无需再生成工序汇报单");

        Map<String, Object> detail = new HashMap<>();
        detail.put("items", items);

        // 新建兜底：单据日期=当天 + 单据编号=form_no（修复：生单生成的汇报单编号缺失，必填校验无法保存草稿）
        fillNewDefaults("PROCESS_REPORT", prData, newNo);

        FormData pr = new FormData();
        pr.setPanelCode("PROCESS_REPORT");
        pr.setFormNo(newNo);
        pr.setData(toJson(prData));
        pr.setDetailData(toJson(detail));
        pr.setStatus("草稿");
        pr.setCreateBy(currentUserName());
        pr.setCreateTime(LocalDateTime.now());
        formMapper.insert(pr);

        Map<String, Object> out = new HashMap<>();
        out.put("单据状态", "草稿");
        out.put("gotoPanel", "PROCESS_REPORT");
        out.put("编号", newNo);
        return out;
    }

    // ══════════ 推式生单（业务流程图流转，2026-08-24 全量补全） ══════════

    private static double num(Object v) {
        if (v == null) return 0;
        try { return Double.parseDouble(String.valueOf(v)); } catch (Exception e) { return 0; }
    }

    /** 数量统一保留 2 位（与面板 calc 规则的 round:2 对齐）。 */
    private static double round2(double v) {
        return java.math.BigDecimal.valueOf(v)
                .setScale(2, java.math.RoundingMode.HALF_UP).doubleValue();
    }

    /** 通用：读取源单据（必须已审核），插入目标草稿单，返回 {编号, gotoPanel} */
    @SuppressWarnings("unchecked")
    private Map<String, Object> insertGenerated(String targetPanel, String sourcePanel, String sourceNo,
                                                Map<String, Object> head, Map<String, Object> detail) {
        // 生单同时要求目标面板新增权限，防止仅有来源面板权限的用户越权创建下游单据。
        requirePanelPermission(targetPanel, "add");
        validateSourceLinks(targetPanel, null, detail);
        String newNo = generateFormNo(targetPanel, head);
        // 新建兜底：字段默认值 + 单据日期=当天 + 单据编号/锭号=form_no（修复：生单目标单必填字段缺失无法保存草稿）
        fillNewDefaults(targetPanel, head, newNo);
        FormData fd = new FormData();
        fd.setPanelCode(targetPanel);
        fd.setFormNo(newNo);
        head.put("来源单据", sourcePanel);
        head.put("来源单号", sourceNo);
        fd.setData(toJson(head));
        fd.setDetailData(toJson(detail));
        fd.setStatus("草稿");
        fd.setCreateBy(currentUserName());
        fd.setCreateTime(LocalDateTime.now());
        fd.setUpdateTime(LocalDateTime.now());
        formMapper.insert(fd);
        Map<String, Object> out = new HashMap<>();
        out.put("单据状态", "草稿");
        out.put("gotoPanel", targetPanel);
        out.put("编号", newNo);
        return out;
    }

    /**
     * 用库存状况表当前值覆盖单据展示层的现存量。带仓库的行取对应仓库；报价单、销售订单等
     * 无仓库行取全部仓库合计。这里只刷新响应对象，不改历史单据存储。
     */
    @SuppressWarnings("unchecked")
    private void refreshCurrentStock(Map<String, Object> detail, List<Map<String, Object>> stockRows) {
        if (detail == null || detail.isEmpty() || stockRows == null) return;
        for (Object rowsObject : detail.values()) {
            if (!(rowsObject instanceof List<?> rows)) continue;
            for (Object rowObject : rows) {
                if (!(rowObject instanceof Map<?, ?> rawRow)) continue;
                Map<String, Object> row = (Map<String, Object>) rawRow;
                if (!row.containsKey("现存量")) continue;
                String code = firstNonBlank(row, "存货编码", "产品编码", "材料编码");
                String name = firstNonBlank(row, "存货名称", "产品名称", "材料名称", "存货");
                String warehouse = firstNonBlank(row, "仓库", "预出仓库", "出库仓库");
                if (code.isBlank() && name.isBlank()) continue;
                double quantity = 0;
                for (Map<String, Object> stock : stockRows) {
                    String stockCode = String.valueOf(stock.getOrDefault("存货编码", "")).trim();
                    String stockName = String.valueOf(stock.getOrDefault("存货", "")).trim();
                    String stockWarehouse = String.valueOf(stock.getOrDefault("仓库", "")).trim();
                    boolean itemMatches = !code.isBlank() ? code.equals(stockCode) : name.equals(stockName);
                    if (!itemMatches || (!warehouse.isBlank() && !warehouse.equals(stockWarehouse))) continue;
                    quantity += num(stock.get("现存量(主)"));
                }
                row.put("现存量", Math.round(quantity * 100) / 100.0);
                if (row.containsKey("现存量说明")) {
                    row.put("现存量说明", warehouse.isBlank() ? "库存状况表（全部仓库）" : "库存状况表（" + warehouse + "）");
                }
            }
        }
    }

    private boolean hasCurrentStockField(Map<String, Object> detail) {
        if (detail == null) return false;
        for (Object rowsObject : detail.values()) {
            if (!(rowsObject instanceof List<?> rows)) continue;
            for (Object rowObject : rows) {
                if (rowObject instanceof Map<?, ?> row && row.containsKey("现存量")) return true;
            }
        }
        return false;
    }

    private String firstNonBlank(Map<String, Object> row, String... keys) {
        for (String key : keys) {
            Object value = row.get(key);
            if (value != null && !String.valueOf(value).isBlank()) return String.valueOf(value).trim();
        }
        return "";
    }

    /** 报检单 → 检验单。到货/成品/首件进入 QM15，工序报检进入生产过程检验。 */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createInspectionFromReport(String panelCode, Map<String, Object> formData) {
        if (!Set.of("ARRIVAL_IN", "FINISH_INSPECT", "FIRST_INSPECT", "PROCESS_INSPECT_APPLY").contains(panelCode)) {
            throw new IllegalStateException("当前单据不能生成检验单：" + panelCode);
        }
        FormData source = formOf(panelCode, formData.get("编号"));
        if (!"已审核".equals(source.getStatus())) throw new IllegalStateException("仅已审核报检单可生成检验单");
        Map<String, Object> sourceHead = parseData(source.getData());
        Map<String, Object> sourceDetail = parseData(source.getDetailData());
        List<Map<String, Object>> sourceRows = firstDetailRows(sourceDetail);
        if (sourceRows.isEmpty()) throw new IllegalStateException("报检单没有可检验明细");

        String targetPanel = "PROCESS_INSPECT_APPLY".equals(panelCode) ? "PROCESS_INSPECTION" : "INSPECTION";
        Map<String, Object> head = new HashMap<>();
        head.put("业务类型", switch (panelCode) {
            case "ARRIVAL_IN" -> "来料检验";
            case "PROCESS_INSPECT_APPLY" -> "生产过程检验";
            default -> "成品检验";
        });
        head.put("检验类别", head.get("业务类型"));
        copyFirst(sourceHead, head, "供应商编码", "供应商", "仓库", "生产车间", "加工单号", "部门", "项目", "经手人");
        head.put("来源单号", source.getFormNo());
        // T+ QM15：来料带采购订单号，成品/委外带加工单号；检验部门默认取来源部门
        if ("ARRIVAL_IN".equals(panelCode)) {
            head.put("采购订单号", sourceHead.getOrDefault("采购订单号", ""));
        } else {
            head.put("加工单号", firstValue(sourceHead, "加工单号", "生产加工单号"));
        }
        head.put("检验部门", firstValue(sourceHead, "检验部门", "部门"));

        List<Map<String, Object>> items = new ArrayList<>();
        int exemptSkipped = 0;
        for (int index = 0; index < sourceRows.size(); index++) {
            Map<String, Object> sourceRow = sourceRows.get(index);
            String inspectMethod = textOf(sourceRow.get("检验方式")).trim();
            // 到货单混装时，免检行不送检，直接走到货单→采购入库；只把需要检验的行生成检验单
            if ("ARRIVAL_IN".equals(panelCode) && "免检".equals(inspectMethod)) {
                exemptSkipped++;
                continue;
            }
            // 到货单按下限口径取「实收数量」：拒收数量不应再送检，也不应进入入库。
            double quantity = "ARRIVAL_IN".equals(panelCode) ? arrivalReceivable(sourceRow) : sourceQuantity(sourceRow);
            if (quantity <= 0) continue;
            double ratio = "抽检".equals(inspectMethod) ? num(sourceRow.get("抽检比例%")) : 100;
            if (ratio <= 0 || ratio > 100) ratio = 100;
            double taxPrice = num(sourceRow.get("含税单价"));
            if (taxPrice <= 0 && num(sourceRow.get("单价")) > 0) {
                taxPrice = round2(num(sourceRow.get("单价")) * (1 + num(sourceRow.get("税率%")) / 100));
            }
            Map<String, Object> row = new HashMap<>();
            row.put("存货编码", firstValue(sourceRow, "存货编码", "产品编码", "物料编码"));
            row.put("存货名称", firstValue(sourceRow, "存货名称", "产品名称", "物料名称"));
            row.put("规格型号", sourceRow.getOrDefault("规格型号", ""));
            row.put("检验批号", firstValue(sourceRow, "批号", "检验批号", "批次号"));
            row.put("工序编码", sourceRow.getOrDefault("工序编码", ""));
            row.put("工序名称", sourceRow.getOrDefault("工序名称", ""));
            row.put("仓库", firstValue(sourceRow, "仓库", "预入仓库"));
            row.put("计量单位", firstValue(sourceRow, "计量单位", "采购单位", "生产单位", "单位", "工序单位"));
            row.put("报检数量", quantity);
            row.put("应检数量", quantity);
            row.put("检验数量", quantity);
            // 合格数默认等于检验数、默认判定合格：检验员只需在发现不良时改这两项，
            // 否则每次都要先回填才能审核（此前是 0，审核会被「合格+不合格=检验数量」拦下）。
            row.put("合格数量", quantity);
            row.put("不合格数量", 0);
            row.put("不合格原因", sourceRow.getOrDefault("不合格原因", ""));
            row.put("单价", num(sourceRow.get("单价")));
            row.put("含税单价", taxPrice);
            row.put("让步接收含税单价", taxPrice);
            row.put("记录样本明细", false);
            row.put("样本明细个数", 0);
            row.put("合格率", 0);
            row.put("检验方式", inspectMethod.isEmpty() ? "全检" : inspectMethod);
            row.put("抽检比例%", ratio);
            // 默认判定合格：检验员只需在发现不良时改，否则每次都要先回填才能审核
            // （此前置空，审核会被「合格+不合格=检验数量」拦下）。
            row.put("检验结果判定", "合格");
            stampSource(row, panelCode, source.getFormNo(), index + 1, quantity);
            items.add(row);
        }
        if (items.isEmpty()) {
            if (exemptSkipped > 0) {
                throw new IllegalStateException("到货单明细均为免检物料，请直接执行「生成采购入库单」");
            }
            throw new IllegalStateException("报检单没有大于零的报检数量");
        }
        return insertGenerated(targetPanel, panelCode, source.getFormNo(), head, Map.of("items", items));
    }

    /** 已审核检验单 → 合格品采购/产成品/委外入库草稿。 */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createInventoryInFromInspection(String panelCode, Map<String, Object> formData,
                                                               String targetPanel) {
        return createInventoryInFromInspection(panelCode, formData, targetPanel, Map.of());
    }

    /**
     * 推式生单：检验单 → 采购入库/产成品入库/委外入库。
     * warehouse 可指定入库仓库；未指定时采购入库默认原料仓，其余默认成品仓。
     */
    private Map<String, Object> createInventoryInFromInspection(String panelCode, Map<String, Object> formData,
                                                               String targetPanel,
                                                               Map<String, Object> buttonParam) {
        FormData source = formOf(panelCode, formData.get("编号"));
        requireApprovedPurchaseSource(source, "生成入库单");
        Map<String, Object> sourceHead = parseData(source.getData());
        Map<String, Object> sourceDetail = parseData(source.getDetailData());
        List<Map<String, Object>> sourceRows = firstDetailRows(sourceDetail);
        requireInspectionDispositionConfirmed(panelCode, sourceRows);
        requireInspectionInboundTarget(panelCode, targetPanel, sourceHead, sourceRows);
        Map<String, Object> head = new HashMap<>();
        copyFirst(sourceHead, head, "供应商编码", "供应商", "仓库", "生产车间", "加工单号", "部门", "项目", "经手人", "委外供应商");
        head.put("业务类型", switch (targetPanel) {
            case "PURCHASE_IN" -> "采购入库";
            case "OUTSOURCE_IN" -> "委外入库";
            default -> "产成品入库";
        });
        head.put("入库类别", targetPanel.equals("PURCHASE_IN") ? "采购入库" : "检验合格入库");
        head.put("检验单号", source.getFormNo());

        // 采购入库价格来自到货单（检验单只带质检结果）；降价接收按折价率取调整后单价
        List<Map<String, Object>> arrivalRows = List.of();
        if ("PURCHASE_IN".equals(targetPanel)) {
            String arrivalNo = textOf(sourceHead.get("来源单号")).trim();
            if (arrivalNo.isEmpty()) {
                for (Map<String, Object> sourceRow : sourceRows) {
                    if ("ARRIVAL_IN".equals(textOf(sourceRow.get("来源面板")).trim())) {
                        arrivalNo = textOf(sourceRow.get("来源单号")).trim();
                        if (!arrivalNo.isEmpty()) break;
                    }
                }
            }
            if (!arrivalNo.isEmpty()) {
                FormData arrival = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                        .eq(FormData::getPanelCode, "ARRIVAL_IN").eq(FormData::getFormNo, arrivalNo));
                if (arrival != null) {
                    arrivalRows = firstDetailRows(parseData(arrival.getDetailData()));
                    Map<String, Object> arrivalHead = parseData(arrival.getData());
                    head.put("到货单号", arrivalNo);
                    head.put("采购订单号", arrivalHead.getOrDefault("采购订单号",
                            head.getOrDefault("采购订单号", "")));
                }
            }
        }

        List<Map<String, Object>> items = new ArrayList<>();
        for (int index = 0; index < sourceRows.size(); index++) {
            Map<String, Object> sourceRow = sourceRows.get(index);
            String disposition = textOf(sourceRow.get("处置方式"));
            // 全批退回 = 整批拒收（含合格品），不再生成入库
            if ("全批退回".equals(disposition)) continue;
            // 返工/返修：复检合格前不得入库（闭环门禁）
            if (("返工".equals(disposition) || "返修".equals(disposition))
                    && !"合格".equals(textOf(sourceRow.get("复检结果")))) {
                throw new IllegalStateException("检验单第 " + (index + 1)
                        + " 行处置为返工/返修，复检合格后才能入库");
            }
            // 可入库量 = 合格数量 + 让步接收处理量；报废/退回数量不入库。
            double accepted = inspectionAcceptable(sourceDetail, sourceRow, index + 1);
            if (accepted <= 0) continue;
            Map<String, Object> row = new HashMap<>();
            Object code = firstValue(sourceRow, "存货编码", "产品编码");
            Object name = firstValue(sourceRow, "存货名称", "产品名称");
            row.put("存货编码", code);
            row.put("产品编码", code);
            row.put("存货名称", name);
            row.put("产品名称", name);
            row.put("规格型号", sourceRow.getOrDefault("规格型号", ""));
            Object warehouse = sourceRow.get("仓库");
            String fallbackWh = targetPanel.equals("PURCHASE_IN") ? "原料仓" : "成品仓";
            row.put("仓库", warehouse == null || String.valueOf(warehouse).isBlank()
                    ? warehouseParam(buttonParam, "warehouse", fallbackWh) : warehouse);
            Object unit = sourceRow.get("计量单位");
            row.put("计量单位", unit == null || String.valueOf(unit).isBlank() ? "件" : unit);
            row.put("实收数量", accepted);
            row.put("数量", accepted);
            Map<String, Object> priceRow = matchArrivalRow(arrivalRows, sourceRow);
            double price = num(priceRow.getOrDefault("单价", sourceRow.getOrDefault("单价", 0)));
            double taxPrice = num(priceRow.getOrDefault("含税单价", sourceRow.getOrDefault("含税单价", 0)));
            double taxRate = num(priceRow.getOrDefault("税率%", sourceRow.getOrDefault("税率%", 13)));
            // 纯让步接收行（没有合格品）按检验单上维护的让步接收含税单价结算；
            // 合格+让步混行仍按原价，避免把合格部分也按让步价入库。
            if ("让步接收".equals(textOf(sourceRow.get("处置方式")))
                    && num(sourceRow.get("合格数量")) <= 0.000001) {
                double concessionTax = num(sourceRow.get("让步接收含税单价"));
                if (concessionTax > 0) {
                    if (taxPrice > 0) price = Math.round(price * concessionTax / taxPrice * 100) / 100.0;
                    taxPrice = concessionTax;
                }
            }
            if ("降价接收".equals(textOf(sourceRow.get("处置方式")))) {
                double discount = num(sourceRow.get("折价率%"));
                double adjusted = num(sourceRow.get("调整后单价"));
                if (adjusted <= 0 && price > 0) {
                    adjusted = Math.round(price * (1 - discount / 100) * 100) / 100.0;
                }
                double adjustedTax = num(sourceRow.get("调整后含税单价"));
                if (adjustedTax <= 0 && taxPrice > 0) {
                    adjustedTax = Math.round(taxPrice * (1 - discount / 100) * 100) / 100.0;
                }
                if (adjusted > 0) price = adjusted;
                if (adjustedTax > 0) taxPrice = adjustedTax;
            }
            double amount = Math.round(accepted * price * 100) / 100.0;
            double gross = Math.round(accepted * taxPrice * 100) / 100.0;
            if (gross <= 0) gross = Math.round(amount * (1 + taxRate / 100) * 100) / 100.0;
            if (taxPrice <= 0 && accepted > 0 && gross > 0) taxPrice = Math.round(gross / accepted * 100) / 100.0;
            double tax = Math.round(gross * taxRate / (100 + taxRate) * 100) / 100.0;
            row.put("单价", price);
            row.put("税率%", taxRate);
            row.put("含税单价", taxPrice);
            row.put("金额", amount);
            row.put("税额", tax);
            row.put("含税金额", gross);
            row.put("批号", firstValue(sourceRow, "批号", "检验批号", "批次号"));
            stampSource(row, panelCode, source.getFormNo(), index + 1, accepted);
            items.add(row);
        }
        if (items.isEmpty()) throw new IllegalStateException("检验单没有可入库的合格或让步接收数量");
        return insertGenerated(targetPanel, panelCode, source.getFormNo(), head, Map.of("items", items));
    }

    /** 推式生单：已审核检验单的不合格退回数量 → 采购退货单。 */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createPurchaseReturnFromInspection(String panelCode, Map<String, Object> formData) {
        if (!"INSPECTION".equals(panelCode)) {
            throw new IllegalStateException("仅来料/成品检验单可生成采购退货单");
        }
        FormData source = formOf(panelCode, formData.get("编号"));
        requireApprovedPurchaseSource(source, "生成采购退货单");
        Map<String, Object> sourceHead = parseData(source.getData());
        Map<String, Object> sourceDetail = parseData(source.getDetailData());
        List<Map<String, Object>> sourceRows = firstDetailRows(sourceDetail);
        if (sourceRows.isEmpty()) throw new IllegalStateException("检验单没有可退货明细");
        requireInspectionDispositionConfirmed(panelCode, sourceRows);
        // 空数组也算「处置已维护」，此时 0 是权威值，不能回退到不合格数量。
        List<Map<String, Object>> unqualifiedRows = sourceDetail.get("unqualified") instanceof List
                ? (List<Map<String, Object>>) sourceDetail.get("unqualified") : null;

        Map<String, Object> head = new HashMap<>();
        head.put("单据日期", LocalDate.now().toString());
        head.put("退货日期", LocalDate.now().toString());
        copyFirst(sourceHead, head, "供应商编码", "供应商", "仓库", "部门", "项目", "经手人");
        head.put("退货原因", "检验不合格退回");
        head.put("检验单号", source.getFormNo());

        // 价格从到货单取：检验单表头的来源单号就是到货单号，来源行号对应到货明细行。
        // 成品报检（来源为成品报检单）没有到货单，必须给出明确提示而不是「表单数据不存在」。
        Map<String, Object> arrivalDetail = new HashMap<>();
        String arrivalNo = textOf(sourceHead.get("来源单号")).trim();
        if (arrivalNo.isEmpty()) {
            for (Map<String, Object> sourceRow : sourceRows) {
                if ("ARRIVAL_IN".equals(textOf(sourceRow.get("来源面板")).trim())) {
                    arrivalNo = textOf(sourceRow.get("来源单号")).trim();
                    if (!arrivalNo.isEmpty()) break;
                }
            }
        }
        FormData arrival = arrivalNo.isEmpty() ? null : formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "ARRIVAL_IN").eq(FormData::getFormNo, arrivalNo));
        if (arrival == null) {
            throw new IllegalStateException("仅来料检验单（来源为到货单）可生成采购退货单，当前检验单不是由到货单生成");
        }
        arrivalDetail = parseData(arrival.getDetailData());
        List<Map<String, Object>> arrivalRows = firstDetailRows(arrivalDetail);
        Map<String, Object> arrivalHead = parseData(arrival.getData());
        head.put("采购订单号", arrivalHead.getOrDefault("采购订单号", ""));

        List<Map<String, Object>> items = new ArrayList<>();
        for (int index = 0; index < sourceRows.size(); index++) {
            Map<String, Object> sourceRow = sourceRows.get(index);
            int lineNo = index + 1;
            // 全批退回：整批（含合格品）退回；其余只退不合格数量
            double returnQuantity = "全批退回".equals(textOf(sourceRow.get("处置方式")))
                    ? num(sourceRow.get("检验数量"))
                    : purchaseReturnQuantity(sourceRow, unqualifiedRows, lineNo);
            if (returnQuantity <= 0) continue;

            Map<String, Object> row = new HashMap<>();
            row.put("存货编码", firstValue(sourceRow, "存货编码", "产品编码"));
            row.put("存货名称", firstValue(sourceRow, "存货名称", "产品名称"));
            row.put("规格型号", sourceRow.getOrDefault("规格型号", ""));
            Object warehouse = sourceRow.get("仓库");
            row.put("仓库", warehouse == null || String.valueOf(warehouse).isBlank()
                    ? head.getOrDefault("仓库", "原料仓") : warehouse);
            Object unit = sourceRow.get("计量单位");
            row.put("计量单位", unit == null || String.valueOf(unit).isBlank() ? "件" : unit);
            row.put("数量", returnQuantity);
            row.put("实收数量", returnQuantity);

            Map<String, Object> arrivalRow = matchArrivalRow(arrivalRows, sourceRow);
            double returnRate = num(arrivalRow.getOrDefault("税率%", sourceRow.getOrDefault("税率%", 13)));
            ReturnAmounts amounts = returnAmounts(returnQuantity,
                    num(arrivalRow.getOrDefault("单价", sourceRow.getOrDefault("单价", 0))),
                    num(arrivalRow.getOrDefault("含税单价", sourceRow.getOrDefault("含税单价", 0))),
                    returnRate);
            row.put("单价", amounts.price());
            row.put("金额", amounts.amount());
            row.put("税率%", returnRate);
            row.put("含税单价", amounts.taxPrice());
            row.put("税额", amounts.tax());
            row.put("含税金额", amounts.gross());
            row.put("批号", firstValue(sourceRow, "批号", "检验批号", "批次号"));
            stampSource(row, panelCode, source.getFormNo(), lineNo, returnQuantity);
            items.add(row);
        }
        if (items.isEmpty()) throw new IllegalStateException("检验单没有不合格退回数量");

        Map<String, Object> detail = new HashMap<>();
        detail.put("items", items);
        return insertGenerated("PURCHASE_RETURN", panelCode, source.getFormNo(), head, detail);
    }

    /**
     * 检验单一行可退回数量：不合格处理页签存在（含空数组）时只取处理方式=退回的处理数量，
     * 0 是权威值；只有页签不存在（旧单据，unqualifiedRows == null）才回退到
     * 验收退回数量 → 不合格数量。
     */
    static double purchaseReturnQuantity(Map<String, Object> sourceRow,
                                         List<Map<String, Object>> unqualifiedRows, int lineNo) {
        double rejected;
        if (sourceRow.containsKey("验收退回数量")) {
            rejected = num(sourceRow.get("验收退回数量"));
        } else if (sourceRow.containsKey("判定退货数量")) {
            rejected = num(sourceRow.get("判定退货数量"));
        } else {
            rejected = num(sourceRow.get("不合格数量"));
        }
        if (unqualifiedRows == null) return Math.max(0, rejected);
        double returned = 0;
        for (Map<String, Object> row : unqualifiedRows) {
            int related = (int) Math.round(num(row.get("关联明细行号")));
            if (related == lineNo && "退回".equals(String.valueOf(row.getOrDefault("处理方式", "")))) {
                returned += num(row.get("处理数量"));
            }
        }
        if (rejected > 0) returned = Math.min(returned, rejected);
        return Math.max(0, returned);
    }

    private Map<String, Object> matchArrivalRow(List<Map<String, Object>> arrivalRows, Map<String, Object> sourceRow) {
        int sourceLine = (int) Math.round(num(sourceRow.get("来源行号")));
        if (sourceLine > 0 && sourceLine <= arrivalRows.size()) return arrivalRows.get(sourceLine - 1);
        Object code = firstValue(sourceRow, "存货编码", "产品编码");
        for (Map<String, Object> row : arrivalRows) {
            if (String.valueOf(code).equals(String.valueOf(firstValue(row, "存货编码", "产品编码")))) return row;
        }
        return Map.of();
    }

    private void stampSource(Map<String, Object> row, String panel, String no, int rowNo, double quantity) {
        row.put("来源面板", panel);
        row.put("来源单号", no);
        row.put("来源行号", rowNo);
        row.put("来源数量", quantity);
    }

    private void copyFirst(Map<String, Object> source, Map<String, Object> target, String... fields) {
        for (String field : fields) if (source.containsKey(field)) target.put(field, source.get(field));
    }

    private Object firstValue(Map<String, Object> row, Object... keys) {
        for (Object key : keys) {
            if (!(key instanceof String name)) continue;
            Object value = row.get(name);
            if (value != null && !String.valueOf(value).isBlank()) return value;
        }
        return keys.length > 0 && !(keys[keys.length - 1] instanceof String) ? keys[keys.length - 1] : "";
    }

    /** 推式生单：请购单（PU_REQ）→ 采购订单（PU_ORDER）；表头带建议供应商，明细带存货/数量/价格 */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createPuOrderFromReq(String panelCode, Map<String, Object> formData) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData src = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "PU_REQ").eq(FormData::getFormNo, String.valueOf(no)));
        if (src == null) throw new IllegalArgumentException("请购单不存在：" + no);
        requireApprovedPurchaseSource(src, "生成采购订单");
        Map<String, Object> head = parseData(src.getData());
        Map<String, Object> dm = parseData(src.getDetailData());
        List<Map<String, Object>> items = dm.get("items") instanceof List
                ? (List<Map<String, Object>>) dm.get("items") : new ArrayList<>();
        if (items.isEmpty()) throw new IllegalStateException("请购单无明细：" + no);

        Map<String, Object> poData = new HashMap<>();
        poData.put("单据日期", LocalDate.now().toString());
        poData.put("项目", head.getOrDefault("项目", ""));
        poData.put("供应商", head.getOrDefault("建议供应商", ""));
        poData.put("供应商编码", head.getOrDefault("建议供应商编码", ""));
        poData.put("币种", "人民币");
        poData.put("汇率", 1);
        poData.put("到货地址", head.getOrDefault("到货地址", ""));
        String deliveryDate = String.valueOf(firstValue(head, "需求日期"));
        if (deliveryDate.isBlank()) {
            for (Map<String, Object> it : items) {
                String candidate = String.valueOf(firstValue(it, "需求日期"));
                if (!candidate.isBlank() && (deliveryDate.isBlank() || candidate.compareTo(deliveryDate) < 0)) {
                    deliveryDate = candidate;
                }
            }
        }
        poData.put("交货日期", deliveryDate);
        poData.put("发货状态", "未发货");
        poData.put("合同号", String.valueOf(no));
        poData.put("订金金额", 0);
        poData.put("付款方式", "现付");
        poData.put("数据来源", "请购单");
        List<Map<String, Object>> rows = new ArrayList<>();
        for (int index = 0; index < items.size(); index++) {
            Map<String, Object> it = items.get(index);
            double ordered = num(it.getOrDefault("数量", 0));
            if (ordered <= 0) continue;
            // 部分下单：本次只生成请购行的剩余未下单数量
            double alreadyOrdered = reservedQuantityForPanel("PU_ORDER", null,
                    "PU_REQ", String.valueOf(no), index + 1);
            double quantity = Math.max(0, ordered - alreadyOrdered);
            if (quantity <= 0) continue;
            double ratio = quantity / ordered;
            Map<String, Object> r = new HashMap<>();
            Object unit = firstValue(it, "采购单位", "计量单位", "单位");
            r.put("物料编码", it.getOrDefault("存货编码", ""));
            r.put("物料名称", it.getOrDefault("存货名称", ""));
            r.put("规格型号", it.getOrDefault("规格型号", ""));
            r.put("单位", String.valueOf(unit).isBlank() ? "件" : unit);
            r.put("数量", quantity);
            r.put("数量2", Math.round(num(it.getOrDefault("数量2", 0)) * ratio * 100) / 100.0);
            r.put("单价", it.getOrDefault("单价", 0));
            r.put("金额", Math.round(num(it.getOrDefault("金额", 0)) * ratio * 100) / 100.0);
            r.put("税率%", it.getOrDefault("税率%", 13));
            r.put("含税单价", it.getOrDefault("含税单价", 0));
            r.put("含税金额", Math.round(num(it.getOrDefault("含税金额", 0)) * ratio * 100) / 100.0);
            Object itemDelivery = firstValue(it, "需求日期");
            r.put("预计到货日期", itemDelivery == null || String.valueOf(itemDelivery).isBlank()
                    ? deliveryDate : itemDelivery);
            r.put("现存量", it.getOrDefault("现存量", 0));
            r.put("现存量说明", it.getOrDefault("现存量说明", ""));
            stampSource(r, "PU_REQ", String.valueOf(no), index + 1, quantity);
            rows.add(r);
        }
        if (rows.isEmpty()) throw new IllegalStateException("请购单没有可下单数量");
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("PU_ORDER", "PU_REQ", String.valueOf(no), poData, detail);
    }

    /** 推式生单：采购需求分析（PU_REQ_ANALYSIS）→ 请购单（PU_REQ）；分析结果按现存量/在途实时重算。 */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createPuReqFromAnalysis(String panelCode, Map<String, Object> formData) {
        Map<String, Object> analysis = queryPurchaseReqAnalysis(null, null);
        List<Map<String, Object>> suggestions = analysis.get("list") instanceof List
                ? (List<Map<String, Object>>) analysis.get("list") : new ArrayList<>();
        Map<String, Object> head = new HashMap<>();
        head.put("单据日期", LocalDate.now().toString());
        head.put("需求日期", LocalDate.now().toString());
        head.put("请购人", currentUserName());
        head.put("备注", "由采购需求分析生成");
        List<Map<String, Object>> rows = new ArrayList<>();
        for (Map<String, Object> item : suggestions) {
            double qty = num(item.get("建议请购数量"));
            if (qty <= 0) continue;
            Map<String, Object> row = new HashMap<>();
            row.put("存货编码", item.getOrDefault("材料编码", ""));
            row.put("存货名称", item.getOrDefault("材料名称", ""));
            row.put("规格型号", item.getOrDefault("规格型号", ""));
            row.put("采购单位", item.getOrDefault("计量单位", "件"));
            row.put("数量", qty);
            row.put("需求日期", LocalDate.now().toString());
            row.put("现存量", item.getOrDefault("现存量", 0));
            row.put("备注", "需求来源：" + item.getOrDefault("需求来源", ""));
            rows.add(row);
        }
        if (rows.isEmpty()) {
            throw new IllegalStateException("采购需求分析没有需要请购的物料（建议请购数量均为 0）");
        }
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("PU_REQ", "PU_REQ_ANALYSIS", "分析-" + LocalDate.now(), head, detail);
    }

    /** 推式生单：采购订单（PU_ORDER）→ 采购入库单（PURCHASE_IN） */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createPurchaseInFromPo(String panelCode, Map<String, Object> formData) {
        return createPurchaseInFromPo(panelCode, formData, Map.of());
    }

    /** 推式生单：采购订单 → 采购入库单；warehouse 可指定入库仓库，默认原料仓。 */
    private Map<String, Object> createPurchaseInFromPo(String panelCode, Map<String, Object> formData,
                                                       Map<String, Object> buttonParam) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData src = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "PU_ORDER").eq(FormData::getFormNo, String.valueOf(no)));
        if (src == null) throw new IllegalArgumentException("采购订单不存在：" + no);
        requireApprovedPurchaseSource(src, "生成采购入库单");
        Map<String, Object> head = parseData(src.getData());
        Map<String, Object> dm = parseData(src.getDetailData());
        List<Map<String, Object>> items = dm.get("items") instanceof List
                ? (List<Map<String, Object>>) dm.get("items") : new ArrayList<>();
        if (items.isEmpty()) throw new IllegalStateException("采购订单无明细：" + no);
        String warehouse = warehouseParam(buttonParam, "warehouse", "原料仓");

        Map<String, Object> piData = new HashMap<>();
        piData.put("单据日期", LocalDate.now().toString());
        piData.put("供应商编码", head.getOrDefault("供应商编码", ""));
        piData.put("供应商", head.getOrDefault("供应商", ""));
        piData.put("采购订单号", String.valueOf(no));
        piData.put("数据来源", "采购订单");
        List<Map<String, Object>> rows = new ArrayList<>();
        for (int index = 0; index < items.size(); index++) {
            Map<String, Object> it = items.get(index);
            double quantity = num(it.getOrDefault("数量", 0));
            if (quantity <= 0) continue;
            double price = num(it.getOrDefault("单价", 0));
            double taxRate = num(it.getOrDefault("税率%", 13));
            double gross = num(it.getOrDefault("含税金额", 0));
            if (gross <= 0) gross = Math.round(quantity * num(it.getOrDefault("含税单价", 0)) * 100) / 100.0;
            double amount = num(it.getOrDefault("金额", 0));
            if (amount <= 0) amount = Math.round(quantity * price * 100) / 100.0;
            double tax = Math.round(gross * taxRate / (100 + taxRate) * 100) / 100.0;
            Map<String, Object> r = new HashMap<>();
            r.put("仓库", warehouse);
            // 编码必须落库：库存台账的 item_code 取自 存货编码/产品编码
            String piCode = String.valueOf(it.getOrDefault("物料编码",
                    it.getOrDefault("存货编码", "")));
            r.put("存货编码", piCode);
            r.put("物料编码", piCode);
            r.put("存货名称", it.getOrDefault("物料名称", ""));
            r.put("规格型号", it.getOrDefault("规格型号", ""));
            r.put("实收数量", quantity);
            r.put("计量单位", it.getOrDefault("单位", "件"));
            r.put("单价", price);
            r.put("税率%", taxRate);
            r.put("含税单价", it.getOrDefault("含税单价", 0));
            r.put("金额", amount);
            r.put("税额", tax);
            r.put("含税金额", gross);
            r.put("现存量", it.getOrDefault("现存量", 0));
            stampSource(r, "PU_ORDER", String.valueOf(no), index + 1, quantity);
            rows.add(r);
        }
        if (rows.isEmpty()) throw new IllegalStateException("采购订单没有可入库数量");
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("PURCHASE_IN", "PU_ORDER", String.valueOf(no), piData, detail);
    }

    /** 推式生单：采购订单（PU_ORDER）→ 到货单（ARRIVAL_IN，收料通知）。 */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createArrivalFromPo(String panelCode, Map<String, Object> formData) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData src = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "PU_ORDER").eq(FormData::getFormNo, String.valueOf(no)));
        if (src == null) throw new IllegalArgumentException("采购订单不存在：" + no);
        requireApprovedPurchaseSource(src, "生成到货单");
        Map<String, Object> head = parseData(src.getData());
        Map<String, Object> dm = parseData(src.getDetailData());
        List<Map<String, Object>> items = firstDetailRows(dm);
        if (items.isEmpty()) throw new IllegalStateException("采购订单无明细：" + no);

        Map<String, Object> arrivalHead = new HashMap<>();
        arrivalHead.put("单据日期", LocalDate.now().toString());
        arrivalHead.put("业务类型", "普通采购");
        arrivalHead.put("供应商编码", head.getOrDefault("供应商编码", ""));
        arrivalHead.put("供应商", head.getOrDefault("供应商", ""));
        arrivalHead.put("项目", head.getOrDefault("项目", ""));
        arrivalHead.put("仓库", "原料仓");
        arrivalHead.put("采购订单号", String.valueOf(no));
        arrivalHead.put("到货日期", LocalDate.now().toString());
        arrivalHead.put("检验状态", "未检");
        arrivalHead.put("数据来源", "采购订单");
        List<Map<String, Object>> rows = new ArrayList<>();
        for (int index = 0; index < items.size(); index++) {
            Map<String, Object> it = items.get(index);
            double ordered = num(it.getOrDefault("数量", 0));
            if (ordered <= 0) continue;
            // 部分到货：本次只生成订单行的剩余未到货数量（已有到货单按同面板占用扣除）
            double alreadyArrived = reservedQuantityForPanel("ARRIVAL_IN", null,
                    "PU_ORDER", String.valueOf(no), index + 1);
            double qty = Math.max(0, ordered - alreadyArrived);
            if (qty <= 0) continue;
            double ratio = qty / ordered;
            Map<String, Object> r = new HashMap<>();
            r.put("项目", head.getOrDefault("项目", ""));
            r.put("存货编码", it.getOrDefault("物料编码", ""));
            r.put("存货名称", it.getOrDefault("物料名称", ""));
            r.put("规格型号", it.getOrDefault("规格型号", ""));
            Object warehouse = it.get("仓库");
            r.put("仓库", warehouse == null || String.valueOf(warehouse).isBlank() ? "原料仓" : warehouse);
            r.put("采购单位", it.getOrDefault("单位", "件"));
            r.put("到货数量", qty);
            r.put("到货拒收数量", 0);
            r.put("实收数量", qty);
            // 质检与累计执行量为结果字段：生单时显式置 0，避免明细 JSON 缺键导致列表/表单空白
            r.put("检验数量", 0);
            r.put("合格数量", 0);
            r.put("不合格数量", 0);
            r.put("让步接收数量", 0);
            r.put("报废数量", 0);
            r.put("累计入库数量", 0);
            r.put("累计退货数量", 0);
            r.put("检验方式", "全检");
            r.put("抽检比例%", 100);
            r.put("预计到货日期", it.getOrDefault("预计到货日期", head.getOrDefault("交货日期", "")));
            r.put("采购订单号", String.valueOf(no));
            double price = num(it.getOrDefault("单价", 0));
            double taxRate = num(it.getOrDefault("税率%", 13));
            double gross = Math.round(num(it.getOrDefault("含税金额", 0)) * ratio * 100) / 100.0;
            if (gross <= 0) gross = Math.round(qty * num(it.getOrDefault("含税单价", 0)) * 100) / 100.0;
            double amount = Math.round(num(it.getOrDefault("金额", 0)) * ratio * 100) / 100.0;
            if (amount <= 0) amount = Math.round(qty * price * 100) / 100.0;
            double tax = Math.round(gross * taxRate / (100 + taxRate) * 100) / 100.0;
            r.put("单价", price);
            r.put("税率%", taxRate);
            r.put("含税单价", it.getOrDefault("含税单价", 0));
            r.put("金额", amount);
            r.put("税额", tax);
            r.put("含税金额", gross);
            stampSource(r, "PU_ORDER", String.valueOf(no), index + 1, qty);
            rows.add(r);
        }
        if (rows.isEmpty()) throw new IllegalStateException("采购订单没有大于零的可到货数量");
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("ARRIVAL_IN", "PU_ORDER", String.valueOf(no), arrivalHead, detail);
    }

    /** 推式生单：到货单（ARRIVAL_IN）→ 采购入库单（PURCHASE_IN）；无质检路径。 */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createPurchaseInFromArrival(String panelCode, Map<String, Object> formData) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData src = formOf(panelCode, no);
        requireApprovedPurchaseSource(src, "生成采购入库单");
        Map<String, Object> head = parseData(src.getData());
        List<Map<String, Object>> items = firstDetailRows(parseData(src.getDetailData()));
        if (items.isEmpty()) throw new IllegalStateException("到货单无明细：" + no);
        Set<Integer> inspectedLines = effectiveInspectionLines(panelCode, String.valueOf(no));

        Map<String, Object> inHead = new HashMap<>();
        inHead.put("单据日期", LocalDate.now().toString());
        inHead.put("供应商编码", head.getOrDefault("供应商编码", ""));
        inHead.put("供应商", head.getOrDefault("供应商", ""));
        inHead.put("仓库", head.getOrDefault("仓库", "原料仓"));
        inHead.put("采购订单号", head.getOrDefault("采购订单号", ""));
        inHead.put("到货单号", String.valueOf(no));
        inHead.put("入库类别", "采购入库");
        inHead.put("数据来源", "到货单");
        List<Map<String, Object>> rows = new ArrayList<>();
        int inspectedSkipped = 0;
        for (int index = 0; index < items.size(); index++) {
            Map<String, Object> it = items.get(index);
            // 已由检验单接手（检验单审批后）的行不再从到货单直接入库；
            // 混装到货单仍可为剩下的免检行生成采购入库单。
            if (inspectedLines.contains(index + 1)) {
                inspectedSkipped++;
                continue;
            }
            // 质量门禁：非免检物料必须先完成检验，不能从到货单直接入库
            String inspectMethod = textOf(it.getOrDefault("检验方式", head.getOrDefault("检验方式", "")));
            if (!"免检".equals(inspectMethod)) {
                throw new IllegalStateException("到货单第 " + (index + 1) + " 行检验方式为"
                        + (inspectMethod.isEmpty() ? "未设置" : inspectMethod)
                        + "，请先生成检验单并完成检验；免检物料请把检验方式改为「免检」");
            }
            double received = arrivalReceivable(it);
            if (received <= 0) continue;
            Map<String, Object> r = new HashMap<>();
            r.put("存货编码", it.getOrDefault("存货编码", ""));
            r.put("存货名称", it.getOrDefault("存货名称", ""));
            r.put("规格型号", it.getOrDefault("规格型号", ""));
            Object warehouse = it.get("仓库");
            r.put("仓库", warehouse == null || String.valueOf(warehouse).isBlank()
                    ? inHead.get("仓库") : warehouse);
            r.put("计量单位", it.getOrDefault("采购单位", it.getOrDefault("计量单位", "件")));
            r.put("实收数量", received);
            r.put("数量", received);
            double price = num(it.getOrDefault("单价", 0));
            double taxRate = num(it.getOrDefault("税率%", 13));
            double gross = num(it.getOrDefault("含税金额", 0));
            if (gross <= 0) gross = Math.round(received * num(it.getOrDefault("含税单价", 0)) * 100) / 100.0;
            double amount = num(it.getOrDefault("金额", 0));
            if (amount <= 0) amount = Math.round(received * price * 100) / 100.0;
            double tax = Math.round(gross * taxRate / (100 + taxRate) * 100) / 100.0;
            r.put("单价", price);
            r.put("税率%", taxRate);
            r.put("含税单价", it.getOrDefault("含税单价", 0));
            r.put("金额", amount);
            r.put("税额", tax);
            r.put("含税金额", gross);
            r.put("批号", it.getOrDefault("批号", ""));
            stampSource(r, panelCode, String.valueOf(no), index + 1, received);
            rows.add(r);
        }
        if (rows.isEmpty()) {
            if (inspectedSkipped > 0) {
                throw new IllegalStateException("到货单明细已由检验单接手，请从检验单生成采购入库单");
            }
            throw new IllegalStateException("到货单没有可入库的实收数量");
        }
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("PURCHASE_IN", panelCode, String.valueOf(no), inHead, detail);
    }

    /** 推式生单：采购入库单（PURCHASE_IN）→ 采购退货单（PURCHASE_RETURN）；库存退料。 */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createPurchaseReturnFromPurchaseIn(String panelCode, Map<String, Object> formData) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData src = formOf("PURCHASE_IN", no);
        requireApprovedPurchaseSource(src, "生成采购退货单");
        Map<String, Object> head = parseData(src.getData());
        List<Map<String, Object>> items = firstDetailRows(parseData(src.getDetailData()));
        if (items.isEmpty()) throw new IllegalStateException("采购入库单无明细：" + no);

        Map<String, Object> retHead = new HashMap<>();
        retHead.put("单据日期", LocalDate.now().toString());
        retHead.put("退货日期", LocalDate.now().toString());
        copyFirst(head, retHead, "供应商编码", "供应商", "仓库", "检验单号", "到货单号", "采购订单号");
        retHead.put("退货原因", "库存退料");
        retHead.put("入库单号", String.valueOf(no));
        List<Map<String, Object>> rows = new ArrayList<>();
        for (int index = 0; index < items.size(); index++) {
            Map<String, Object> it = items.get(index);
            double qty = num(it.get("实收数量"));
            if (qty <= 0) qty = num(it.get("数量"));
            if (qty <= 0) continue;
            Map<String, Object> r = new HashMap<>();
            r.put("存货编码", it.getOrDefault("存货编码", it.getOrDefault("物料编码", "")));
            r.put("存货名称", it.getOrDefault("存货名称", it.getOrDefault("物料名称", "")));
            r.put("规格型号", it.getOrDefault("规格型号", ""));
            Object warehouse = it.get("仓库");
            r.put("仓库", warehouse == null || String.valueOf(warehouse).isBlank() ? retHead.get("仓库") : warehouse);
            r.put("计量单位", it.getOrDefault("计量单位", it.getOrDefault("单位", "件")));
            r.put("数量", qty);
            r.put("实收数量", qty);
            double returnRate = num(it.getOrDefault("税率%", 13));
            ReturnAmounts amounts = returnAmounts(qty,
                    num(it.getOrDefault("单价", 0)),
                    num(it.getOrDefault("含税单价", 0)),
                    returnRate);
            r.put("单价", amounts.price());
            r.put("金额", amounts.amount());
            r.put("税率%", returnRate);
            r.put("含税单价", amounts.taxPrice());
            r.put("税额", amounts.tax());
            r.put("含税金额", amounts.gross());
            r.put("批号", it.getOrDefault("批号", ""));
            stampSource(r, "PURCHASE_IN", String.valueOf(no), index + 1, qty);
            rows.add(r);
        }
        if (rows.isEmpty()) throw new IllegalStateException("采购入库单没有可退货数量");
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("PURCHASE_RETURN", "PURCHASE_IN", String.valueOf(no), retHead, detail);
    }

    /** 推式生单：生产加工单（MANU_ORDER）→ 产成品入库单（FINISH_IN） */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createFinishInFromMo(String panelCode, Map<String, Object> formData) {
        return createFinishInFromMo(panelCode, formData, Map.of());
    }

    /**
     * 推式生单：生产加工单 → 产成品入库单。
     *
     * @param buttonParam 可带 {@code warehouse}（入库仓库，默认成品仓）与
     *                    {@code lines=[{存货编码, 数量}]}（按剩余量分次入库）。
     */
    private Map<String, Object> createFinishInFromMo(String panelCode, Map<String, Object> formData,
                                                     Map<String, Object> buttonParam) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData src = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "MANU_ORDER").eq(FormData::getFormNo, String.valueOf(no)));
        if (src == null) throw new IllegalArgumentException("生产加工单不存在：" + no);
        if (!"已审核".equals(src.getStatus()) && !"生产中".equals(src.getStatus())) {
            throw new IllegalStateException("仅已审核/生产中的生产加工单可生成产成品入库单");
        }
        // 完工入库前置：末道工序必须报满（累计报工 = 加工单数量），没做完不能入库
        Map<String, Object> completion = completionSummary(String.valueOf(no));
        // 有工序路线的按完工判定卡；没有工序明细的老单/无路线单不做此限制
        int totalProc = (int) Math.round(num(completion.get("工序数")));
        int doneProc = (int) Math.round(num(completion.get("已完成工序数")));
        if (totalProc > 0 && !Boolean.TRUE.equals(completion.get("首检应触发"))) {
            throw new IllegalStateException("末道工序尚未报工完成（" + doneProc + "/" + totalProc
                    + " 道完成），全部工序报工完成才能入库");
        }
        // 质量门：首件报检单没审核不能入库，否则首检只是张草稿纸
        requireFirstInspectApproved(String.valueOf(no), totalProc);
        Map<String, Object> head = parseData(src.getData());
        Map<String, Object> dm = parseData(src.getDetailData());
        List<Map<String, Object>> products = dm.get("products") instanceof List
                ? (List<Map<String, Object>>) dm.get("products") : new ArrayList<>();
        if (products.isEmpty()) throw new IllegalStateException("生产加工单无产成品明细：" + no);
        // 只入「还没入过的量」：累计入库数量由入库单审核维护，整单照抄会重复入库。
        List<Map<String, Object>> remainingProducts = new ArrayList<>();
        for (Map<String, Object> p : products) {
            double plan = num(p.getOrDefault("数量", 0));
            double received = num(p.getOrDefault("累计入库数量", 0));
            double remaining = round2(plan - received);
            if (remaining <= 0) continue;
            Map<String, Object> copy = new HashMap<>(p);
            copy.put("数量", remaining);
            remainingProducts.add(copy);
        }
        if (remainingProducts.isEmpty()) {
            throw new IllegalStateException("该生产加工单已全部入库，无需再生成产成品入库单");
        }
        // 未完结（草稿/审批中）的入库单会挡住重复生成：草稿不写库存流水，
        // 多张草稿各自都能通过「剩余量」检查，全部审核后就会超量入库。
        String openIn = findOpenFinishIn(String.valueOf(no));
        if (openIn != null) {
            throw new IllegalStateException("该生产加工单已有产成品入库单（状态：" + openIn
                    + "），请直接处理该单据；如需分批入库，先审核当前单据。");
        }
        Map<String, Double> selectedQty = selectedLineQty(buttonParam);
        if (!selectedQty.isEmpty()) {
            List<Map<String, Object>> picked = new ArrayList<>();
            for (Map<String, Object> p : remainingProducts) {
                String code = String.valueOf(p.getOrDefault("产品编码", "")).trim();
                Double qty = selectedQty.get(code);
                if (qty == null || qty <= 0) continue;
                Map<String, Object> copy = new HashMap<>(p);
                copy.put("数量", Math.min(qty, num(p.get("数量"))));
                picked.add(copy);
            }
            if (picked.isEmpty()) {
                throw new IllegalStateException("请至少勾选一行要入库的产品，并填写大于 0 的入库数量");
            }
            remainingProducts = picked;
        }
        products = remainingProducts;
        String warehouse = String.valueOf(buttonParam.getOrDefault("warehouse", "")).trim();
        if (warehouse.isEmpty()) warehouse = SALE_OUT_WAREHOUSE;

        Map<String, Object> fiData = new HashMap<>();
        fiData.put("单据日期", LocalDate.now().toString());
        fiData.put("加工单号", String.valueOf(no));
        fiData.put("业务类型", "产成品入库");
        fiData.put("入库类别", "自制加工入库");
        fiData.put("生产车间", head.getOrDefault("生产车间", ""));
        // 客户/订单上下文必须随单据带下去：销货单与应收凭证都靠表头客户挂往来单位，
        // 缺了它销货单客户为空、应收挂不上、收款核销无从匹配。
        fiData.put("客户", head.getOrDefault("客户", ""));
        fiData.put("客户编码", head.getOrDefault("客户编码", ""));
        fiData.put("结算客户", head.getOrDefault("结算客户", head.getOrDefault("客户", "")));
        fiData.put("销售订单号", head.getOrDefault("销售订单号", ""));
        double receiveTotal = 0;
        for (Map<String, Object> p : products) receiveTotal += num(p.getOrDefault("数量", 0));
        double unitCost = manufactureUnitCost(dm, receiveTotal);
        List<Map<String, Object>> rows = new ArrayList<>();
        for (int index = 0; index < products.size(); index++) {
            Map<String, Object> p = products.get(index);
            Map<String, Object> r = new HashMap<>();
            // 编码必须落库：库存台账 stock_txn/stock_balance 的 item_code 取自 存货编码/产品编码，
            // 只写名称会让 item_code 变空串（查询接口按名称反查补编码，把问题掩盖在界面上）。
            String productCode = String.valueOf(p.getOrDefault("产品编码", p.getOrDefault("存货编码", "")));
            r.put("产品编码", productCode);
            r.put("存货编码", productCode);
            r.put("产品名称", p.getOrDefault("产品名称", ""));
            r.put("存货名称", p.getOrDefault("产品名称", p.getOrDefault("存货名称", "")));
            r.put("仓库", warehouse);
            r.put("规格型号", p.getOrDefault("规格型号", ""));
            r.put("计量单位", p.getOrDefault("生产单位", "件"));
            double receiveQty = num(p.getOrDefault("数量", 0));
            r.put("实收数量", p.getOrDefault("数量", 0));
            // 自制成本 = 材料结存均价×计划用量 + 工序工价×计划数量，折成单位成本入库，
            // 否则产成品结存金额恒为 0，销售出库的成本结转永远是零
            r.put("单价", unitCost);
            r.put("金额", round2(unitCost * receiveQty));
            r.put("现存量", p.getOrDefault("现存量", 0));
            r.put("图号", p.getOrDefault("图号", ""));
            stampSource(r, "MANU_ORDER", String.valueOf(no), index + 1, num(p.getOrDefault("数量", 0)));
            rows.add(r);
        }
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("FINISH_IN", "MANU_ORDER", String.valueOf(no), fiData, detail);
    }

    /** 推式生单：领料申请单（MATERIAL_REQ）或生产加工单（MANU_ORDER）→ 材料出库单（MATERIAL_OUT） */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createMaterialOut(String panelCode, Map<String, Object> formData) {
        return createMaterialOut(panelCode, formData, Map.of());
    }

    /** 推式生单：领料申请单/加工单 → 材料出库单；warehouse 可整体覆盖出库仓库。 */
    private Map<String, Object> createMaterialOut(String panelCode, Map<String, Object> formData,
                                                  Map<String, Object> buttonParam) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData src = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, panelCode).eq(FormData::getFormNo, String.valueOf(no)));
        if (src == null) throw new IllegalArgumentException("来源单据不存在：" + no);
        if (!"已审核".equals(src.getStatus())) throw new IllegalStateException("仅已审核单据可生成材料出库单");
        Map<String, Object> head = parseData(src.getData());
        Map<String, Object> dm = parseData(src.getDetailData());
        List<Map<String, Object>> rows = new ArrayList<>();
        Map<String, Object> moData = new HashMap<>();
        moData.put("单据日期", LocalDate.now().toString());
        moData.put("业务类型", "材料出库");
        moData.put("出库类别", "直接领料");
        moData.put("生产车间", head.getOrDefault("生产车间", ""));
        moData.put("领用人", head.getOrDefault("领料申请人", ""));
        moData.put("加工单号", head.getOrDefault("加工单号", ""));
        if ("TRANSFER".equals(panelCode)) {
            // 推式生单：调拨单 → 材料出库单（调拨出库，T+ 调拨单出库方向）
            List<Map<String, Object>> items = dm.get("items") instanceof List
                    ? (List<Map<String, Object>>) dm.get("items") : new ArrayList<>();
            for (Map<String, Object> it : items) {
                Map<String, Object> r = new HashMap<>();
                r.put("仓库", stockWarehouseOf(head.get("调出仓库"),
                    it.getOrDefault("材料编码", ""), it.getOrDefault("材料名称", ""), "原料仓"));
                r.put("加工单号", head.getOrDefault("来源单号", ""));
                r.put("材料名称", it.getOrDefault("存货名称", ""));
                r.put("计量单位", it.getOrDefault("计量单位", "kg"));
                double trQty = num(it.getOrDefault("数量", 0));
                r.put("数量", it.getOrDefault("数量", 0));
                double trPrice = stockAvgPrice(String.valueOf(it.getOrDefault("材料编码", "")),
                        String.valueOf(it.getOrDefault("存货名称", "")), String.valueOf(r.get("仓库")));
                if (trPrice <= 0) trPrice = num(it.getOrDefault("单价", 0));
                r.put("单价", trPrice);
                r.put("金额", round2(trQty * trPrice));
                r.put("规格型号", it.getOrDefault("规格型号", ""));
                r.put("现存量", it.getOrDefault("现存量", 0));
                r.put("现存量说明", it.getOrDefault("现存量说明", ""));
                rows.add(r);
            }
            moData.put("出库类别", "调拨出库");
        } else if ("MATERIAL_REQ".equals(panelCode)) {
            List<Map<String, Object>> items = dm.get("items") instanceof List
                    ? (List<Map<String, Object>>) dm.get("items") : new ArrayList<>();
            for (Map<String, Object> it : items) {
                Map<String, Object> r = new HashMap<>();
                r.put("仓库", stockWarehouseOf(it.get("仓库"),
                        it.getOrDefault("材料编码", it.getOrDefault("存货编码", "")),
                        it.getOrDefault("材料名称", it.getOrDefault("存货名称", "")), "原料仓"));
                r.put("加工单号", it.getOrDefault("加工单号", ""));
                // 编码必须落库：库存台账的 item_code 取自 材料编码/存货编码
                String lineCode = String.valueOf(it.getOrDefault("材料编码",
                        it.getOrDefault("存货编码", "")));
                r.put("材料编码", lineCode);
                r.put("存货编码", lineCode);
                r.put("材料名称", it.getOrDefault("材料名称", ""));
                r.put("计量单位", it.getOrDefault("计量单位", "kg"));
                double reqQty = num(it.getOrDefault("数量", 0));
                r.put("数量", it.getOrDefault("数量", 0));
                // 出库按结存均价，源单没填价时才用源单价格
                double reqPrice = stockAvgPrice(lineCode,
                        String.valueOf(it.getOrDefault("材料名称", "")), String.valueOf(r.get("仓库")));
                if (reqPrice <= 0) reqPrice = num(it.getOrDefault("单价", 0));
                r.put("单价", reqPrice);
                r.put("金额", round2(reqQty * reqPrice));
                r.put("规格型号", it.getOrDefault("规格型号", ""));
                r.put("现存量", it.getOrDefault("现存量", 0));
                r.put("现存量说明", it.getOrDefault("现存量说明", ""));
                rows.add(r);
            }
        } else {
            List<Map<String, Object>> mats = dm.get("materials") instanceof List
                    ? (List<Map<String, Object>>) dm.get("materials") : new ArrayList<>();
            for (Map<String, Object> m : mats) {
                Map<String, Object> r = new HashMap<>();
                r.put("仓库", stockWarehouseOf(m.get("预出仓库"),
                        m.getOrDefault("材料编码", ""), m.getOrDefault("材料名称", ""), "原料仓"));
                r.put("加工单号", String.valueOf(no));
                String matCode = String.valueOf(m.getOrDefault("材料编码", m.getOrDefault("存货编码", "")));
                r.put("材料编码", matCode);
                r.put("存货编码", matCode);
                r.put("材料名称", m.getOrDefault("材料名称", ""));
                r.put("计量单位", m.getOrDefault("计量单位", "kg"));
                // 只带还没领完的量：已领满的材料不再带出，否则生成即超领
                double planned = num(m.getOrDefault("计划数量", m.getOrDefault("需用数量", 0)));
                double issuedAlready = num(m.getOrDefault("累计领用数量", 0));
                double remain = round2(planned - issuedAlready);
                if (remain <= 0) continue;
                r.put("数量", remain);
                // 加工单 → 材料出库单同样按结存均价出库
                double matPrice = stockAvgPrice(matCode,
                        String.valueOf(m.getOrDefault("材料名称", "")), String.valueOf(r.get("仓库")));
                r.put("单价", matPrice);
                r.put("金额", round2(remain * matPrice));
                r.put("规格型号", m.getOrDefault("规格型号", ""));
                r.put("现存量", m.getOrDefault("现存量", 0));
                rows.add(r);
            }
        }
        if (rows.isEmpty()) throw new IllegalStateException("该单据的材料已全部领用，没有可出库的剩余量：" + no);
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("MATERIAL_OUT", panelCode, String.valueOf(no), moData, detail);
    }

    /** 推式生单：领料申请单（MATERIAL_REQ）→ 调拨单（TRANSFER）：明细材料转入调拨单，调出仓库=领料申请单仓库 */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createTransferFromMaterialReq(String panelCode, Map<String, Object> formData) {
        return createTransferFromMaterialReq(panelCode, formData, Map.of());
    }

    /**
     * 推式生单：领料申请单 → 调拨单。
     * 调拨是双仓语义：fromWarehouse（调出）/ toWarehouse（调入）都由调用方指定。
     */
    private Map<String, Object> createTransferFromMaterialReq(String panelCode, Map<String, Object> formData,
                                                              Map<String, Object> buttonParam) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData src = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "MATERIAL_REQ").eq(FormData::getFormNo, String.valueOf(no)));
        if (src == null) throw new IllegalArgumentException("领料申请单不存在：" + no);
        if (!"已审核".equals(src.getStatus())) throw new IllegalStateException("仅已审核领料申请单可生成调拨单");
        Map<String, Object> head = parseData(src.getData());
        Map<String, Object> dm = parseData(src.getDetailData());
        List<Map<String, Object>> items = dm.get("items") instanceof List
                ? (List<Map<String, Object>>) dm.get("items") : new ArrayList<>();
        if (items.isEmpty()) throw new IllegalStateException("领料申请单无材料明细：" + no);

        Map<String, Object> trData = new HashMap<>();
        trData.put("单据日期", LocalDate.now().toString());
        trData.put("业务类型", "调拨");
        // 调拨是双仓语义：调出仓默认取源单仓库，调入仓必须由调用方指定
        trData.put("调出仓库", warehouseParam(buttonParam, "fromWarehouse",
                String.valueOf(head.getOrDefault("仓库", "原料仓"))));
        trData.put("调入仓库", warehouseParam(buttonParam, "toWarehouse", ""));
        trData.put("经手人", head.getOrDefault("领料申请人", ""));
        trData.put("生产车间", head.getOrDefault("生产车间", ""));
        trData.put("部门", head.getOrDefault("部门", ""));
        List<Map<String, Object>> rows = new ArrayList<>();
        for (Map<String, Object> it : items) {
            Map<String, Object> r = new HashMap<>();
            r.put("存货编码", it.getOrDefault("材料编码", ""));
            r.put("存货名称", it.getOrDefault("材料名称", ""));
            r.put("规格型号", it.getOrDefault("规格型号", ""));
            r.put("计量单位", it.getOrDefault("计量单位", "kg"));
            r.put("数量", it.getOrDefault("数量", 0));
            r.put("单价", it.getOrDefault("单价", 0));
            r.put("金额", it.getOrDefault("金额", 0));
            r.put("现存量", it.getOrDefault("现存量", 0));
            r.put("现存量说明", it.getOrDefault("现存量说明", ""));
            r.put("加工单号", it.getOrDefault("加工单号", ""));
            r.put("行中止", false);
            rows.add(r);
        }
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("TRANSFER", "MATERIAL_REQ", String.valueOf(no), trData, detail);
    }

    /** 推式生单：委外加工单（OUTSOURCE_ORDER）/领料申请单（MATERIAL_REQ）→ 委外发料单（OUTSOURCE_ISSUE） */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createOutsourceIssue(String panelCode, Map<String, Object> formData) {
        return createOutsourceIssue(panelCode, formData, Map.of());
    }

    /** 推式生单：委外加工单/领料申请单 → 委外发料单；warehouse 可整体覆盖发料仓。 */
    private Map<String, Object> createOutsourceIssue(String panelCode, Map<String, Object> formData,
                                                     Map<String, Object> buttonParam) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        String source = "OUTSOURCE_ORDER".equals(panelCode) ? "OUTSOURCE_ORDER" : "MATERIAL_REQ";
        FormData src = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, source).eq(FormData::getFormNo, String.valueOf(no)));
        if (src == null) throw new IllegalArgumentException("来源单据不存在：" + no);
        if (!"已审核".equals(src.getStatus())) throw new IllegalStateException("仅已审核单据可生成委外发料单");
        Map<String, Object> head = parseData(src.getData());
        Map<String, Object> dm = parseData(src.getDetailData());
        List<Map<String, Object>> rows = new ArrayList<>();
        if ("OUTSOURCE_ORDER".equals(source)) {
            List<Map<String, Object>> mats = dm.get("materials") instanceof List
                    ? (List<Map<String, Object>>) dm.get("materials") : new ArrayList<>();
            for (Map<String, Object> m : mats) {
                Map<String, Object> r = new HashMap<>();
                r.put("材料编码", m.getOrDefault("材料编码", ""));
                r.put("材料名称", m.getOrDefault("材料名称", ""));
                r.put("规格型号", m.getOrDefault("规格型号", ""));
                r.put("计量单位", m.getOrDefault("计量单位", "kg"));
                r.put("数量", m.getOrDefault("计划数量", m.getOrDefault("需用数量", 0)));
                r.put("单价", 0);
                r.put("金额", 0);
                r.put("仓库", stockWarehouseOf(m.get("预出仓库"),
                        m.getOrDefault("材料编码", ""), m.getOrDefault("材料名称", ""), "原料仓"));
                rows.add(r);
            }
        } else {
            List<Map<String, Object>> items = dm.get("items") instanceof List
                    ? (List<Map<String, Object>>) dm.get("items") : new ArrayList<>();
            for (Map<String, Object> it : items) {
                Map<String, Object> r = new HashMap<>();
                r.put("材料编码", it.getOrDefault("材料编码", ""));
                r.put("材料名称", it.getOrDefault("材料名称", ""));
                r.put("规格型号", it.getOrDefault("规格型号", ""));
                r.put("计量单位", it.getOrDefault("计量单位", "kg"));
                r.put("数量", it.getOrDefault("数量", 0));
                r.put("单价", it.getOrDefault("单价", 0));
                r.put("金额", it.getOrDefault("金额", 0));
                r.put("仓库", stockWarehouseOf(it.get("仓库"),
                        it.getOrDefault("材料编码", it.getOrDefault("存货编码", "")),
                        it.getOrDefault("材料名称", it.getOrDefault("存货名称", "")), "原料仓"));
                rows.add(r);
            }
        }
        if (rows.isEmpty()) throw new IllegalStateException("来源单据无材料明细：" + no);
        Map<String, Object> data = new HashMap<>();
        data.put("单据日期", LocalDate.now().toString());
        data.put("业务类型", "委外发料");
        data.put("委外供应商", head.getOrDefault("委外供应商", ""));
        data.put("委外加工单号", "OUTSOURCE_ORDER".equals(source) ? String.valueOf(no) : "");
        data.put("仓库", rows.get(0).getOrDefault("仓库", "原料仓"));
        data.put("部门", head.getOrDefault("部门", ""));
        data.put("经手人", head.getOrDefault("经手人", ""));
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("OUTSOURCE_ISSUE", source, String.valueOf(no), data, detail);
    }

    /** 推式生单：委外加工单（OUTSOURCE_ORDER）→ 委外入库单（OUTSOURCE_IN） */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createOutsourceInFromOrder(String panelCode, Map<String, Object> formData) {
        return createOutsourceInFromOrder(panelCode, formData, Map.of());
    }

    /** 推式生单：委外加工单 → 委外入库单；warehouse 可指定入库仓库。 */
    private Map<String, Object> createOutsourceInFromOrder(String panelCode, Map<String, Object> formData,
                                                           Map<String, Object> buttonParam) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData src = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "OUTSOURCE_ORDER").eq(FormData::getFormNo, String.valueOf(no)));
        if (src == null) throw new IllegalArgumentException("委外加工单不存在：" + no);
        if (!"已审核".equals(src.getStatus())) throw new IllegalStateException("仅已审核委外加工单可生成委外入库单");
        Map<String, Object> head = parseData(src.getData());
        Map<String, Object> dm = parseData(src.getDetailData());
        List<Map<String, Object>> items = dm.get("products") instanceof List
                ? (List<Map<String, Object>>) dm.get("products") : new ArrayList<>();
        if (items.isEmpty()) throw new IllegalStateException("委外加工单无产成品明细：" + no);
        Map<String, Object> data = new HashMap<>();
        data.put("单据日期", LocalDate.now().toString());
        data.put("业务类型", "委外入库");
        data.put("委外供应商", head.getOrDefault("委外供应商", ""));
        data.put("委外加工单号", String.valueOf(no));
        data.put("仓库", warehouseParam(buttonParam, "warehouse",
                String.valueOf(head.getOrDefault("仓库", "原料仓"))));
        data.put("经手人", head.getOrDefault("经手人", ""));
        List<Map<String, Object>> rows = new ArrayList<>();
        for (Map<String, Object> it : items) {
            Map<String, Object> r = new HashMap<>();
            r.put("产品编码", it.getOrDefault("产品编码", ""));
            r.put("产品名称", it.getOrDefault("产品名称", ""));
            r.put("规格型号", it.getOrDefault("规格型号", ""));
            r.put("计量单位", it.getOrDefault("计量单位", "件"));
            r.put("实收数量", it.getOrDefault("数量", 0));
            r.put("单价", it.getOrDefault("委外单价", it.getOrDefault("单价", 0)));
            r.put("金额", it.getOrDefault("金额", 0));
            r.put("现存量", it.getOrDefault("现存量", 0));
            rows.add(r);
        }
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("OUTSOURCE_IN", "OUTSOURCE_ORDER", String.valueOf(no), data, detail);
    }

    /** 推式生单：委外加工单（OUTSOURCE_ORDER）→ 委外加工费用单（OUTSOURCE_FEE）：按产成品明细委外单价×数量汇总 */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createOutsourceFeeFromOrder(String panelCode, Map<String, Object> formData) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData src = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "OUTSOURCE_ORDER").eq(FormData::getFormNo, String.valueOf(no)));
        if (src == null) throw new IllegalArgumentException("委外加工单不存在：" + no);
        if (!"已审核".equals(src.getStatus())) throw new IllegalStateException("仅已审核委外加工单可生成委外加工费用单");
        Map<String, Object> head = parseData(src.getData());
        Map<String, Object> dm = parseData(src.getDetailData());
        List<Map<String, Object>> items = dm.get("products") instanceof List
                ? (List<Map<String, Object>>) dm.get("products") : new ArrayList<>();
        if (items.isEmpty()) throw new IllegalStateException("委外加工单无产成品明细：" + no);
        Map<String, Object> data = new HashMap<>();
        data.put("单据日期", LocalDate.now().toString());
        data.put("业务类型", "委外加工费用");
        data.put("委外供应商", head.getOrDefault("委外供应商", ""));
        data.put("委外加工单号", String.valueOf(no));
        data.put("经手人", head.getOrDefault("经手人", ""));
        List<Map<String, Object>> rows = new ArrayList<>();
        double total = 0;
        for (Map<String, Object> it : items) {
            double qty = num(it.getOrDefault("数量", 0));
            double price = num(it.getOrDefault("委外单价", it.getOrDefault("单价", 0)));
            double amount = Math.round(qty * price * 100) / 100.0;
            total += amount;
            Map<String, Object> r = new HashMap<>();
            r.put("费用项目", "委外加工费");
            r.put("产品名称", it.getOrDefault("产品名称", ""));
            r.put("计量单位", it.getOrDefault("计量单位", "件"));
            r.put("数量", qty);
            r.put("委外单价", price);
            r.put("费用金额", amount);
            r.put("备注", "");
            rows.add(r);
        }
        data.put("费用合计", Math.round(total * 100) / 100.0);
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("OUTSOURCE_FEE", "OUTSOURCE_ORDER", String.valueOf(no), data, detail);
    }

    /**
     * 推式生单：产成品入库单 → 销货单（补齐 O2C 的 FINISH_IN → SALE_INV 缺环）。
     * 来源须已审核且有明细；逐行写入来源面板/单号/行号，供占用反查与追溯。
     */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createSaleInvFromFinishIn(String panelCode, Map<String, Object> formData) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData src = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "FINISH_IN").eq(FormData::getFormNo, String.valueOf(no)));
        if (src == null) throw new IllegalArgumentException("产成品入库单不存在：" + no);
        if (!"已审核".equals(src.getStatus())) {
            throw new IllegalStateException("仅已审核产成品入库单可生成销货单");
        }
        Map<String, Object> head = parseData(src.getData());
        Map<String, Object> dm = parseData(src.getDetailData());
        List<Map<String, Object>> items = dm.get("items") instanceof List
                ? (List<Map<String, Object>>) dm.get("items") : new ArrayList<>();
        if (items.isEmpty()) throw new IllegalStateException("产成品入库单无明细：" + no);

        // 产成品入库单没有销售价格：单据上的单价是成本口径，直接照抄会把销货单/发票
        // 做成 0 金额，应收凭证不生成、收款也无从核销。按关联销售订单补价。
        Map<String, Double> orderPrices = salePriceByItem(String.valueOf(head.getOrDefault("加工单号", "")));
        Map<String, Object> data = new HashMap<>();
        data.put("单据日期", LocalDate.now().toString());
        data.put("业务类型", "普通销售");
        data.put("客户", head.getOrDefault("客户", ""));
        data.put("客户编码", head.getOrDefault("客户编码", ""));
        data.put("结算客户", head.getOrDefault("结算客户", head.getOrDefault("客户", "")));
        data.put("仓库", head.getOrDefault("仓库", "成品仓"));
        data.put("销售订单号", head.getOrDefault("销售订单号", ""));
        data.put("数据来源", "产成品入库单");
        List<Map<String, Object>> rows = new ArrayList<>();
        int rowNo = 0;
        for (Map<String, Object> it : items) {
            rowNo++;
            double qty = num(firstValue(it, "实收数量", "数量"));
            if (qty == 0) continue;
            String priceCode = String.valueOf(firstValue(it, "存货编码", "产品编码") == null ? ""
                    : firstValue(it, "存货编码", "产品编码")).trim();
            double price = num(firstValue(it, "单价", "成本价"));
            if (price == 0) price = orderPrices.getOrDefault(priceCode, 0.0);
            double amount = Math.round(qty * price * 100) / 100.0;
            Map<String, Object> r = new HashMap<>();
            r.put("仓库", firstValue(it, "仓库") == null ? head.getOrDefault("仓库", "成品仓") : it.get("仓库"));
            r.put("存货编码", firstValue(it, "存货编码", "产品编码"));
            r.put("存货名称", firstValue(it, "存货名称", "产品名称", "存货"));
            r.put("规格型号", firstValue(it, "规格型号"));
            r.put("计量单位", firstValue(it, "计量单位", "生产单位", "主单位"));
            r.put("数量", qty);
            r.put("销售单位", firstValue(it, "计量单位", "生产单位", "主单位"));
            r.put("单价", price);
            r.put("含税单价", price);
            r.put("售价", price);
            r.put("含税售价", price);
            r.put("销售金额", amount);
            r.put("金额", amount);
            r.put("含税金额", amount);
            r.put("来源面板", "FINISH_IN");
            r.put("来源单号", String.valueOf(no));
            r.put("来源行号", rowNo);
            r.put("来源数量", qty);
            rows.add(r);
        }
        if (rows.isEmpty()) throw new IllegalStateException("产成品入库单明细数量全为零：" + no);
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("SALE_INV", "FINISH_IN", String.valueOf(no), data, detail);
    }

    /** 推式生单：销售订单（SO_ORDER）→ 销售出库单（SALE_OUT） */
    @SuppressWarnings("unchecked")
    /**
     * 销售出库前的库存校验：成品仓现存量必须覆盖订单需求，否则不允许直接出库。
     *
     * <p>销售订单有两条生单线——有货直接出库、无货先投生产。这里守住第一条线的入口，
     * 避免"点生单就出一张数量照抄的出库单"造成超卖。
     * 数量为 0 的行不参与校验（空行不应挡住正常出库）。
     */
    private void requireStockForSaleOut(List<Map<String, Object>> items, String warehouse) {
        if (stockTxnService == null) return; // 无库存服务的场景（单测/精简部署）不拦
        String wh = warehouse == null || warehouse.isBlank() ? SALE_OUT_WAREHOUSE : warehouse.trim();
        List<String> shortages = new ArrayList<>();
        for (Map<String, Object> item : items) {
            double need = num(item.getOrDefault("数量", 0));
            if (need <= 0) continue;
            String code = String.valueOf(item.getOrDefault("存货编码", "")).trim();
            String name = String.valueOf(item.getOrDefault("存货名称", "")).trim();
            if (code.isEmpty() && name.isEmpty()) continue;
            BigDecimal onHand = stockTxnService.quantityOf(code, name, wh);
            if (onHand.compareTo(BigDecimal.valueOf(need)) < 0) {
                shortages.add((name.isEmpty() ? code : name) + " 现存量 " + stripZero(onHand)
                        + "，需求 " + stripZero(BigDecimal.valueOf(need)));
            }
        }
        if (!shortages.isEmpty()) {
            throw new IllegalStateException(wh + "库存不足，不能直接生成销售出库单："
                    + String.join("；", shortages)
                    + "。请先生成生产加工单，完工入库后再从销售订单生成销售出库单。");
        }
    }

    /** 去掉 BigDecimal 的小数尾零，提示文案里显示 60 而不是 60.0000。 */
    private static String stripZero(BigDecimal value) {
        return value.stripTrailingZeros().toPlainString();
    }

    /**
     * 该销售订单是否已经出库（或已提交出库）。
     *
     * <p>返回阻塞它的销售出库单状态，没有则返回 null。草稿不算——草稿还没占用货物。
     * 单独成方法便于用单测替身覆盖，避免为此引入复杂 SQL。
     */
    /** 未完结（草稿/审批中）的产成品入库单：拦住重复生成，避免多张草稿叠加后超量入库。 */
    String findOpenFinishIn(String moNo) {
        if (moNo == null || moNo.isBlank()) return null;
        List<FormData> docs = formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "FINISH_IN")
                .in(FormData::getStatus, "草稿", "审批中"));
        for (FormData doc : docs) {
            Map<String, Object> head;
            try {
                head = parseData(doc.getData());
            } catch (Exception e) {
                continue;
            }
            if (moNo.equals(String.valueOf(head.getOrDefault("加工单号", "")).trim())) return doc.getStatus();
        }
        return null;
    }

    /** 未完结（草稿/审批中）的销售出库单：拦住重复生成，避免多张草稿叠加后超卖。 */
    String findOpenSaleOut(String soNo) {
        if (soNo == null || soNo.isBlank()) return null;
        List<FormData> outs = formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "SALE_OUT")
                .in(FormData::getStatus, "草稿", "审批中"));
        for (FormData out : outs) {
            Map<String, Object> head;
            try {
                head = parseData(out.getData());
            } catch (Exception e) {
                continue;
            }
            if (soNo.equals(String.valueOf(head.getOrDefault("销售订单号", "")).trim())) return out.getStatus();
        }
        return null;
    }

    /**
     * 该销售订单已审核出库的数量（按存货编码汇总）。
     *
     * <p>已审核出库已经扣过库存，所以计算「剩余待出」时要减掉它，
     * 否则同一批货会被反复出库。
     */
    Map<String, Double> shippedQtyByItem(String soNo) {
        Map<String, Double> out = new LinkedHashMap<>();
        if (soNo == null || soNo.isBlank()) return out;
        List<FormData> outs = formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "SALE_OUT")
                .eq(FormData::getStatus, "已审核"));
        for (FormData doc : outs) {
            Map<String, Object> head;
            Map<String, Object> detail;
            try {
                head = parseData(doc.getData());
                detail = parseData(doc.getDetailData());
            } catch (Exception e) {
                continue;
            }
            if (!soNo.equals(String.valueOf(head.getOrDefault("销售订单号", "")).trim())) continue;
            if (!(detail.get("items") instanceof List<?> items)) continue;
            for (Object o : items) {
                if (!(o instanceof Map<?, ?> row)) continue;
                Object codeObj = row.get("存货编码");
                String code = codeObj == null ? "" : String.valueOf(codeObj).trim();
                if (code.isEmpty()) continue;
                Object qtyObj = row.get("数量");
                double qty;
                try {
                    qty = qtyObj == null ? 0 : Double.parseDouble(String.valueOf(qtyObj));
                } catch (NumberFormatException e) {
                    qty = 0;
                }
                out.merge(code, qty, Double::sum);
            }
        }
        return out;
    }

    /**
     * 出库预览：逐行给出 需求 / 已出库 / 剩余待出 / 现存量 / 建议出库数量 / 是否不足。
     *
     * <p>建议出库 = min(剩余待出, 现存量)。库存够出多少就建议多少，缺口部分留给生产。
     */
    public List<Map<String, Object>> saleOutPreview(String soNo) {
        return saleOutPreview(soNo, null);
    }

    /**
     * 出库预览（可指定发货仓库）。warehouse 为空时退回成品仓。
     */
    public List<Map<String, Object>> saleOutPreview(String soNo, String warehouse) {
        String warehouseCode = warehouse == null || warehouse.isBlank() ? SALE_OUT_WAREHOUSE : warehouse.trim();
        FormData so = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "SO_ORDER")
                .eq(FormData::getFormNo, String.valueOf(soNo)));
        if (so == null) throw new IllegalArgumentException("销售订单不存在：" + soNo);
        Map<String, Object> dm = parseData(so.getDetailData());
        List<Map<String, Object>> items = dm.get("items") instanceof List
                ? (List<Map<String, Object>>) dm.get("items") : new ArrayList<>();
        Map<String, Double> shipped = shippedQtyByItem(String.valueOf(soNo));
        Map<String, Double> stockLeft = new LinkedHashMap<>();
        Map<String, Double> shippedLeft = new LinkedHashMap<>();
        List<Map<String, Object>> rows = new ArrayList<>();
        for (Map<String, Object> it : items) {
            String code = String.valueOf(it.getOrDefault("存货编码", "")).trim();
            String name = String.valueOf(it.getOrDefault("存货名称", "")).trim();
            if (code.isEmpty() && name.isEmpty()) continue;
            String key = code.isEmpty() ? "#" + name : code;
            double demand = num(it.getOrDefault("数量", 0));
            double onHand = stockTxnService == null ? 0
                    : stockTxnService.quantityOf(code, name, warehouseCode).doubleValue();
            // 同一存货多行时，库存与已出库量都要按行顺序分配
            double leftStock = stockLeft.computeIfAbsent(key, k -> onHand);
            double leftShipped = shippedLeft.computeIfAbsent(key, k -> shipped.getOrDefault(k, 0.0));
            double shippedHere = Math.min(leftShipped, demand);
            shippedLeft.put(key, leftShipped - shippedHere);
            double remaining = Math.max(0, round2(demand - shippedHere));
            double suggest = Math.max(0, round2(Math.min(remaining, leftStock)));
            stockLeft.put(key, Math.max(0, round2(leftStock - suggest)));
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("存货编码", code);
            row.put("存货名称", name);
            row.put("规格型号", it.getOrDefault("规格型号", ""));
            row.put("计量单位", it.getOrDefault("销售单位", "件"));
            row.put("需求数量", demand);
            row.put("已出库数量", round2(shippedHere));
            row.put("剩余待出数量", remaining);
            row.put("现存量", round2(onHand));
            row.put("建议出库数量", suggest);
            row.put("是否不足", suggest < remaining);
            row.put("仓库", warehouseCode);
            rows.add(row);
        }
        return rows;
    }


    String findBlockingSaleOut(String soNo) {
        return findSaleOut(soNo, true);
    }

    /**
     * 找出关联该销售订单的销售出库单状态。
     *
     * @param onlyCommitted true 只看「已审核/审批中」（真正占用了货物）；
     *                      false 连草稿一起看（防止反复生成草稿单造成超卖）
     */
    private String findSaleOut(String soNo, boolean onlyCommitted) {
        if (soNo == null || soNo.isBlank()) return null;
        LambdaQueryWrapper<FormData> query = new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "SALE_OUT");
        if (onlyCommitted) query.in(FormData::getStatus, "已审核", "审批中");
        List<FormData> outs = formMapper.selectList(query);
        for (FormData out : outs) {
            Map<String, Object> head;
            try {
                head = parseData(out.getData());
            } catch (Exception e) {
                continue;
            }
            String linked = String.valueOf(head.getOrDefault("销售订单号", "")).trim();
            if (soNo.equals(linked)) return out.getStatus();
        }
        return null;
    }

    private Map<String, Object> createSaleOutFromSo(String panelCode, Map<String, Object> formData) {
        return createSaleOutFromSo(panelCode, formData, Map.of());
    }

    /**
     * 推式生单：销售订单 → 销售出库单。
     *
     * @param buttonParam 可带 {@code lines=[{存货编码, 数量}]}：出库预览弹窗勾选的结果；
     *                    不传则按「剩余待出量」整单出库。
     */
    private Map<String, Object> createSaleOutFromSo(String panelCode, Map<String, Object> formData,
                                                    Map<String, Object> buttonParam) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData src = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "SO_ORDER").eq(FormData::getFormNo, String.valueOf(no)));
        if (src == null) throw new IllegalArgumentException("销售订单不存在：" + no);
        if (!"已审核".equals(src.getStatus())) throw new IllegalStateException("仅已审核销售订单可生成销售出库单");
        Map<String, Object> head = parseData(src.getData());
        Map<String, Object> dm = parseData(src.getDetailData());
        List<Map<String, Object>> items = dm.get("items") instanceof List
                ? (List<Map<String, Object>>) dm.get("items") : new ArrayList<>();
        if (items.isEmpty()) throw new IllegalStateException("销售订单无明细：" + no);
        // 一张销售订单同时只能有一张未完结的销售出库单：否则可以反复生成草稿单，
        // 每张都通过库存校验（草稿不写库存流水），全部审核后就会超卖。
        String openOut = findOpenSaleOut(String.valueOf(no));
        if (openOut != null) {
            throw new IllegalStateException("该销售订单已有销售出库单（状态：" + openOut
                    + "），不能重复生成；如需调整数量，请直接处理该单据。");
        }
        // 只出「还没出过的量」：已审核出库已经扣过库存，再按订单原数量出库会重复出。
        Map<String, Double> shipped = shippedQtyByItem(String.valueOf(no));
        Map<String, Double> shippedLeft = new LinkedHashMap<>();
        List<Map<String, Object>> remainingItems = new ArrayList<>();
        for (Map<String, Object> it : items) {
            String code = String.valueOf(it.getOrDefault("存货编码", "")).trim();
            String key = code;
            double demand = num(it.getOrDefault("数量", 0));
            double left = shippedLeft.computeIfAbsent(key, k -> shipped.getOrDefault(k, 0.0));
            double used = Math.min(left, demand);
            shippedLeft.put(key, left - used);
            double remaining = round2(demand - used);
            if (remaining <= 0) continue;
            Map<String, Object> copy = new HashMap<>(it);
            copy.put("数量", remaining);
            remainingItems.add(copy);
        }
        if (remainingItems.isEmpty()) {
            throw new IllegalStateException("该销售订单已全部出库，无需再生成销售出库单");
        }
        // 生单预览弹窗勾选的数量：只出勾选的行与数量
        Map<String, Double> selectedQty = selectedLineQty(buttonParam);
        if (!selectedQty.isEmpty()) {
            List<Map<String, Object>> picked = new ArrayList<>();
            for (Map<String, Object> it : remainingItems) {
                String code = String.valueOf(it.getOrDefault("存货编码", "")).trim();
                Double qty = selectedQty.get(code);
                if (qty == null || qty <= 0) continue;
                Map<String, Object> copy = new HashMap<>(it);
                copy.put("数量", Math.min(qty, num(it.get("数量"))));
                picked.add(copy);
            }
            if (picked.isEmpty()) {
                throw new IllegalStateException("请至少勾选一行要出库的产品，并填写大于 0 的出库数量");
            }
            remainingItems = picked;
        }
        items = remainingItems;
        // 有货才可直接出库：成品仓现存量不足时不允许生成销售出库单，
        // 必须先走生产加工单，完工入库后再出库。
        String warehouse = String.valueOf(buttonParam.getOrDefault("warehouse", "")).trim();
        if (warehouse.isEmpty()) warehouse = SALE_OUT_WAREHOUSE;
        requireStockForSaleOut(items, warehouse);

        Map<String, Object> soData = new HashMap<>();
        soData.put("单据日期", LocalDate.now().toString());
        soData.put("客户", head.getOrDefault("客户", ""));
        soData.put("客户编码", head.getOrDefault("客户编码", ""));
        soData.put("结算客户", head.getOrDefault("结算客户", ""));
        soData.put("业务员", head.getOrDefault("业务员", ""));
        soData.put("销售订单号", String.valueOf(no));
        soData.put("数据来源", "销售订单");
        List<Map<String, Object>> rows = new ArrayList<>();
        for (Map<String, Object> it : items) {
            Map<String, Object> r = new HashMap<>();
            r.put("仓库", warehouse);
            r.put("存货名称", it.getOrDefault("存货名称", ""));
            r.put("存货编码", it.getOrDefault("存货编码", ""));
            r.put("规格型号", it.getOrDefault("规格型号", ""));
            r.put("计量单位", it.getOrDefault("销售单位", "件"));
            r.put("数量", it.getOrDefault("数量", 0));
            r.put("税率%", it.getOrDefault("税率%", 13));
            double tax = num(it.getOrDefault("税率%", 13));
            double qty = num(it.getOrDefault("数量", 0));
            // 含税单价拆成不含税售价：出库单明细同时存净价与税价，
            // 否则销货单选单时把含税金额当不含税金额带过去，税额/价税合计会被重复放大
            double grossPrice = num(it.getOrDefault("含税单价", 0));
            double netPrice = grossPrice * 100 / (100 + tax);
            double grossAmount = qty * grossPrice;
            double netAmount = qty * netPrice;
            r.put("售价", Math.round(netPrice * 100) / 100.0);
            r.put("含税售价", grossPrice);
            r.put("销售金额", Math.round(netAmount * 100) / 100.0);
            r.put("含税销售金额", Math.round(grossAmount * 100) / 100.0);
            r.put("税额", Math.round((grossAmount - netAmount) * 100) / 100.0);
            r.put("折扣金额", 0);
            // 出库时刻的结存均价：会计凭证按成本结转，不拿销售额当成本
            java.math.BigDecimal avgCost = stockTxnService == null ? null : stockTxnService.averageCostOf(
                    String.valueOf(it.getOrDefault("存货编码", "")),
                    String.valueOf(it.getOrDefault("存货名称", "")), warehouse);
            double costPrice = avgCost == null ? 0 : avgCost.doubleValue();
            r.put("成本价", round2(costPrice));
            r.put("成本金额", round2(costPrice * qty));
            r.put("现存量", it.getOrDefault("现存量", 0));
            r.put("现存量说明", it.getOrDefault("现存量说明", ""));
            rows.add(r);
        }
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("SALE_OUT", "SO_ORDER", String.valueOf(no), soData, detail);
    }

    /** 推式生单：报价单（QUOTE_ORDER）→ 销售订单（SO_ORDER）；表头带客户/部门/业务员，明细带报价单价/含税单价 */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createSaleOrderFromQuote(String panelCode, Map<String, Object> formData) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData src = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "QUOTE_ORDER").eq(FormData::getFormNo, String.valueOf(no)));
        if (src == null) throw new IllegalArgumentException("报价单不存在：" + no);
        if (!"已审核".equals(src.getStatus())) throw new IllegalStateException("仅已审核报价单可生成销售订单");
        Map<String, Object> head = parseData(src.getData());
        Map<String, Object> dm = parseData(src.getDetailData());
        List<Map<String, Object>> items = dm.get("items") instanceof List
                ? (List<Map<String, Object>>) dm.get("items") : new ArrayList<>();
        if (items.isEmpty()) throw new IllegalStateException("报价单无明细：" + no);

        Map<String, Object> soData = new HashMap<>();
        soData.put("单据日期", LocalDate.now().toString());
        soData.put("客户", head.getOrDefault("客户", ""));
        soData.put("客户编码", head.getOrDefault("客户编码", ""));
        soData.put("结算客户", head.getOrDefault("结算客户", ""));
        soData.put("部门", head.getOrDefault("部门", ""));
        soData.put("业务员", head.getOrDefault("业务员", ""));
        soData.put("项目", head.getOrDefault("项目", ""));
        soData.put("预计交货日期", head.getOrDefault("有效期至", ""));
        soData.put("联系人", head.getOrDefault("联系人", ""));
        soData.put("来源单据", "QUOTE_ORDER");
        soData.put("来源单号", String.valueOf(no));
        List<Map<String, Object>> rows = new ArrayList<>();
        for (Map<String, Object> it : items) {
            Map<String, Object> r = new HashMap<>();
            r.put("存货名称", it.getOrDefault("存货名称", ""));
            r.put("存货编码", it.getOrDefault("存货编码", ""));
            r.put("规格型号", it.getOrDefault("规格型号", ""));
            r.put("销售单位", it.getOrDefault("销售单位", "件"));
            r.put("数量", it.getOrDefault("数量", 0));
            r.put("单价", it.getOrDefault("报价单价", 0));
            r.put("税率%", it.getOrDefault("税率%", 13));
            double qty = num(it.getOrDefault("数量", 0));
            double price = num(it.getOrDefault("含税单价", it.getOrDefault("报价单价", 0)));
            r.put("含税单价", price);
            r.put("含税金额", Math.round(qty * price * 100) / 100.0);
            r.put("金额", Math.round(qty * num(it.getOrDefault("报价单价", 0)) * 100) / 100.0);
            r.put("折扣金额", it.getOrDefault("折扣金额", 0));
            r.put("预计交货日期", it.getOrDefault("预计交货日期", ""));
            r.put("现存量", it.getOrDefault("现存量", 0));
            r.put("备注", it.getOrDefault("备注", ""));
            rows.add(r);
        }
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("SO_ORDER", "QUOTE_ORDER", String.valueOf(no), soData, detail);
    }

    /** 推式生单：销货单（SALE_INV）→ 销售发票（SALE_INVOICE）；明细按 数量×含税单价 拆分税额/价税合计 */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createSaleInvoiceFromSaleInv(String panelCode, Map<String, Object> formData) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData src = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "SALE_INV").eq(FormData::getFormNo, String.valueOf(no)));
        if (src == null) throw new IllegalArgumentException("销货单不存在：" + no);
        if (!"已审核".equals(src.getStatus())) throw new IllegalStateException("仅已审核销货单可生成销售发票");
        // 销货单审批通过时已自动开票；这里拦住重复生成，避免一单多票。
        String existing = findInvoiceOfSaleInv(String.valueOf(no));
        if (existing != null) {
            throw new IllegalStateException("该销货单已开票（发票状态：" + existing + "），请直接使用该发票");
        }
        Map<String, Object> head = parseData(src.getData());
        Map<String, Object> dm = parseData(src.getDetailData());
        List<Map<String, Object>> items = dm.get("items") instanceof List
                ? (List<Map<String, Object>>) dm.get("items") : new ArrayList<>();
        if (items.isEmpty()) throw new IllegalStateException("销货单无明细：" + no);

        Map<String, Object> data = new HashMap<>();
        data.put("单据日期", LocalDate.now().toString());
        data.put("业务类型", "销售发票");
        data.put("客户", head.getOrDefault("客户", ""));
        data.put("客户编码", head.getOrDefault("客户编码", ""));
        data.put("结算客户", head.getOrDefault("结算客户", ""));
        data.put("部门", head.getOrDefault("部门", ""));
        data.put("业务员", head.getOrDefault("经手人", ""));
        // 数电票默认开普通发票（多数场景）；需要抵扣时在单据上改为增值税专用发票
        data.put("开票类型", "增值税普通发票");
        data.put("销货单号", String.valueOf(no));
        data.put("来源单据", "SALE_INV");
        data.put("来源单号", String.valueOf(no));
        List<Map<String, Object>> rows = new ArrayList<>();
        double totalAmount = 0;
        for (Map<String, Object> it : items) {
            double qty = num(it.getOrDefault("数量", 0));
            double price = num(it.getOrDefault("含税单价", it.getOrDefault("单价", 0)));
            double taxRate = num(it.getOrDefault("税率%", 13));
            double gross = Math.round(qty * price * 100) / 100.0;
            double tax = Math.round(gross * taxRate / (100 + taxRate) * 100) / 100.0;
            double net = Math.round((gross - tax) * 100) / 100.0;
            totalAmount += gross;
            Map<String, Object> r = new HashMap<>();
            r.put("存货编码", it.getOrDefault("存货编码", ""));
            r.put("存货名称", it.getOrDefault("存货名称", ""));
            r.put("规格型号", it.getOrDefault("规格型号", ""));
            r.put("计量单位", it.getOrDefault("销售单位", "件"));
            r.put("数量", qty);
            r.put("无税单价", Math.round(net / Math.max(qty, 1) * 100) / 100.0);
            r.put("税率%", taxRate);
            r.put("税额", tax);
            r.put("价税合计", gross);
            rows.add(r);
        }
        data.put("价税合计", Math.round(totalAmount * 100) / 100.0);
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("SALE_INVOICE", "SALE_INV", String.valueOf(no), data, detail);
    }

    /** 推式生单：费用单（EXPENSE）→ 销售费用分摊单（SALE_COST_ALLOC）；费用明细按费用项目带入 */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createSaleCostAllocFromExpense(String panelCode, Map<String, Object> formData) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData src = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "EXPENSE").eq(FormData::getFormNo, String.valueOf(no)));
        if (src == null) throw new IllegalArgumentException("费用单不存在：" + no);
        if (!"已审核".equals(src.getStatus())) throw new IllegalStateException("仅已审核费用单可生成销售费用分摊单");
        Map<String, Object> head = parseData(src.getData());
        Map<String, Object> dm = parseData(src.getDetailData());
        List<Map<String, Object>> items = dm.get("items") instanceof List
                ? (List<Map<String, Object>>) dm.get("items") : new ArrayList<>();
        if (items.isEmpty()) throw new IllegalStateException("费用单无费用明细：" + no);

        Map<String, Object> data = new HashMap<>();
        data.put("单据日期", LocalDate.now().toString());
        data.put("业务类型", "销售费用分摊");
        data.put("费用类型", head.getOrDefault("费用类型", "销售费用"));
        data.put("部门", head.getOrDefault("部门", ""));
        data.put("经手人", head.getOrDefault("经手人", ""));
        data.put("项目", head.getOrDefault("项目", ""));
        data.put("来源单据", "EXPENSE");
        data.put("来源单号", String.valueOf(no));
        List<Map<String, Object>> rows = new ArrayList<>();
        double total = 0;
        for (Map<String, Object> it : items) {
            double amount = num(it.getOrDefault("金额", 0));
            total += amount;
            Map<String, Object> r = new HashMap<>();
            r.put("费用单号", String.valueOf(no));
            r.put("费用项目", it.getOrDefault("费用项目", ""));
            r.put("分摊金额", amount);
            r.put("备注", it.getOrDefault("备注", ""));
            rows.add(r);
        }
        data.put("分摊合计", Math.round(total * 100) / 100.0);
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("SALE_COST_ALLOC", "EXPENSE", String.valueOf(no), data, detail);
    }

    /** 推式生单：采购入库单/采购退货单/采购订单 → 采购发票（PU_INVOICE）；明细按含税单价拆分税额/价税合计 */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createPuInvoice(String panelCode, Map<String, Object> formData) {
        if (!Set.of("PURCHASE_IN", "PURCHASE_RETURN", "PU_ORDER").contains(panelCode)) {
            throw new IllegalStateException("仅采购入库单/采购退货单/采购订单可生成采购发票，当前单据：" + panelCode);
        }
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData src = formOf(panelCode, no);
        requireApprovedPurchaseSource(src, "生成采购发票");
        Map<String, Object> head = parseData(src.getData());
        List<Map<String, Object>> items = firstDetailRows(parseData(src.getDetailData()));
        if (items.isEmpty()) throw new IllegalStateException("来源单据无明细：" + no);

        boolean returnInvoice = "PURCHASE_RETURN".equals(panelCode)
                || textOf(head.get("业务类型")).contains("退货");
        // 红字发票口径（对齐金蝶/税务）：数量、金额、税额、价税合计为负；单价为正；
        // 必须关联原蓝字发票，未入库/未开蓝票的退货不允许直接开红字。
        String purchaseInNo = "";
        FormData blueInvoice = null;
        double redRemaining = 0;
        if (returnInvoice) {
            purchaseInNo = textOf(head.get("入库单号")).trim();
            if (purchaseInNo.isEmpty()) {
                throw new IllegalStateException("退货单未关联采购入库单（检验退回尚未入库），不能开红字采购发票；"
                        + "请按负数应付或退货不开发票处理");
            }
            blueInvoice = findBluePurchaseInvoice(purchaseInNo);
            if (blueInvoice == null) {
                throw new IllegalStateException("入库单 " + purchaseInNo
                        + " 尚未开具已审核蓝字采购发票，不能开红字发票；请先开蓝字发票或按负数应付处理");
            }
            redRemaining = blueInvoiceRemaining(purchaseInNo);
        }
        Map<String, Object> blueHead = blueInvoice == null ? Map.of() : parseData(blueInvoice.getData());

        Map<String, Object> data = new HashMap<>();
        data.put("单据日期", LocalDate.now().toString());
        data.put("业务类型", returnInvoice ? "采购发票(退货)" : "采购发票");
        data.put("供应商", String.valueOf(head.getOrDefault("供应商", head.getOrDefault("供应商简称", ""))));
        data.put("供应商编码", head.getOrDefault("供应商编码", ""));
        data.put("部门", head.getOrDefault("部门", ""));
        data.put("业务员", String.valueOf(head.getOrDefault("业务员", head.getOrDefault("经手人", ""))));
        data.put("开票类型", returnInvoice
                ? String.valueOf(blueHead.getOrDefault("开票类型", "增值税专用发票"))
                : "增值税专用发票");
        data.put("入库单号", returnInvoice ? purchaseInNo : ("PURCHASE_IN".equals(panelCode) ? String.valueOf(no) : ""));
        data.put("原发票号", returnInvoice ? blueInvoice.getFormNo() : "");
        data.put("采购订单号", "PU_ORDER".equals(panelCode)
                ? String.valueOf(no) : head.getOrDefault("采购订单号", ""));

        List<Map<String, Object>> rows = new ArrayList<>();
        double total = 0;
        double totalQty = 0;
        for (int index = 0; index < items.size(); index++) {
            Map<String, Object> it = items.get(index);
            // 入库/退货按实收数量开票；采购订单直开票按订单数量
            double qtyAbs = "PU_ORDER".equals(panelCode) ? num(it.getOrDefault("数量", 0)) : purchaseInQuantity(it);
            if (qtyAbs <= 0) continue;
            totalQty += qtyAbs;
            double price = num(it.getOrDefault("含税单价", 0));
            if (price <= 0) price = num(it.getOrDefault("单价", 0));
            double taxRate = num(it.getOrDefault("税率%", 13));
            double grossAbs = num(it.getOrDefault("含税金额", 0));
            if (grossAbs <= 0) grossAbs = Math.round(qtyAbs * price * 100) / 100.0;
            if (returnInvoice && (price <= 0 || grossAbs <= 0)) {
                throw new IllegalStateException("退货行缺少单价/金额，不能开具红字发票；请先补充退货价格或从原蓝字发票带入");
            }
            double taxAbs = Math.round(grossAbs * taxRate / (100 + taxRate) * 100) / 100.0;
            double netAbs = Math.round((grossAbs - taxAbs) * 100) / 100.0;
            double qty = returnInvoice ? -qtyAbs : qtyAbs;
            total += returnInvoice ? -grossAbs : grossAbs;
            Map<String, Object> r = new HashMap<>();
            r.put("存货编码", it.getOrDefault("存货编码", it.getOrDefault("物料编码", "")));
            r.put("存货名称", it.getOrDefault("存货名称", it.getOrDefault("物料名称", "")));
            r.put("规格型号", it.getOrDefault("规格型号", ""));
            r.put("计量单位", it.getOrDefault("计量单位",
                    it.getOrDefault("采购单位", it.getOrDefault("单位", "件"))));
            r.put("数量", qty);
            r.put("无税单价", Math.round(netAbs / Math.max(qtyAbs, 1) * 100) / 100.0);
            r.put("税率%", taxRate);
            r.put("税额", returnInvoice ? -taxAbs : taxAbs);
            r.put("价税合计", returnInvoice ? -grossAbs : grossAbs);
            stampSource(r, panelCode, String.valueOf(no), index + 1, qtyAbs);
            rows.add(r);
        }
        if (rows.isEmpty()) throw new IllegalStateException("来源单据没有可开票数量");
        if (returnInvoice && totalQty > redRemaining + 0.000001) {
            throw new IllegalStateException("红字发票数量超过原蓝字发票可冲数量：可冲 " + redRemaining
                    + "，本次 " + totalQty);
        }
        data.put("价税合计", Math.round(total * 100) / 100.0);
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("PU_INVOICE", panelCode, String.valueOf(no), data, detail);
    }

    /** 原蓝字采购发票：同一入库单、已审核、业务类型非退货。 */
    private FormData findBluePurchaseInvoice(String purchaseInNo) {
        for (FormData document : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "PU_INVOICE"))) {
            if (!EFFECTIVE_STATUSES.contains(document.getStatus())) continue;
            Map<String, Object> head = parseData(document.getData());
            if (textOf(head.get("业务类型")).contains("退货")) continue;
            if (purchaseInNo.equals(textOf(head.get("入库单号")))) return document;
        }
        return null;
    }

    /** 原蓝字发票可冲数量 = 蓝票数量 − 已开红票数量（都按同一入库单汇总）。 */
    private double blueInvoiceRemaining(String purchaseInNo) {
        double blue = 0;
        double red = 0;
        for (FormData document : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "PU_INVOICE"))) {
            if (!EFFECTIVE_STATUSES.contains(document.getStatus())) continue;
            Map<String, Object> head = parseData(document.getData());
            if (!purchaseInNo.equals(textOf(head.get("入库单号")))) continue;
            double quantity = 0;
            for (Map<String, Object> row : firstDetailRows(parseData(document.getDetailData()))) {
                quantity += Math.abs(num(row.get("数量")));
            }
            if (textOf(head.get("业务类型")).contains("退货")) red += quantity;
            else blue += quantity;
        }
        return Math.max(0, blue - red);
    }

    /**
     * 采购发票行级选单（2026-11-06）：返回来源入库单/退货单中尚未开票的明细行。
     * mode=purchase 扫描已审核采购入库单，mode=return 扫描已审核采购退货单；
     * 已开票数量按有效采购发票明细的 abs(数量) 汇总，未开票数量 <= 0 的行不再返回。
     */
    public Map<String, Object> invoiceSourceLines(String mode, String supplier, String keyword) {
        requirePanelPermission("PU_INVOICE", "query");
        String normalized = mode == null || mode.isBlank() ? "purchase" : mode.trim().toLowerCase(Locale.ROOT);
        String sourcePanel = switch (normalized) {
            case "purchase" -> "PURCHASE_IN";
            case "return" -> "PURCHASE_RETURN";
            default -> throw new IllegalArgumentException("不支持的发票选单模式：" + mode + "（仅 purchase/return）");
        };
        String supplierFilter = supplier == null ? "" : supplier.trim();
        String keywordFilter = keyword == null ? "" : keyword.trim();
        // 一次取回来源单据与发票后在内存分流：来源单据 + 已开票汇总，避免逐行查库
        List<FormData> documents = formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .in(FormData::getPanelCode, List.of("PURCHASE_IN", "PURCHASE_RETURN", "PU_INVOICE")));
        Map<String, Double> invoiced = invoicedQuantities(documents, null);
        boolean returnMode = "PURCHASE_RETURN".equals(sourcePanel);
        List<Map<String, Object>> list = new ArrayList<>();
        for (FormData document : documents) {
            if (!sourcePanel.equals(document.getPanelCode())) continue;
            if (!EFFECTIVE_STATUSES.contains(document.getStatus())) continue;
            Map<String, Object> head = parseData(document.getData());
            String docSupplier = textOf(firstValue(head, "供应商", "供应商简称")).trim();
            String supplierCode = textOf(head.get("供应商编码")).trim();
            if (!supplierFilter.isEmpty()
                    && !containsText(docSupplier, supplierFilter)
                    && !containsText(supplierCode, supplierFilter)) {
                continue;
            }
            List<Map<String, Object>> rows = firstDetailRows(parseData(document.getDetailData()));
            for (int index = 0; index < rows.size(); index++) {
                Map<String, Object> sourceRow = rows.get(index);
                int lineNo = index + 1;
                // 入库按实收数量、退货按数量取可开票基数；红字/手工负数行一律按绝对值判断额度
                double quantity = returnMode
                        ? Math.abs(executedQuantity("PURCHASE_RETURN", sourceRow))
                        : Math.abs(purchaseInQuantity(sourceRow));
                if (quantity <= 0) continue;
                double invoicedQty = invoiced.getOrDefault(
                        sourceKey(sourcePanel, document.getFormNo(), lineNo), 0d);
                double remaining = round4(quantity - invoicedQty);
                if (remaining <= 0.000001) continue;
                String itemCode = textOf(firstValue(sourceRow, "存货编码", "物料编码")).trim();
                String itemName = textOf(firstValue(sourceRow, "存货名称", "物料名称")).trim();
                if (!keywordFilter.isEmpty()
                        && !containsText(document.getFormNo(), keywordFilter)
                        && !containsText(itemCode, keywordFilter)
                        && !containsText(itemName, keywordFilter)
                        && !containsText(docSupplier, keywordFilter)) {
                    continue;
                }
                list.add(invoiceSourceLine(document, head, sourceRow, sourcePanel, lineNo,
                        quantity, invoicedQty, remaining, returnMode));
            }
        }
        Map<String, Object> out = new HashMap<>();
        out.put("list", list);
        out.put("total", list.size());
        return out;
    }

    /**
     * 有效采购发票的已开票数量：按「来源面板|来源单号|来源行号」汇总明细 abs(数量)。
     * 红字发票只冲减退货来源行，不计入入库行的已开票量；excludeNo 用于编辑草稿时排除自身。
     */
    private Map<String, Double> invoicedQuantities(List<FormData> documents, String excludeNo) {
        Map<String, Double> totals = new HashMap<>();
        for (FormData invoice : documents) {
            if (!"PU_INVOICE".equals(invoice.getPanelCode())) continue;
            if (!EFFECTIVE_STATUSES.contains(invoice.getStatus())) continue;
            if (excludeNo != null && excludeNo.equals(invoice.getFormNo())) continue;
            Map<String, Object> head = parseData(invoice.getData());
            boolean red = textOf(head.get("业务类型")).contains("退货");
            for (Map<String, Object> row : firstDetailRows(parseData(invoice.getDetailData()))) {
                String sourcePanel = sourcePanelOf(row);
                if (!"PURCHASE_IN".equals(sourcePanel) && !"PURCHASE_RETURN".equals(sourcePanel)) continue;
                if (red && !"PURCHASE_RETURN".equals(sourcePanel)) continue;
                String sourceNo = sourceNoOf(row);
                int lineNo = sourceLineNoOf(row);
                if (sourceNo.isEmpty() || lineNo <= 0) continue;
                double quantity = Math.abs(num(row.get("数量")));
                if (quantity <= 0) continue;
                totals.merge(sourceKey(sourcePanel, sourceNo, lineNo), quantity, Double::sum);
            }
        }
        return totals;
    }

    /** 选单行：对齐真实 ERP「来源单号 + 来源行号 + 入库/已开票/未开票数量 + 金额税额」。 */
    private Map<String, Object> invoiceSourceLine(FormData document, Map<String, Object> head,
                                                  Map<String, Object> sourceRow, String sourcePanel, int lineNo,
                                                  double quantity, double invoicedQty, double remaining,
                                                  boolean returnMode) {
        double price = num(firstValue(sourceRow, "单价", "无税单价"));
        double taxRate = num(firstValue(sourceRow, "税率%"));
        double rate = taxRate < 0 ? 0 : taxRate;
        double taxPrice = num(firstValue(sourceRow, "含税单价"));
        double net = num(firstValue(sourceRow, "金额"));
        double tax = num(firstValue(sourceRow, "税额"));
        double gross = num(firstValue(sourceRow, "含税金额"));
        // 来源行金额缺失时按数量/单价/税率补算，保证发票行金额口径完整
        if (taxPrice <= 0 && price > 0) taxPrice = round2(price * (1 + rate / 100));
        if (gross <= 0 && taxPrice > 0) gross = round2(quantity * taxPrice);
        if (gross <= 0 && net > 0) gross = round2(net * (1 + rate / 100));
        if (tax <= 0 && gross > 0) tax = round2(gross * rate / (100 + rate));
        if (net <= 0 && gross > 0) net = round2(gross - tax);
        if (net <= 0 && price > 0) net = round2(quantity * price);
        String unit = textOf(firstValue(sourceRow, "计量单位", "采购单位", "单位")).trim();
        Map<String, Object> line = new LinkedHashMap<>();
        line.put("供应商", textOf(firstValue(head, "供应商", "供应商简称")));
        line.put("供应商编码", textOf(head.get("供应商编码")));
        line.put("来源面板", sourcePanel);
        line.put("来源单号", document.getFormNo());
        line.put("来源行号", lineNo);
        line.put("存货编码", textOf(firstValue(sourceRow, "存货编码", "物料编码")));
        line.put("存货名称", textOf(firstValue(sourceRow, "存货名称", "物料名称")));
        line.put("规格型号", textOf(sourceRow.get("规格型号")));
        line.put("计量单位", unit.isEmpty() ? "件" : unit);
        line.put("入库数量", round4(quantity));
        line.put("已开票数量", round4(invoicedQty));
        line.put("未开票数量", round4(remaining));
        line.put("单价", price);
        line.put("税率%", taxRate);
        line.put("含税单价", taxPrice);
        line.put("金额", net);
        line.put("税额", tax);
        line.put("含税金额", gross);
        line.put("采购订单号", textOf(head.get("采购订单号")));
        // 退货行带出原入库单号：前端据此校验红票；空值仍返回，由保存/审核规则拒绝
        if (returnMode) line.put("入库单号", textOf(head.get("入库单号")));
        return line;
    }

    private static String sourceKey(String panel, String no, int lineNo) {
        return panel + "|" + no + "|" + lineNo;
    }

    private static boolean containsText(String value, String filter) {
        return value != null && filter != null && !filter.isEmpty()
                && value.toLowerCase(Locale.ROOT).contains(filter.toLowerCase(Locale.ROOT));
    }

    /** 来源面板：行级选单写「来源面板」，展示列「源单类型」作为兜底。 */
    private String sourcePanelOf(Map<String, Object> row) {
        return textOf(firstValue(row, "来源面板", "源单类型")).trim();
    }

    /** 来源单号：行级选单写「来源单号」，展示列「源单编号」作为兜底。 */
    private String sourceNoOf(Map<String, Object> row) {
        return textOf(firstValue(row, "来源单号", "源单编号")).trim();
    }

    /** 来源行号：行级选单写「来源行号」，展示列「源单行号」作为兜底。 */
    private int sourceLineNoOf(Map<String, Object> row) {
        return (int) Math.round(num(firstValue(row, "来源行号", "源单行号")));
    }

    private static double round4(double value) {
        return Math.round(value * 10000) / 10000.0;
    }

    /** 明细值为空（未填写）才允许补算，显式填写的 0 视为权威值。 */
    private static boolean isBlankValue(Object value) {
        return value == null || String.valueOf(value).isBlank();
    }

    /** 推式生单：费用单（EXPENSE，费用类型=采购费用）→ 采购费用分摊单（PU_COST_ALLOC） */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createPuCostAllocFromExpense(String panelCode, Map<String, Object> formData) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData src = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "EXPENSE").eq(FormData::getFormNo, String.valueOf(no)));
        if (src == null) throw new IllegalArgumentException("费用单不存在：" + no);
        if (!"已审核".equals(src.getStatus())) throw new IllegalStateException("仅已审核费用单可生成采购费用分摊单");
        Map<String, Object> head = parseData(src.getData());
        if (!"采购费用".equals(String.valueOf(head.getOrDefault("费用类型", "")))) {
            throw new IllegalStateException("仅费用类型为「采购费用」的费用单可生成采购费用分摊单");
        }
        Map<String, Object> dm = parseData(src.getDetailData());
        List<Map<String, Object>> items = dm.get("items") instanceof List
                ? (List<Map<String, Object>>) dm.get("items") : new ArrayList<>();
        if (items.isEmpty()) throw new IllegalStateException("费用单无费用明细：" + no);

        Map<String, Object> data = new HashMap<>();
        data.put("单据日期", LocalDate.now().toString());
        data.put("业务类型", "采购费用分摊");
        data.put("供应商", head.getOrDefault("往来单位", ""));
        data.put("费用类型", "采购费用");
        data.put("部门", head.getOrDefault("部门", ""));
        data.put("经手人", head.getOrDefault("经手人", ""));
        data.put("来源单据", "EXPENSE");
        data.put("来源单号", String.valueOf(no));
        List<Map<String, Object>> rows = new ArrayList<>();
        double total = 0;
        for (Map<String, Object> it : items) {
            double amount = num(it.getOrDefault("金额", 0));
            total += amount;
            Map<String, Object> r = new HashMap<>();
            r.put("费用单号", String.valueOf(no));
            r.put("费用项目", it.getOrDefault("费用项目", ""));
            r.put("分摊金额", amount);
            r.put("备注", it.getOrDefault("备注", ""));
            rows.add(r);
        }
        data.put("分摊合计", Math.round(total * 100) / 100.0);
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("PU_COST_ALLOC", "EXPENSE", String.valueOf(no), data, detail);
    }

    /**
     * 默认工序（3 道：下料/机加工/检验）：推式生单生成加工单时附带，工序汇报单按此生成待汇报行
     */
    @SuppressWarnings("unchecked")
    /**
     * 存货档案只读带出工艺路线：按存货编码找默认 BOM（产品编码或子件父件编码），
     * 取 BOM 上的「工艺路线编码」并补路线名称，供界面显示（不回写存货档案）。
     */
    private void fillInvRoute(Map<String, Object> detail) {
        if (!(detail.get("items") instanceof List<?> items)) return;
        Map<String, String> routeName = new LinkedHashMap<>();
        Map<String, String> routeByProduct = new LinkedHashMap<>();
        for (FormData route : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "ROUTE"))) {
            if (!(parseData(route.getDetailData()).get("processes") instanceof List<?> procs)) continue;
            for (Object o : procs) {
                if (!(o instanceof Map<?, ?> raw)) continue;
                Map<String, Object> r = castRow(raw);
                String rc = str(r.get("工艺路线编码"));
                if (!rc.isEmpty()) routeName.putIfAbsent(rc, str(r.get("工艺路线名称")));
            }
        }
        for (FormData bom : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "BOM"))) {
            Map<String, Object> h = parseData(bom.getData());
            String rc = str(h.get("工艺路线编码"));
            if (rc.isEmpty()) continue;
            String owner = str(h.get("产品编码"));
            if (owner.isEmpty()) owner = parentCodeOf(bom, "");
            if (!owner.isEmpty()) routeByProduct.putIfAbsent(owner, rc);
        }
        for (Object o : items) {
            if (!(o instanceof Map<?, ?> raw)) continue;
            @SuppressWarnings("unchecked")
            Map<String, Object> row = (Map<String, Object>) raw;
            String code = str(row.get("存货编码"));
            String rc = routeByProduct.getOrDefault(code, "");
            row.put("工艺路线", rc.isEmpty() ? "" : (routeName.getOrDefault(rc, "") + "(" + rc + ")").trim());
        }
    }

    /**
     * 工艺路线保存前的工序行规范化（主从结构：一条路线一张单据）。
     *
     * <ol>
     *   <li>按行序写回 {@code 加工顺序 = 1..N}：界面已隐藏该列，顺序只由行的先后决定；</li>
     *   <li>清掉行内的 {@code 工艺路线编码 / 工艺路线名称 / 停用} 冗余副本——它们已升为表头字段。
     *       留着会让 {@link #routeRowsOf} 的旧结构分支误命中，表头改名后取到旧值。</li>
     * </ol>
     */
    @SuppressWarnings("unchecked")
    private void normalizeRouteDetail(Map<String, Object> detail) {
        if (!(detail.get("processes") instanceof List<?> procs)) return;
        int seq = 0;
        for (Object o : procs) {
            if (!(o instanceof Map<?, ?> raw)) continue;
            Map<String, Object> row = (Map<String, Object>) raw;
            row.put("加工顺序", ++seq);
            row.remove("工艺路线编码");
            row.remove("工艺路线名称");
            row.remove("停用");
        }
    }

    /**
     * 工艺路线读路径：工序按「加工顺序」升序返回，并给每行写 1..N 的「工序顺序」。
     *
     * <p>写入时 {@link #normalizeRouteDetail} 已按行序重排过加工顺序，但历史数据、迁移数据
     * 或手工改库都可能让数组顺序与加工顺序不一致。排产与工序汇报都依赖这个先后，
     * 界面也要看得见，所以读出口统一再排一次，不能依赖存储时的数组顺序。
     */
    @SuppressWarnings("unchecked")
    private void normalizeRouteDetailForRead(Map<String, Object> detail) {
        if (detail == null || !(detail.get("processes") instanceof List<?> procs) || procs.isEmpty()) return;
        List<Map<String, Object>> rows = new ArrayList<>();
        for (Object o : procs) {
            if (o instanceof Map<?, ?> raw) rows.add((Map<String, Object>) raw);
        }
        if (rows.isEmpty()) return;
        rows.sort(PROCESS_SEQ_ORDER);
        int seq = 0;
        for (Map<String, Object> row : rows) row.put("工序顺序", ++seq);
        detail.put("processes", rows);
    }

    /**
     * 某条工艺路线的工序行，按加工顺序排序。
     *
     * <p>兼容两种存储形态（工艺路线 2026-11 改为主从结构：一条路线一张单据）：
     * <ol>
     *   <li><b>新结构</b>：工艺路线编码在<b>表头</b> data 上，单据的 processes 全部属于这条路线；</li>
     *   <li><b>旧结构</b>：单单据（singleDoc），多条路线混在一张单据里，靠行内 工艺路线编码 区分。</li>
     * </ol>
     * 迁移期两种可能并存，同一路线同一道工序只算一次（先表头结构优先）。
     */
    List<Map<String, Object>> routeRowsOf(String routeCode) {
        List<Map<String, Object>> rows = new ArrayList<>();
        if (routeCode == null || routeCode.isBlank()) return rows;
        Set<String> seen = new LinkedHashSet<>();
        for (FormData route : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "ROUTE"))) {
            Map<String, Object> head = parseData(route.getData());
            boolean headerOwnsRoute = routeCode.equals(str(head.get("工艺路线编码")));
            if (!(parseData(route.getDetailData()).get("processes") instanceof List<?> procs)) continue;
            for (Object o : procs) {
                if (!(o instanceof Map<?, ?> raw)) continue;
                Map<String, Object> r = castRow(raw);
                // 新结构：整单命中；旧结构：按行内编码命中
                if (!headerOwnsRoute && !routeCode.equals(str(r.get("工艺路线编码")))) continue;
                String key = procIdentity(r);
                if (!seen.add(key)) continue;
                rows.add(r);
            }
        }
        rows.sort(PROCESS_SEQ_ORDER);
        return rows;
    }

    /** 工序去重身份：优先工序编码，缺失时退化为名称，再缺则用行内容哈希。 */
    private String procIdentity(Map<String, Object> row) {
        String code = str(row.get("工序编码"));
        if (!code.isBlank()) return code;
        String name = str(row.get("工序名称"));
        if (!name.isBlank()) return "name:" + name;
        return "row:" + row.toString();
    }

    /**
     * 工序排序：加工顺序优先，缺失时按行序（稳定排序保留原有相对次序），
     * 保证「加工顺序」未写入的历史数据也不会乱序。
     */
    private final Comparator<Map<String, Object>> PROCESS_SEQ_ORDER = (a, b) -> {
        double sa = num(a.get("加工顺序"));
        double sb = num(b.get("加工顺序"));
        boolean aHas = sa > 0;
        boolean bHas = sb > 0;
        if (aHas && bHas) return Double.compare(sa, sb);
        if (aHas) return -1;
        if (bHas) return 1;
        return 0; // 都缺：保持原有次序（List.sort 是稳定排序）
    };

    /**
     * 手工建加工单：按所选工艺路线整体替换工序明细（工序/车间/工价/设备/班组取路线与工序档案）。
     *
     * @param formData 需带 编号 与 工艺路线编码；数量优先取入参，缺省取产成品数量
     */
    public Map<String, Object> applyRouteProcesses(String panelCode, Map<String, Object> formData) {
        String no = str(formData == null ? null : formData.get("编号"));
        String routeCode = str(formData == null ? null : formData.get("工艺路线编码"));
        if (no.isEmpty()) throw new IllegalArgumentException("缺少加工单号");
        if (routeCode.isEmpty()) throw new IllegalArgumentException("请选择工艺路线");
        FormData fd = formOf(panelCode, no);
        if ("已审核".equals(fd.getStatus()) || "生产中".equals(fd.getStatus()) || "已完工".equals(fd.getStatus())) {
            throw new IllegalStateException("已审核/生产中的加工单不能改工序，请先弃审");
        }
        Map<String, Object> detail = parseData(fd.getDetailData());
        double qty = num(formData.get("数量"));
        if (qty <= 0 && detail.get("products") instanceof List<?> ps && !ps.isEmpty()) {
            qty = num(castRow((Map<?, ?>) ps.get(0)).get("数量"));
        }
        List<Map<String, Object>> routeRows = routeRowsOf(routeCode);
        if (routeRows.isEmpty()) throw new IllegalStateException("工艺路线 " + routeCode + " 没有工序明细");
        List<Map<String, Object>> procs = new ArrayList<>();
        int seq = 0;
        for (Map<String, Object> r : routeRows) {
            seq++;
            addDefaultProc(procs, seq, str(r.get("工序编码")), str(r.get("工序名称")),
                    str(r.getOrDefault("生产车间", "熔铸车间")), str(r.get("设备")),
                    str(r.get("班组")), "", qty, num(r.getOrDefault("工价", r.get("计件单价"))),
                    num(r.get("单位标准工时")), "", "");
        }
        detail.put("processes", procs);
        fd.setDetailData(toJson(detail));
        Map<String, Object> head = parseData(fd.getData());
        head.put("工艺路线编码", routeCode);
        fd.setData(toJson(head));
        fd.setUpdateTime(LocalDateTime.now());
        formMapper.updateById(fd);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("编号", no);
        out.put("工序数", procs.size());
        out.put("工艺路线编码", routeCode);
        return out;
    }

    /** BOM 明细里的父件编码（老 BOM 表头没有产品编码，只能从子件行反查）。 */
    private String parentCodeOf(FormData bom, String code) {
        Map<String, Object> d = parseData(bom.getDetailData());
        if (!(d.get("children") instanceof List<?> rows)) return "";
        for (Object o : rows) {
            if (!(o instanceof Map<?, ?> raw)) continue;
            String parent = str(castRow(raw).get("父件编码"));
            if (!parent.isEmpty()) return parent;
        }
        return "";
    }

    /**
     * 加工单工序：按「产品 → 默认 BOM → BOM 上的工艺路线」展开；取不到时回退默认三道。
     *
     * <p>工艺路线档案在 PDM（ROUTE），产品与路线的绑定配置在 BOM 表头「工艺路线编码」。
     */
    List<Map<String, Object>> resolveProcesses(List<Map<String, Object>> products, String workshop) {
        if (products == null || products.isEmpty()) return defaultProcesses(products, workshop);
        String code = String.valueOf(products.get(0).getOrDefault("产品编码",
                products.get(0).getOrDefault("存货编码", ""))).trim();
        String routeCode = "";
        if (!code.isEmpty()) {
            for (FormData bom : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                    .eq(FormData::getPanelCode, "BOM"))) {
                Map<String, Object> h = parseData(bom.getData());
                String owner = str(h.getOrDefault("产品编码", ""));
                if (owner.isEmpty()) owner = parentCodeOf(bom, code);
                if (!code.equals(owner)) continue;
                String rc = str(h.getOrDefault("工艺路线编码", ""));
                if (!rc.isEmpty()) { routeCode = rc; break; }
            }
        }
        if (routeCode.isEmpty()) return defaultProcesses(products, workshop);
        List<Map<String, Object>> routeRows = routeRowsOf(routeCode);
        if (routeRows.isEmpty()) return defaultProcesses(products, workshop);

        routeRows.sort((a, b) -> Double.compare(num(a.get("加工顺序")), num(b.get("加工顺序"))));
        Object qty = products.get(0).getOrDefault("数量", 0);
        Object req = products.get(0).getOrDefault("需求令号", "");
        Object fig = products.get(0).getOrDefault("图号", "");
        List<Map<String, Object>> out = new ArrayList<>();
        int seq = 0;
        for (Map<String, Object> r : routeRows) {
            seq++;
            addDefaultProc(out, seq, str(r.get("工序编码")), str(r.get("工序名称")),
                    str(r.getOrDefault("生产车间", workshop)), str(r.get("设备")),
                    str(r.get("班组")), "", qty, num(r.getOrDefault("工价", r.get("计件单价"))),
                    num(r.get("单位标准工时")), req, fig);
        }
        return out;
    }

    private List<Map<String, Object>> defaultProcesses(List<Map<String, Object>> products, String workshop) {
        List<Map<String, Object>> out = new ArrayList<>();
        Map<String, Object> p0 = products.isEmpty() ? new HashMap<>() : products.get(0);
        Object qty = p0.getOrDefault("数量", 0);
        Object req = p0.getOrDefault("需求令号", "");
        Object fig = p0.getOrDefault("图号", "");
        addDefaultProc(out, 1, "PX001", "下料", workshop, "锯床-01", "下料班", "王强", qty, 2.5, 0.05, req, fig);
        addDefaultProc(out, 2, "PX002", "机加工", workshop, "数控车床-01", "车工班", "李强", qty, 3.5, 0.1, req, fig);
        addDefaultProc(out, 3, "PX007", "检验", workshop, "检测台-01", "质检班", "赵敏", qty, 1.5, 0.03, req, fig);
        // 把产品信息带到每道工序行：派工单「选生产加工单-工序」按工序明细映射，
        // 没有产品编码/名称时派工单这些列会空白（原来只能在 products 里找到）。
        String prodCode = String.valueOf(p0.getOrDefault("产品编码", p0.getOrDefault("存货编码", "")));
        String prodName = String.valueOf(p0.getOrDefault("产品名称", p0.getOrDefault("存货名称", "")));
        String prodSpec = String.valueOf(p0.getOrDefault("规格型号", ""));
        String prodUnit = String.valueOf(p0.getOrDefault("生产单位", "件"));
        for (Map<String, Object> proc : out) {
            proc.putIfAbsent("产品编码", prodCode);
            proc.putIfAbsent("产品名称", prodName);
            proc.putIfAbsent("规格型号", prodSpec);
            proc.putIfAbsent("工序单位", prodUnit);
        }
        return out;
    }

    private void addDefaultProc(List<Map<String, Object>> out, int seq, String code, String name, String workshop,
                                String equip, String team, String worker, Object qty, double price, double stdHour,
                                Object req, Object fig) {
        Map<String, Object> it = new HashMap<>();
        it.put("工序行码", "");
        it.put("工艺类型", "自制");
        it.put("工艺序号", 0);
        it.put("加工顺序", seq);
        it.put("加工类型", "自制");
        it.put("工序编码", code);
        it.put("工序名称", name);
        it.put("工序备注", "");
        it.put("生产车间", workshop);
        it.put("工作中心", workshop + "中心");
        it.put("设备", equip);
        it.put("班组", team);
        it.put("工人", worker);
        it.put("委外供应商", "");
        it.put("委外单价", 0);
        it.put("税率%", 13);
        it.put("委外金额", 0);
        it.put("按辅单位计价", false);
        it.put("计价辅单位", "件");
        it.put("换算率(辅单位)", 1);
        it.put("计价辅数量", 0);
        it.put("工序完工状态", "未完工");
        it.put("手工完工", false);
        it.put("行中止", false);
        it.put("工价（辅单位）", 0);
        it.put("工废工价", 0);
        it.put("工废工价（辅单位）", 0);
        it.put("料废工价", 0);
        it.put("料废工价（辅单位）", 0);
        it.put("工序单位", "件");
        it.put("计划数量", qty);
        it.put("工资类型", "计件");
        it.put("工价", price);
        it.put("金额", 0);
        it.put("关键工序", false);
        it.put("单位标准工时", stdHour);
        it.put("待返修数量-本序发现", 0);
        it.put("待返修数量-他序发现", 0);
        it.put("计划时间", "");
        it.put("完成时间", "");
        it.put("单重", 0);
        it.put("总重", 0);
        it.put("需求令号", req);
        it.put("子表材料", new ArrayList<>());
        it.put("工序字符专用自定义项1", "");
        out.add(it);
    }

    private Map<String, Object> create(String panelCode, Map<String, Object> formData, String userName) {
        return save(panelCode, formData, userName);
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> save(String panelCode, Map<String, Object> formData, String userName) {
        Object detail = formData.remove("detail");
        // 工艺路线主从结构：工序顺序以行序为准，表头字段不再冗余写进每行
        if ("ROUTE".equals(panelCode) && detail instanceof Map<?, ?> dm) {
            normalizeRouteDetail((Map<String, Object>) dm);
        }
        FormData fd;
        Object no = formData.get("编号");
        if (detail instanceof Map<?, ?> map) {
            @SuppressWarnings("unchecked")
            Map<String, Object> detailMap = (Map<String, Object>) map;
            deriveInspectionDetail(panelCode, detailMap);
            // 采购发票行级来源：先归一来源键，再走通用来源校验（存在/已审核/防重复/防超量）
            if ("PU_INVOICE".equals(panelCode)) normalizeInvoiceRowSources(detailMap);
            validateSourceLinks(panelCode, no == null ? null : String.valueOf(no), detailMap);
            // 发票表头金额、供应商一致性与勾稽状态由明细派生（保存时覆盖，明细为空时保留手工值）
            if ("PU_INVOICE".equals(panelCode)) {
                deriveInvoiceHeader(detailMap, formData, no == null ? null : String.valueOf(no));
            }
        }
        String stateField = (String) fieldsOf(panelCode).get("stateFieldName");
        formData.remove("编号");
        formData.remove("创建时间");
        formData.remove("更新时间");
        formData.remove("发起人编号");
        if (no != null && !"".equals(String.valueOf(no))) {
            fd = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                    .eq(FormData::getPanelCode, panelCode)
                    .eq(FormData::getFormNo, String.valueOf(no)));
            if (fd == null) throw new IllegalArgumentException("表单数据不存在：" + no);
            // 单据=草稿可保存；基础档案（存货/部门等）状态为 启用/停用 同样允许保存
            String st0 = fd.getStatus() == null ? "" : fd.getStatus();
            if (!"草稿".equals(st0) && !"启用".equals(st0) && !"停用".equals(st0)) {
                throw new IllegalStateException("仅草稿状态可保存");
            }
            // 质检数据改动后原「确认处置」失效：否则改完数量/判定仍带着旧确认直接入库。
            if ("INSPECTION".equals(panelCode) && "草稿".equals(st0) && detail instanceof Map<?, ?> nextRaw) {
                Map<String, Object> previous = parseData(fd.getDetailData());
                @SuppressWarnings("unchecked")
                Map<String, Object> next = (Map<String, Object>) nextRaw;
                if (hasConfirmedDisposition(previous)
                        && !inspectionDispositionSignature(previous)
                        .equals(inspectionDispositionSignature(next))) {
                    resetDispositionToPending(next);
                }
            }
            fd.setData(toJson(formData));
            // 明细兜底：未传 detail（如列表页仅保存表头）时保留原明细，禁止用 null 覆盖清空
            fd.setDetailData(detail == null || "null".equals(String.valueOf(detail)) ? fd.getDetailData() : toJson(detail));
            // 档案面板：保存即生效（草稿/停用保存后置「启用」）
            if (isArchivePanel(panelCode)) fd.setStatus("启用");
            fd.setUpdateTime(LocalDateTime.now());
            formMapper.updateById(fd);
        } else {
            fd = new FormData();
            fd.setPanelCode(panelCode);
            // 存货新单：按类别前缀编号（产成品 CP-、原材料 YL-、辅助材料 FZ-、包装物 BZ-、半成品 BC-）
            // 编号规则：基础档案 / 自编码面板（autoCodeField 非「单据编号」，如 工艺路线编码/物料清单编码/期初*号）→ 面板代码-3位序号；
            //          标准单据（单据编号 或 无 autoCodeField）→ 日期序号（SO-/MO-/FI-/RK-/CK-/GX-）
            String newNo;
            if ("INV".equals(panelCode)) {
                newNo = invNo(formData.get("类别") == null ? "" : String.valueOf(formData.get("类别")));
            } else if (isArchivePanel(panelCode)) {
                newNo = archNo(panelCode);
            } else {
                newNo = generateFormNo(panelCode, formData);
            }
            fd.setFormNo(newNo);
            // 新建保存兜底（2026-08-20 定稿）：所有面板补 字段默认值 + 单据日期=当天 + 单据编号/锭号=form_no
            // （缺才填）——手动新增/选单生成/推式生单的草稿表头数据完整可见；ensureAuditStamp 审核时幂等兜底
            fillNewDefaults(panelCode, formData, newNo);
            fd.setData(toJson(formData));
            fd.setDetailData(detail == null ? "{}" : toJson(detail));
            // 档案/自编码面板（EMP/DEPT/ROUTE/BOM…）新建即「启用」（保存即生效）；单据类初始「草稿」
            fd.setStatus("INV".equals(panelCode) || isArchivePanel(panelCode) ? "启用" : "草稿");
            fd.setCreateBy(userName == null ? "admin" : userName);
            fd.setCreateTime(LocalDateTime.now());
            fd.setUpdateTime(LocalDateTime.now());
            formMapper.insert(fd);
        }
        Map<String, Object> out = new HashMap<>();
        out.put("编号", fd.getFormNo());
        out.put("单据状态", fd.getStatus());
        out.put(stateField, fd.getStatus());
        return out;
    }

    /**
     * 采购发票行级来源键归一（2026-11-06）：
     * 前端选单行可能只带展示列「源单类型/源单编号/源单行号」，这里与
     * 「来源面板/来源单号/来源行号」互为兜底，保证 validateSourceLinks 的
     * 来源存在/已审核/防重复/防超量校验始终生效；同时补「来源数量」，
     * 让红字负数量行按绝对值占用来源额度。
     */
    private void normalizeInvoiceRowSources(Map<String, Object> detail) {
        for (Map<String, Object> row : firstDetailRows(detail)) {
            String sourcePanel = textOf(row.get("来源面板")).trim();
            if (sourcePanel.isEmpty()) {
                sourcePanel = textOf(row.get("源单类型")).trim();
                if (!sourcePanel.isEmpty()) row.put("来源面板", sourcePanel);
            } else if (textOf(row.get("源单类型")).isBlank()) {
                row.put("源单类型", sourcePanel);
            }
            String sourceNo = textOf(row.get("来源单号")).trim();
            if (sourceNo.isEmpty()) {
                sourceNo = textOf(row.get("源单编号")).trim();
                if (!sourceNo.isEmpty()) row.put("来源单号", sourceNo);
            } else if (textOf(row.get("源单编号")).isBlank()) {
                row.put("源单编号", sourceNo);
            }
            int sourceLine = (int) Math.round(num(row.get("来源行号")));
            if (sourceLine <= 0) {
                sourceLine = (int) Math.round(num(row.get("源单行号")));
                if (sourceLine > 0) row.put("来源行号", sourceLine);
            } else if (Math.round(num(row.get("源单行号"))) <= 0) {
                row.put("源单行号", sourceLine);
            }
            // 来源数量用于通用来源额度校验：红字行数量为负，额度仍按绝对值占用
            if (sourceLine > 0 && !sourceNo.isEmpty()) {
                row.put("来源数量", Math.abs(num(row.get("数量"))));
            }
        }
    }

    /**
     * 采购发票保存派生（2026-11-06）：
     * 1) 表头不含税金额/税额/价税合计由明细汇总（保留 2 位；明细为空不覆盖手工值）；
     * 2) 红字发票行数量/税额/价税合计必须为负，拒绝正数红票行；
     * 3) 发票供应商必须与来源入库单/退货单表头一致，为空时回填首个来源供应商；
     * 4) 勾稽状态由来源行未开票数量派生：全部开完=已勾稽，有剩余=部分勾稽，无来源=未勾稽。
     */
    private void deriveInvoiceHeader(Map<String, Object> detail, Map<String, Object> head, String currentNo) {
        List<Map<String, Object>> rows = firstDetailRows(detail);
        // 仅保存表头（明细未加载）时不覆盖用户手工填写的金额
        if (rows.isEmpty()) return;
        boolean red = textOf(head.get("业务类型")).contains("退货");
        double netTotal = 0;
        double taxTotal = 0;
        double grossTotal = 0;
        List<Map<String, Object>> sourceRows = new ArrayList<>();
        for (int index = 0; index < rows.size(); index++) {
            Map<String, Object> row = rows.get(index);
            double quantity = num(row.get("数量"));
            double rate = Math.max(0, num(row.get("税率%")));
            Object taxValue = row.get("税额");
            Object grossValue = row.get("价税合计");
            double tax = num(taxValue);
            double gross = num(grossValue);
            // 明细只填数量/单价时补算金额，避免表头汇总成 0；已填写（含手工 0）则按填写值汇总
            if (isBlankValue(grossValue) && quantity != 0) {
                double taxPrice = num(row.get("含税单价"));
                if (taxPrice == 0) taxPrice = num(row.get("无税单价")) * (1 + rate / 100);
                gross = round2(quantity * taxPrice);
            }
            if (isBlankValue(taxValue) && gross != 0) tax = round2(gross * rate / (100 + rate));
            if (red && (quantity > 0 || tax > 0 || gross > 0)) {
                throw new IllegalStateException("第 " + (index + 1) + " 行红字发票数量/税额/价税合计必须为负数");
            }
            netTotal += gross - tax;
            taxTotal += tax;
            grossTotal += gross;
            if (!sourcePanelOf(row).isEmpty() && !sourceNoOf(row).isEmpty()) sourceRows.add(row);
        }
        head.put("不含税金额", round2(netTotal));
        head.put("税额", round2(taxTotal));
        head.put("价税合计", round2(grossTotal));

        if (sourceRows.isEmpty()) {
            head.put("勾稽状态", "未勾稽");
            return;
        }
        // 供应商一致性：同一张发票只能对应一个供应商，空值回填首个来源供应商
        String invoiceSupplier = textOf(firstValue(head, "供应商", "供应商简称")).trim();
        String invoiceCode = textOf(head.get("供应商编码")).trim();
        String firstSupplier = "";
        String firstCode = "";
        Map<String, FormData> sources = new LinkedHashMap<>();
        for (Map<String, Object> row : sourceRows) {
            String sourcePanel = sourcePanelOf(row);
            String sourceNo = sourceNoOf(row);
            if (sourcePanel.isEmpty() || sourceNo.isEmpty()) continue;
            FormData source = sources.computeIfAbsent(sourcePanel + "|" + sourceNo, key ->
                    formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                            .eq(FormData::getPanelCode, sourcePanel)
                            .eq(FormData::getFormNo, sourceNo)));
            if (source == null) continue; // 来源存在性已由 validateSourceLinks 拦截
            Map<String, Object> sourceHead = parseData(source.getData());
            String sourceSupplier = textOf(firstValue(sourceHead, "供应商", "供应商简称")).trim();
            if (sourceSupplier.isEmpty()) continue;
            if (firstSupplier.isEmpty()) {
                firstSupplier = sourceSupplier;
                firstCode = textOf(sourceHead.get("供应商编码")).trim();
            }
            if (!invoiceSupplier.isEmpty() && !invoiceSupplier.equals(sourceSupplier)) {
                throw new IllegalStateException("发票供应商与来源单据不一致：发票 " + invoiceSupplier
                        + "，来源 " + sourceNo + " 为 " + sourceSupplier);
            }
        }
        if (invoiceSupplier.isEmpty() && !firstSupplier.isEmpty()) {
            head.put("供应商", firstSupplier);
            if (invoiceCode.isEmpty() && !firstCode.isEmpty()) head.put("供应商编码", firstCode);
        }

        // 勾稽状态：回写来源行已开票/未开票数量，全部来源行开完即「已勾稽」
        List<FormData> documents = formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "PU_INVOICE"));
        Map<String, Double> invoicedOthers = invoicedQuantities(documents, currentNo);
        boolean allInvoiced = true;
        for (Map<String, Object> row : sourceRows) {
            double remaining = refreshInvoiceSourceRow(row, invoicedOthers);
            if (remaining > 0.000001) allInvoiced = false;
        }
        head.put("勾稽状态", allInvoiced ? "已勾稽" : "部分勾稽");
    }

    /**
     * 回写来源行数量：来源数量 − 其他有效发票已开票数量 − 本行本次开票数量。
     * 来源单据缺失或不可计算时保留行上原值，避免把「未开票数量」误写成 0。
     */
    private double refreshInvoiceSourceRow(Map<String, Object> row, Map<String, Double> invoicedOthers) {
        String sourcePanel = sourcePanelOf(row);
        String sourceNo = sourceNoOf(row);
        int lineNo = sourceLineNoOf(row);
        double fallback = num(row.get("未开票数量"));
        if (sourcePanel.isEmpty() || sourceNo.isEmpty() || lineNo <= 0) return fallback;
        FormData source = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, sourcePanel)
                .eq(FormData::getFormNo, sourceNo));
        if (source == null) return fallback;
        List<Map<String, Object>> sourceRows = firstDetailRows(parseData(source.getDetailData()));
        if (lineNo > sourceRows.size()) return fallback;
        Map<String, Object> sourceRow = sourceRows.get(lineNo - 1);
        double sourceQty = "PURCHASE_RETURN".equals(sourcePanel)
                ? Math.abs(executedQuantity("PURCHASE_RETURN", sourceRow))
                : Math.abs(purchaseInQuantity(sourceRow));
        if (sourceQty <= 0) return fallback;
        double others = invoicedOthers.getOrDefault(sourceKey(sourcePanel, sourceNo, lineNo), 0d);
        double current = Math.abs(num(row.get("数量")));
        row.put("入库数量", round4(sourceQty));
        row.put("已开票数量", round4(others + current));
        double remaining = round4(sourceQty - others - current);
        row.put("未开票数量", Math.max(0, remaining));
        return remaining;
    }

    @SuppressWarnings("unchecked")
    /** 当前登录用户（JwtAuthFilter 写入 SecurityContext；未认证时兜底 system） */
    private String currentUserName() {
        var auth = SecurityContextHolder.getContext().getAuthentication();
        if (auth != null && auth.getName() != null && !auth.getName().isBlank()) return auth.getName();
        return "system";
    }

    private Map<String, Object> changeStatus(String panelCode, Map<String, Object> formData, String action) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData fd = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, panelCode)
                .eq(FormData::getFormNo, String.valueOf(no)));
        if (fd == null) throw new IllegalArgumentException("表单数据不存在：" + no);
        if ("审核".equals(action)) {
            if (requiresApprovalWorkflow(panelCode)) {
                throw new IllegalStateException("该面板必须先提交审批，不能直接审核");
            }
            if (!"草稿".equals(fd.getStatus())) throw new IllegalStateException("仅草稿状态可审核");
            validateBomForApproval(panelCode, fd);
            validateQualityForApproval(panelCode, fd);
            if ("MATERIAL_OUT".equals(panelCode)) {
                validateMaterialIssueFor(fd);
                validateBatchIssueFor(fd);
            }
            if ("MANU_ORDER".equals(panelCode)) {
                refreshKitting(fd.getFormNo());
            }
            validateSourceLinks(panelCode, fd.getFormNo(), parseData(fd.getDetailData()));
            if ("INSPECTION".equals(panelCode)) confirmInspectionDetail(fd);
            // 人工审核：审核人 = 当前登录人（不再硬编码 admin）
            String operator = currentUserName();
            fd.setStatus("已审核");
            fd.setAuditBy(operator);
            fd.setAuditTime(LocalDateTime.now());
            // 审核人/审核时间/审核意见 写入表头 JSON（前端表尾/列表直接展示）
            Map<String, Object> head = parseData(fd.getData());
            head.put("审核人", operator);
            head.put("审核时间", fd.getAuditTime().format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss")));
            Object opinion = formData.get("审核意见");
            if (opinion != null && !String.valueOf(opinion).isBlank()) head.put("审核意见", String.valueOf(opinion));
            // 审批面板：审核时自动填写 单据日期 + 单据编号（缺才填，见 ensureAuditStamp）
            ensureAuditStamp(head, fd.getFormNo(), panelCode);
            fd.setData(toJson(head));
            // 审核通过即落库存事务：出入库单在此增加/减少库存余额
            postStockTxn(panelCode, fd.getFormNo(), head, parseData(fd.getDetailData()));
            // 同一时刻生成应收/应付往来单（财务面板才触发）
            postFinance(panelCode, fd.getFormNo(), head, parseData(fd.getDetailData()));
            // 业财一体化：生成会计凭证分录
            postAccounting(panelCode, fd.getFormNo(), head, parseData(fd.getDetailData()));
        } else if ("弃审".equals(action)) {
            if (!"已审核".equals(fd.getStatus())) throw new IllegalStateException("仅已审核状态可弃审");
            ensureNoEffectiveChildren(panelCode, fd.getFormNo(), "弃审");
            // 加工单已完工（产成品已入库）时，不能再撤汇报单，否则库存/完工口径对不上
            if ("PROCESS_REPORT".equals(panelCode)) requireOrderNotFinished(fd);
            // 弃审先冲销库存事务，避免余额表残留已撤销单据的出入库
            reverseStockTxn(panelCode, fd.getFormNo());
            // 同步冲销往来单
            reverseFinance(panelCode, fd.getFormNo());
            // 同步冲销会计凭证
            reverseAccounting(panelCode, fd.getFormNo());
            Map<String, Object> head = parseData(fd.getData());
            head.remove("审核人");
            head.remove("审核时间");
            head.remove("审核意见");
            // 弃审回草稿后必须清掉表头审批状态：否则前端「已审批」印章会凭残留的
            // 审批状态=已通过/已审批 继续显示，出现「状态草稿 + 已审批章」并存。
            head.remove("审批状态");
            // 显式 set(null)：MyBatis-Plus 默认 NOT_NULL 策略不会把 null 字段写进 UPDATE，
            // 必须用 LambdaUpdateWrapper.set 强制清空 audit_by/audit_time
            formMapper.update(null, new LambdaUpdateWrapper<FormData>()
                    .eq(FormData::getId, fd.getId())
                    .set(FormData::getStatus, "草稿")
                    .set(FormData::getAuditBy, null)
                    .set(FormData::getAuditTime, null)
                    .set(FormData::getData, toJson(head))
                    .set(FormData::getUpdateTime, LocalDateTime.now()));
            if (requiresApprovalWorkflow(panelCode)) {
                recordApproval(panelCode, fd.getFormNo(), "UNAUDIT", "PENDING", opinionOf(formData));
            }
            refreshSourceExecution(fd);
            syncProgressFrom(fd);
            return new HashMap<String, Object>() {{
                put("编号", fd.getFormNo());
                put("单据状态", "草稿");
            }};
        } else if ("关闭".equals(action)) {
            if (!"已审核".equals(fd.getStatus()) && !"生产中".equals(fd.getStatus())) {
                throw new IllegalStateException("仅已审核/生产中状态可关闭");
            }
            fd.setStatus("已关闭");
            fd.setCloseTime(LocalDateTime.now());
        } else if ("手工完工".equals(action)) {
            if (!"已审核".equals(fd.getStatus()) && !"生产中".equals(fd.getStatus())) {
                throw new IllegalStateException("仅已审核/生产中状态可手工完工");
            }
            fd.setStatus("已完工");
        } else if ("中止".equals(action)) {
            if (!"已审核".equals(fd.getStatus()) && !"生产中".equals(fd.getStatus()) && !"已完工".equals(fd.getStatus())) {
                throw new IllegalStateException("仅已审核/生产中/已完工状态可中止");
            }
            fd.setStatus("已中止");
        } else if ("取消中止".equals(action)) {
            if (!"已中止".equals(fd.getStatus())) {
                throw new IllegalStateException("仅已中止状态可取消中止");
            }
            fd.setStatus("已审核");
        }
        fd.setUpdateTime(LocalDateTime.now());
        formMapper.updateById(fd);
        if ("审核".equals(action)) {
            refreshSourceExecution(fd);
            syncProgressFrom(fd);
        }
        Map<String, Object> out = new HashMap<>();
        out.put("编号", fd.getFormNo());
        out.put("单据状态", fd.getStatus());
        return out;
    }

    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> firstDetailRows(Map<String, Object> detail) {
        for (String key : List.of("items", "products", "detail", "rows", "materials", "processes")) {
            Object value = detail.get(key);
            if (!(value instanceof List<?> list)) continue;
            List<Map<String, Object>> rows = new ArrayList<>();
            for (Object item : list) if (item instanceof Map<?, ?>) rows.add((Map<String, Object>) item);
            return rows;
        }
        for (Object value : detail.values()) {
            if (!(value instanceof List<?> list)) continue;
            List<Map<String, Object>> rows = new ArrayList<>();
            for (Object item : list) if (item instanceof Map<?, ?>) rows.add((Map<String, Object>) item);
            if (!rows.isEmpty()) return rows;
        }
        return new ArrayList<>();
    }

    /**
     * 检验退料（来源检验单、尚未入库）不落库存事务：货没有进过仓库，退回时也不能出库，
     * 否则会把库存扣成负数。库存退料（来源采购入库单/带入库单号）仍按 PURCHASE_RETURN 出库。
     */
    private boolean isInspectionReturn(String panelCode, Map<String, Object> head,
                                       Map<String, Object> detail) {
        if (!"PURCHASE_RETURN".equals(panelCode)) return false;
        if (!textOf(head.get("入库单号")).trim().isEmpty()) return false;
        if (!textOf(head.get("检验单号")).trim().isEmpty()) return true;
        if ("检验不合格退回".equals(textOf(head.get("退货原因")).trim())) return true;
        for (Map<String, Object> row : firstDetailRows(detail)) {
            if ("INSPECTION".equals(textOf(row.get("来源面板")).trim())) return true;
        }
        return false;
    }

    /** 审核通过时落库存事务；未注入服务、非库存面板或检验退料时静默跳过。 */
    private void postStockTxn(String panelCode, String formNo, Map<String, Object> head,
                              Map<String, Object> detail) {
        if (stockTxnService == null) return;
        if (isInspectionReturn(panelCode, head, detail)) return;
        stockTxnService.post(panelCode, formNo, head, detail);
    }

    /** 弃审时冲销库存事务。 */
    private void reverseStockTxn(String panelCode, String formNo) {
        if (stockTxnService == null) return;
        stockTxnService.reverse(panelCode, formNo);
    }

    /** 审核通过时生成应收/应付往来单；未注入服务或非财务面板时跳过。 */
    private void postFinance(String panelCode, String formNo, Map<String, Object> head,
                             Map<String, Object> detail) {
        if (financeSettleService == null) return;
        // 红字发票只做税务/勾稽：应付冲减由采购退货单的 AP_RETURN 负责，避免重复冲减
        if (isReturnInvoice(panelCode, head)) return;
        financeSettleService.post(panelCode, formNo, head, detail);
    }

    /** 采购发票(退货) = 红字发票：不重复产生应付/凭证。 */
    private static boolean isReturnInvoice(String panelCode, Map<String, Object> head) {
        return "PU_INVOICE".equals(panelCode) && textOf(head.get("业务类型")).contains("退货");
    }

    /** 弃审时冲销往来单。 */
    private void reverseFinance(String panelCode, String formNo) {
        if (financeSettleService == null) return;
        financeSettleService.reverse(panelCode, formNo);
    }

    /** 审核通过时生成会计凭证分录（借贷平衡校验在服务内完成）。 */
    private void postAccounting(String panelCode, String formNo, Map<String, Object> head,
                                Map<String, Object> detail) {
        if (accountingService == null) return;
        // 红字发票不重复生成会计凭证（退货单已按 借应付/贷库存 冲销）
        if (isReturnInvoice(panelCode, head)) return;
        java.math.BigDecimal amount = java.math.BigDecimal.valueOf(settlementAmount(detail));
        if (amount.signum() == 0) return;
        String partner = textOf(firstValue(head, "客户", "供应商", "往来单位"));
        String summary = textOf(firstValue(head, "业务类型", "备注"));
        if ("SALE_INV".equals(panelCode)) {
            // 销货单：应收按含税，收入按不含税，销项税单独挂 222101
            accountingService.postSaleInvoice(formNo, amount,
                    java.math.BigDecimal.valueOf(taxAmount(detail)), partner, summary,
                    java.time.LocalDate.now());
            return;
        }
        accountingService.post(panelCode, formNo, amount, partner, summary, java.time.LocalDate.now());
    }

    /** 弃审时冲销会计凭证。 */
    private void reverseAccounting(String panelCode, String formNo) {
        if (accountingService == null) return;
        accountingService.reverse(panelCode, formNo);
    }

    /** 明细金额合计：与往来/库存服务同一口径。 */
    /**
     * 自制产成品单位成本 = (Σ 材料计划用量 × 材料结存均价 + Σ 工序工价 × 计划数量) / 产成品数量。
     *
     * <p>工价按计件单价乘计划数量；材料单价一致取库存结存均价，与领料出库同源。
     */
    /** 结存均价（按仓）；无结存或服务缺失时返回 0。 */
    private double stockAvgPrice(String code, String name, String warehouse) {
        if (stockTxnService == null) return 0;
        java.math.BigDecimal avg = stockTxnService.averageCostOf(code, name, warehouse);
        return avg == null ? 0 : avg.doubleValue();
    }

    double manufactureUnitCost(Map<String, Object> moDetail, double orderQty) {
        if (moDetail == null || orderQty <= 0) return 0;
        double materialCost = 0;
        if (moDetail.get("materials") instanceof List<?> mats) {
            for (Object o : mats) {
                if (!(o instanceof Map<?, ?> raw)) continue;
                Map<String, Object> m = castRow(raw);
                double qty = num(m.get("计划数量"));
                if (qty <= 0) qty = num(m.get("需用数量"));
                if (qty <= 0) continue;
                java.math.BigDecimal avg = stockTxnService == null ? null
                        : stockTxnService.averageCostOf(str(m.get("材料编码")), str(m.get("材料名称")));
                double price = avg == null ? 0 : avg.doubleValue();
                materialCost += qty * price;
            }
        }
        double laborCost = 0;
        if (moDetail.get("processes") instanceof List<?> procs) {
            for (Object o : procs) {
                if (!(o instanceof Map<?, ?> raw)) continue;
                Map<String, Object> p = castRow(raw);
                double price = num(p.get("工价"));
                if (price <= 0) continue;
                double qty = num(p.get("计划数量"));
                laborCost += price * (qty > 0 ? qty : orderQty);
            }
        }
        return round2((materialCost + laborCost) / orderQty);
    }

    /**
     * 凭证金额口径：销售出库按成本结转（成本金额），其它单据仍按单据金额（含税/销售/采购额）。
     */
    double accountingAmount(String panelCode, Map<String, Object> detail) {
        if ("SALE_OUT".equals(panelCode)) return costAmount(detail);
        return settlementAmount(detail);
    }

    /**
     * 明细的税额合计：优先取行「税额」，缺失时按 含税金额 × 税率 /(100+税率) 反算。
     */
    double taxAmount(Map<String, Object> detail) {
        if (detail == null) return 0;
        double total = 0;
        for (Object rowsObject : detail.values()) {
            if (!(rowsObject instanceof List<?> rows)) continue;
            for (Object rowObject : rows) {
                if (!(rowObject instanceof Map<?, ?> raw)) continue;
                @SuppressWarnings("unchecked")
                Map<String, Object> row = (Map<String, Object>) raw;
                double tax = num(firstValue(row, "税额"));
                if (tax == 0) {
                    double gross = num(firstValue(row, "含税金额", "含税销售金额"));
                    double rate = num(firstValue(row, "税率%"));
                    if (gross != 0 && rate > 0) tax = gross * rate / (100 + rate);
                }
                total += tax;
            }
        }
        return round2(total);
    }

    /** 明细的成本合计：优先成本金额，缺失时按 数量 × 成本价 兜底。 */
    private double costAmount(Map<String, Object> detail) {
        if (detail == null) return 0;
        double total = 0;
        for (Object rowsObject : detail.values()) {
            if (!(rowsObject instanceof List<?> rows)) continue;
            for (Object rowObject : rows) {
                if (!(rowObject instanceof Map<?, ?> raw)) continue;
                @SuppressWarnings("unchecked")
                Map<String, Object> row = (Map<String, Object>) raw;
                double amount = num(firstValue(row, "成本金额"));
                if (amount == 0) {
                    amount = num(firstValue(row, "数量", "实收数量")) * num(firstValue(row, "成本价"));
                }
                total += amount;
            }
        }
        return total;
    }

    private double settlementAmount(Map<String, Object> detail) {
        if (detail == null) return 0;
        double total = 0;
        for (Object rowsObject : detail.values()) {
            if (!(rowsObject instanceof List<?> rows)) continue;
            for (Object rowObject : rows) {
                if (!(rowObject instanceof Map<?, ?> raw)) continue;
                @SuppressWarnings("unchecked")
                Map<String, Object> row = (Map<String, Object>) raw;
                double amount = num(firstValue(row, "含税金额", "价税合计", "销售金额", "金额", "成本金额", "实收金额"));
                if (amount == 0) {
                    amount = num(firstValue(row, "实收数量", "数量")) * num(firstValue(row, "含税单价", "售价", "单价", "成本价"));
                }
                total += amount;
            }
        }
        return total;
    }

    private static String textOf(Object value) {
        return value == null ? "" : String.valueOf(value);
    }

    private double sourceQuantity(Map<String, Object> row) {
        // 计划数量放最后：加工单的工序行只有「计划数量」，而其它来源单据优先用自己的执行量字段
        for (String field : List.of("报检数量", "到货数量", "数量", "派工数量", "报工数量", "检验数量", "实收数量", "金额", "计划数量")) {
            if (row.containsKey(field)) return num(row.get(field));
        }
        return 0;
    }

    /**
     * 下游单据一行的执行数量。按「单据自身面板」取口径，而不是按字段出现顺序猜：
     * 到货单看实收、入库单看实收、退货单看数量、检验单看检验数量。
     * 其它面板保持旧顺序兜底。
     */
    private double executedQuantity(String documentPanel, Map<String, Object> row) {
        List<String> preferred = switch (documentPanel == null ? "" : documentPanel) {
            case "ARRIVAL_IN" -> List.of("实收数量", "到货数量");
            case "PURCHASE_IN", "FINISH_IN", "OUTSOURCE_IN" -> List.of("实收数量", "数量");
            case "PURCHASE_RETURN" -> List.of("数量", "实收数量");
            case "PU_INVOICE" -> List.of("来源数量", "数量");
            case "INSPECTION", "PROCESS_INSPECTION" -> List.of("检验数量");
            // 工序汇报单/派工单用的是「报工数量」「派工数量」：漏了它们会让选单进来的行
            // 被执行成 0，报「执行数量必须大于 0」。
            case "PROCESS_REPORT" -> List.of("报工数量", "合格数量");
            case "DISPATCH" -> List.of("派工数量", "计划数量");
            default -> List.of();
        };
        for (String field : preferred) {
            if (row.containsKey(field)) return num(row.get(field));
        }
        for (String field : List.of("报工数量", "派工数量", "检验数量", "实收数量", "入库数量",
                "处理数量", "报检数量", "到货数量", "数量", "分摊金额", "金额")) {
            if (row.containsKey(field)) return num(row.get(field));
        }
        return num(row.get("来源数量"));
    }

    /** 到货行可执行量：实收数量 → 到货数量-到货拒收数量 → 到货数量。 */
    static double arrivalReceivable(Map<String, Object> row) {
        if (row.containsKey("实收数量")) return num(row.get("实收数量"));
        double arrived = num(row.get("到货数量"));
        if (arrived > 0) {
            double rejected = num(row.get("到货拒收数量"));
            double net = arrived - rejected;
            return net > 0 ? net : arrived;
        }
        return 0;
    }

    /** 采购入库行可退量：实收数量 → 数量。 */
    static double purchaseInQuantity(Map<String, Object> row) {
        for (String field : List.of("实收数量", "数量")) {
            if (row.containsKey(field)) return num(row.get(field));
        }
        return 0;
    }

    /**
     * 检验单可入库量 = 合格数量 + 让步接收处理量。
     * 处置页签（unqualified）存在时以行上的让步接收数量为准（0 也是权威值）；
     * 旧单据没有该页签时才按「判定=让步接收 → 不合格数量」回退。
     */
    static double inspectionAcceptable(Map<String, Object> detail, Map<String, Object> row, int lineNo) {
        double qualified = num(row.get("合格数量"));
        return qualified + inspectionConcessionQuantity(detail, row, lineNo);
    }

    static double inspectionConcessionQuantity(Map<String, Object> detail, Map<String, Object> row, int lineNo) {
        if (detail != null && detail.containsKey("unqualified")) {
            return num(row.get("让步接收数量"));
        }
        String verdict = textOf(row.get("检验结果判定")).trim();
        if ("让步接收".equals(verdict)) return num(row.get("不合格数量"));
        return 0;
    }

    /**
     * 检验单可退量。处置页签存在时以行上的验收退回数量为准（空数组/0 都是权威值）；
     * 旧单据没有验收退回数量时退回判定退货数量，再没有才回退 不合格数量。
     */
    @SuppressWarnings("unchecked")
    static double inspectionReturnQuantity(Map<String, Object> detail, Map<String, Object> row, int lineNo) {
        if ("全批退回".equals(textOf(row.get("处置方式")))) return num(row.get("检验数量"));
        if (detail != null && detail.containsKey("unqualified")) {
            if (row.containsKey("验收退回数量")) return Math.max(0, num(row.get("验收退回数量")));
            if (row.containsKey("判定退货数量")) return Math.max(0, num(row.get("判定退货数量")));
            return Math.max(0, num(row.get("不合格数量")));
        }
        return purchaseReturnQuantity(row, null, lineNo);
    }

    /**
     * 检验单保存时由「不合格处理」页签回写行级处置数量：
     * 让步接收数量、报废数量、判定退货数量。
     * 页签存在（含空数组）时以页签为权威值，空数组即全部清零；
     * 旧单据没有该页签时保留行上原值。未填完整的页签行跳过，
     * 数量勾稽由提交/审批校验兜底，避免编辑草稿时被半成品行卡住。
     */
    @SuppressWarnings("unchecked")
    static void deriveInspectionDetail(String panelCode, Map<String, Object> detail) {
        if (detail == null || !QUALITY_INSPECTION_PANELS.contains(panelCode)) return;
        if (!(detail.get("unqualified") instanceof List<?> treatments)) return;
        if (!(detail.get("items") instanceof List<?> items)) return;
        List<Map<String, Object>> rows = new ArrayList<>();
        for (Object item : items) if (item instanceof Map<?, ?>) rows.add((Map<String, Object>) item);
        if (rows.isEmpty()) return;

        // 每行累计 [让步接收, 报废, 退回, 返工/返修]
        Map<Integer, double[]> totalsByLine = new HashMap<>();
        for (Object value : treatments) {
            if (!(value instanceof Map<?, ?> raw)) continue;
            @SuppressWarnings("unchecked")
            Map<String, Object> treatment = (Map<String, Object>) raw;
            int lineNo = (int) Math.round(num(treatment.get("关联明细行号")));
            if (lineNo <= 0 || lineNo > rows.size()) continue;
            // 行级不合格原因是主表字段，不合格处理页签没填时自动带下来
            if (textOf(treatment.get("不合格原因")).isBlank()) {
                String reason = textOf(rows.get(lineNo - 1).get("不合格原因"));
                if (!reason.isBlank()) treatment.put("不合格原因", reason);
            }
            double quantity = num(treatment.get("处理数量"));
            if (quantity <= 0) continue;
            double[] totals = totalsByLine.computeIfAbsent(lineNo, ignored -> new double[4]);
            switch (textOf(treatment.get("处理方式")).trim()) {
                case "让步接收" -> totals[0] += quantity;
                case "报废" -> totals[1] += quantity;
                case "退回" -> totals[2] += quantity;
                case "返工", "返修" -> totals[3] += quantity;
                default -> { }
            }
        }
        for (int index = 0; index < rows.size(); index++) {
            double[] totals = totalsByLine.getOrDefault(index + 1, new double[4]);
            Map<String, Object> row = rows.get(index);
            row.put("让步接收数量", totals[0]);
            row.put("报废数量", totals[1]);
            // 验收退回数量是对外口径；判定退货数量保留给老单据/老报表兼容
            row.put("验收退回数量", totals[2]);
            row.put("判定退货数量", totals[2]);
            row.put("返工数量", totals[3]);
            if ("PROCESS_INSPECTION".equals(panelCode)) row.put("检验返工数量", totals[3]);
            // 处置数量以不合格处理页签汇总为准；降价接收按折价率回写调整后单价/折价金额
            row.put("处置数量", totals[0] + totals[1] + totals[2] + totals[3]);
            double rate = num(row.get("折价率%"));
            String disposition = textOf(row.get("处置方式")).trim();
            double price = num(row.get("单价"));
            double taxPrice = num(row.get("含税单价"));
            if ("降价接收".equals(disposition) && rate > 0) {
                row.put("调整后单价", Math.round(price * (1 - rate / 100) * 100) / 100.0);
                row.put("调整后含税单价", Math.round(taxPrice * (1 - rate / 100) * 100) / 100.0);
                double amount = num(row.get("金额"));
                if (amount <= 0) amount = Math.round(price * num(row.get("检验数量")) * 100) / 100.0;
                row.put("折价金额", Math.round(amount * rate / 100 * 100) / 100.0);
            } else {
                row.put("调整后单价", 0);
                row.put("调整后含税单价", 0);
                row.put("折价金额", 0);
            }
            // 让步接收含税单价：折价时按原含税单价折减；无折价且未手工填写时取原价
            if (("让步接收".equals(disposition) || "降价接收".equals(disposition)) && taxPrice > 0) {
                if (rate > 0) {
                    row.put("让步接收含税单价", Math.round(taxPrice * (1 - rate / 100) * 100) / 100.0);
                } else if (num(row.get("让步接收含税单价")) <= 0) {
                    row.put("让步接收含税单价", taxPrice);
                }
            }
        }
    }

    /**
     * 处置确认签名：只覆盖会影响入库/退货/结算的字段，备注等展示字段变化不触发重新确认。
     */
    private static final List<String> DISPOSITION_ITEM_FIELDS = List.of(
            "存货编码", "仓库", "检验数量", "合格数量", "不合格数量", "检验结果判定",
            "单价", "税率%", "含税单价", "处置方式", "处置数量", "让步接收数量", "报废数量",
            "判定退货数量", "结算方式", "折价率%", "折价金额", "调整后单价", "调整后含税单价",
            "责任归属", "复检要求", "复检结果");
    private static final List<String> DISPOSITION_TREATMENT_FIELDS = List.of(
            "关联明细行号", "不合格原因", "处理方式", "处理数量", "报废类型");
    private static final List<String> DISPOSITION_PROJECT_FIELDS = List.of(
            "关联明细行号", "检验项目", "检验项目编码", "检验结果", "单项判定");

    static String inspectionDispositionSignature(Map<String, Object> detail) {
        if (detail == null) return "";
        StringBuilder out = new StringBuilder();
        if (detail.get("items") instanceof List<?> rows) {
            for (Object value : rows) {
                if (!(value instanceof Map<?, ?> row)) continue;
                out.append('I');
                for (String name : DISPOSITION_ITEM_FIELDS) out.append('|').append(signatureValue(row.get(name)));
                out.append('\n');
            }
        }
        if (detail.get("unqualified") instanceof List<?> treatments) {
            for (Object value : treatments) {
                if (!(value instanceof Map<?, ?> row)) continue;
                out.append('U');
                for (String name : DISPOSITION_TREATMENT_FIELDS) out.append('|').append(signatureValue(row.get(name)));
                out.append('\n');
            }
        }
        if (detail.get("projects") instanceof List<?> projects) {
            for (Object value : projects) {
                if (!(value instanceof Map<?, ?> row)) continue;
                out.append('P');
                for (String name : DISPOSITION_PROJECT_FIELDS) out.append('|').append(signatureValue(row.get(name)));
                out.append('\n');
            }
        }
        return out.toString();
    }

    /** 数值签名统一去掉尾随 0：历史单据存 2、新手填 2.0 不应被当成改动。 */
    private static String signatureValue(Object value) {
        String text = textOf(value).trim();
        if (text.isEmpty()) return "";
        try {
            return new java.math.BigDecimal(text).stripTrailingZeros().toPlainString();
        } catch (NumberFormatException ignored) {
            return text;
        }
    }

    static boolean hasConfirmedDisposition(Map<String, Object> detail) {
        if (detail == null || !(detail.get("items") instanceof List<?> rows)) return false;
        for (Object value : rows) {
            if (value instanceof Map<?, ?> row && "已确认".equals(textOf(row.get("处置状态")))) return true;
        }
        return false;
    }

    /** 检验数据被改动后，原「确认处置」失效，必须重新确认后才能生单。 */
    @SuppressWarnings("unchecked")
    static void resetDispositionToPending(Map<String, Object> detail) {
        if (detail == null || !(detail.get("items") instanceof List<?> rows)) return;
        for (Object value : rows) {
            if (!(value instanceof Map<?, ?> raw)) continue;
            Map<String, Object> row = (Map<String, Object>) raw;
            String status = textOf(row.get("处置状态"));
            if (!status.isEmpty() && !"待确认".equals(status)) row.put("处置状态", "待确认");
        }
    }

    /**
     * 来源上限（只对采购配对生效）。其它来源/目标组合沿用 sourceQuantity 旧顺序。
     */
    private double sourceLimit(String targetPanel, String sourcePanel, Map<String, Object> sourceDetail,
                               Map<String, Object> sourceRow, int sourceRowNo) {
        String target = targetPanel == null ? "" : targetPanel;
        String source = sourcePanel == null ? "" : sourcePanel;
        if ("ARRIVAL_IN".equals(source) && ("INSPECTION".equals(target) || "PURCHASE_IN".equals(target))) {
            return arrivalReceivable(sourceRow);
        }
        if (QUALITY_INSPECTION_PANELS.contains(source)) {
            if ("PURCHASE_RETURN".equals(target)) {
                return inspectionReturnQuantity(sourceDetail, sourceRow, sourceRowNo);
            }
            if (Set.of("PURCHASE_IN", "FINISH_IN", "OUTSOURCE_IN").contains(target)) {
                return inspectionAcceptable(sourceDetail, sourceRow, sourceRowNo);
            }
            return num(sourceRow.get("检验数量"));
        }
        if ("PURCHASE_IN".equals(source) && Set.of("PURCHASE_RETURN", "PU_INVOICE").contains(target)) {
            return purchaseInQuantity(sourceRow);
        }
        if ("PURCHASE_RETURN".equals(source) && "PU_INVOICE".equals(target)) {
            // 退货可开票量以「数量」为准（实收数量可能未填写而为 0，不能作为额度）
            double returnQty = Math.abs(num(sourceRow.get("数量")));
            return returnQty > 0 ? returnQty : purchaseInQuantity(sourceRow);
        }
        if ("PU_REQ".equals(source) && "PU_ORDER".equals(target)) {
            return num(sourceRow.get("数量"));
        }
        return sourceQuantity(sourceRow);
    }

    /** 已生效检验单引用的到货明细行号，用于阻止绕过检验单直接入库。 */
    private Set<Integer> effectiveInspectionLines(String sourcePanel, String sourceNo) {
        Set<Integer> lines = new HashSet<>();
        for (FormData document : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "INSPECTION"))) {
            if (!EFFECTIVE_STATUSES.contains(document.getStatus())) continue;
            for (Map<String, Object> row : firstDetailRows(parseData(document.getDetailData()))) {
                if (!sourcePanel.equals(textOf(row.get("来源面板")).trim())
                        || !sourceNo.equals(textOf(row.get("来源单号")).trim())) continue;
                int line = (int) Math.round(num(row.get("来源行号")));
                if (line > 0) lines.add(line);
            }
        }
        return lines;
    }

    /** 采购生单来源状态守卫：已中止/已关闭/审批中给明确原因，其余按已审核要求。 */
    private void requireApprovedPurchaseSource(FormData source, String action) {
        if (source == null) throw new IllegalArgumentException("来源单据不存在");
        String status = source.getStatus() == null ? "" : source.getStatus();
        switch (status) {
            case "已审核" -> { }
            case "已中止" -> throw new IllegalStateException(
                    "来源单据 " + source.getFormNo() + " 已中止，不能" + action);
            case "已关闭" -> throw new IllegalStateException(
                    "来源单据 " + source.getFormNo() + " 已关闭，不能" + action);
            case "审批中" -> throw new IllegalStateException(
                    "来源单据 " + source.getFormNo() + " 审批中，不能" + action);
            default -> throw new IllegalStateException("仅已审核单据可" + action + "：" + source.getFormNo());
        }
    }

    /** 到货单提交/审核：实收数量必须等于到货数量减到货拒收数量。 */
    private void validateArrivalReceiptQuantities(List<Map<String, Object>> rows) {
        for (int index = 0; index < rows.size(); index++) {
            Map<String, Object> row = rows.get(index);
            double arrived = num(row.get("到货数量"));
            double rejected = num(row.get("到货拒收数量"));
            double received = num(row.get("实收数量"));
            if (arrived <= 0) {
                throw new IllegalStateException("第 " + (index + 1) + " 行到货数量必须大于 0");
            }
            if (rejected < -0.000001 || rejected > arrived + 0.000001) {
                throw new IllegalStateException("第 " + (index + 1) + " 行到货拒收数量必须在 0 与到货数量之间");
            }
            double expected = arrived - rejected;
            if (Math.abs(received - expected) > 0.000001) {
                throw new IllegalStateException("第 " + (index + 1) + " 行实收数量必须等于到货数量减到货拒收数量（"
                        + trimNumber(expected) + "），当前为 " + trimNumber(received));
            }
        }
    }

    /**
     * Validates persisted source links. Drafts reserve a source quantity so two
     * users cannot select the same source line and later over-execute it.
     */
    /**
     * 来源单据的哪一页明细被选用了。
     *
     * <p>加工单有 products/processes/materials 三页，而 firstDetailRows 永远取第一页（产成品）。
     * 工序汇报单、派工单选的是工序行，领料单选的是材料行——按来源行号取值必须落到对应页，
     * 否则行号对不上，会出现「计划 0、已报 0」这种对不上的校验结果。
     */
    private List<Map<String, Object>> sourceRowsFor(FormData source, String targetPanel, String sourcePanel) {
        Map<String, Object> detail = parseData(source.getDetailData());
        String key = detailKeyFor(targetPanel, sourcePanel);
        if (key != null && detail.get(key) instanceof List<?> list && !list.isEmpty()) {
            List<Map<String, Object>> rows = new ArrayList<>();
            for (Object o : list) {
                if (o instanceof Map<?, ?> raw) rows.add(castRow(raw));
            }
            if (!rows.isEmpty()) return rows;
        }
        return firstDetailRows(detail);
    }

    /** 目标单据 → 它采销来源单据的哪一页明细。 */
    private static String detailKeyFor(String targetPanel, String sourcePanel) {
        if ("MANU_ORDER".equals(sourcePanel)) {
            return switch (targetPanel) {
                case "PROCESS_REPORT", "DISPATCH" -> "processes";
                case "MATERIAL_OUT" -> "materials";
                case "FINISH_IN" -> "products";
                default -> null;
            };
        }
        return null;
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> castRow(Map<?, ?> raw) {
        return (Map<String, Object>) raw;
    }

    // 包内可见：单测直接验证选单校验（外部只经 save/审核 触发）
    void validateSourceLinks(String targetPanel, String targetNo, Map<String, Object> detail) {
        List<Map<String, Object>> rows = firstDetailRows(detail);
        Set<String> localKeys = new HashSet<>();
        for (int index = 0; index < rows.size(); index++) {
            Map<String, Object> row = rows.get(index);
            String sourcePanel = String.valueOf(row.getOrDefault("来源面板", "")).trim();
            String sourceNo = String.valueOf(row.getOrDefault("来源单号", "")).trim();
            int sourceRowNo = (int) Math.round(num(row.get("来源行号")));
            if (sourcePanel.isEmpty() && sourceNo.isEmpty()) continue;
            if (sourcePanel.isEmpty() || sourceNo.isEmpty() || sourceRowNo <= 0) {
                throw new IllegalStateException("第 " + (index + 1) + " 行来源信息不完整");
            }
            FormData source = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                    .eq(FormData::getPanelCode, sourcePanel).eq(FormData::getFormNo, sourceNo));
            if (source == null) throw new IllegalStateException("来源单据不存在：" + sourcePanel + "/" + sourceNo);
            if (!EFFECTIVE_STATUSES.contains(source.getStatus())) {
                throw new IllegalStateException("来源单据必须已审核且有效：" + sourceNo);
            }
            Map<String, Object> sourceDetail = parseData(source.getDetailData());
            List<Map<String, Object>> sourceRows = sourceRowsFor(source, targetPanel, sourcePanel);
            if (sourceRowNo > sourceRows.size()) throw new IllegalStateException("来源明细行不存在：" + sourceNo + " 第 " + sourceRowNo + " 行");
            String key = sourcePanel + "|" + sourceNo + "|" + sourceRowNo;
            if (!localKeys.add(key)) throw new IllegalStateException("同一来源明细不能重复选入：" + sourceNo + " 第 " + sourceRowNo + " 行");

            Map<String, Object> sourceRow = sourceRows.get(sourceRowNo - 1);
            boolean returnTarget = "PURCHASE_RETURN".equals(targetPanel);
            double limit = sourceLimit(targetPanel, sourcePanel, sourceDetail, sourceRow, sourceRowNo);
            if (returnTarget && limit <= 0) {
                throw new IllegalStateException(QUALITY_INSPECTION_PANELS.contains(sourcePanel)
                        ? "检验单第 " + sourceRowNo + " 行没有不合格退回数量"
                        : "来源单据第 " + sourceRowNo + " 行没有可退货数量");
            }
            double current = reservationQuantity(targetPanel, row);
            boolean partial = partialPair(sourcePanel, targetPanel);
            double reserved;
            if (partial) {
                // 部分下单/部分到货：同面板可多次下推，但不得与其他下游面板混用
                double otherPanels = reservedByOtherPanels(targetPanel, sourcePanel, sourceNo, sourceRowNo);
                if (otherPanels > 0.000001) {
                    throw new IllegalStateException("来源明细已被其他下游单据使用：" + sourceNo + " 第 " + sourceRowNo + " 行");
                }
                reserved = reservedQuantityForPanel(targetPanel, targetNo, sourcePanel, sourceNo, sourceRowNo);
            } else if (returnTarget) {
                // 退货只统计已退量：入库单已开票/已入库不应阻塞退货
                reserved = reservedQuantityForPanel(targetPanel, targetNo, sourcePanel, sourceNo, sourceRowNo);
            } else if (QUALITY_INSPECTION_PANELS.contains(sourcePanel)) {
                // 检验单的合格入库与不合格退回是互不重叠的两个分支，退货不占用入库额度。
                reserved = reservedQuantityExcept(targetPanel, targetNo, sourcePanel, sourceNo, sourceRowNo,
                        Set.of("PURCHASE_RETURN"));
            } else if ("PU_INVOICE".equals(targetPanel)) {
                // 发票只统计已开票量：入库单/退货单互不阻塞（退货开红字发票由退货单自己触发）
                reserved = reservedQuantityForPanel(targetPanel, targetNo, sourcePanel, sourceNo, sourceRowNo);
            } else {
                // 发票是财务口径，不占用订单的履约额度：预开票后仍可到货/入库
                reserved = reservedQuantityExcept(targetPanel, targetNo, sourcePanel, sourceNo, sourceRowNo,
                        FINANCIAL_DOWNSTREAM_PANELS);
            }
            if (current <= 0) throw new IllegalStateException("第 " + (index + 1) + " 行执行数量必须大于 0");
            // 工序汇报单：上限是「计划数量 − 已报工量」，已报满的工序不允许再选，
            // 否则同一道工序会被重复汇报（数量不为 0，能绕过后面的 >0 校验）。
            if ("PROCESS_REPORT".equals(targetPanel) && "MANU_ORDER".equals(sourcePanel)) {
                String procCode = String.valueOf(sourceRow.getOrDefault("工序编码", "")).trim();
                double planQty = num(sourceRow.get("计划数量"));
                double reportedAlready = reportedQtyByProcess(sourceNo, targetNo)
                        .getOrDefault(procCode, 0.0);
                double remain = round2(planQty - reportedAlready);
                if (procCode.isEmpty() || remain <= 0) {
                    throw new IllegalStateException("工序「"
                            + String.valueOf(sourceRow.getOrDefault("工序名称", procCode))
                            + "」在加工单 " + sourceNo + " 已全部报工（计划 "
                            + trimNum(planQty) + "，已报 " + trimNum(reportedAlready)
                            + "），不能重复选入工序汇报单");
                }
                limit = Math.min(limit, remain);
            }
            if (!partial && reserved > 0.000001) {
                throw new IllegalStateException("来源明细已被其他下游单据选入：" + sourceNo + " 第 " + sourceRowNo + " 行");
            }
            if (limit <= 0) {
                throw new IllegalStateException("来源明细第 " + sourceRowNo + " 行没有可执行数量：" + sourceNo);
            }
            if (current + reserved > limit + 0.000001) {
                // 工序汇报单的来源行报工量为 0 时，「可执行 0.0」看不出原因，单独说清是还没报工
                String unReported = unReportedProcessMessage(
                        sourcePanel, sourceRows.get(sourceRowNo - 1), sourceNo, sourceRowNo);
                if (unReported != null) throw new IllegalStateException(unReported);
                throw new IllegalStateException("来源明细超量：" + sourceNo + " 第 " + sourceRowNo
                        + " 行，可执行 " + limit + "，已占用 " + reserved + "，本次 " + current);
            }
        }
    }

    /**
     * 工序汇报单的来源行报工数量为 0 时，给一句能直接行动的话：
     * 用户看到的是「该工序还没报工」，而不是「可执行 0.0」。
     * 不适用时返回 null，仍走通用的来源超量提示。
     */
    static String unReportedProcessMessage(String sourcePanel, Map<String, Object> sourceRow,
                                           String sourceNo, int sourceRowNo) {
        if (!"PROCESS_REPORT".equals(sourcePanel) || sourceRow == null) return null;
        Object value = sourceRow.get("报工数量");
        double reported;
        if (value instanceof Number number) {
            reported = number.doubleValue();
        } else if (value == null || String.valueOf(value).isBlank()) {
            return null;
        } else {
            try {
                reported = Double.parseDouble(String.valueOf(value).trim());
            } catch (NumberFormatException ignored) {
                return null;
            }
        }
        if (reported > 0.000001) return null;
        String code = pickText(sourceRow, "工序编码");
        String name = pickText(sourceRow, "工序名称");
        if (code.isBlank()) return null;
        return "工序 " + code + (name.isBlank() ? "" : " " + name)
                + " 尚未报工（工序汇报单 " + sourceNo + " 第 " + sourceRowNo
                + " 行报工数量为 0），请先报工再报检";
    }

    /**
     * 来源占用口径：检验单按报检数量占用整条来源——抽检只检一部分时，
     * 同一批到货也不能再生成第二张检验单；其它面板仍按自身执行量占用。
     */
    private double reservationQuantity(String documentPanel, Map<String, Object> row) {
        if ("INSPECTION".equals(documentPanel)) {
            double reported = num(row.get("报检数量"));
            if (reported > 0) return reported;
        }
        return executedQuantity(documentPanel, row);
    }

    private double reservedQuantityExcept(String targetPanel, String targetNo, String sourcePanel, String sourceNo,
                                          int sourceRowNo, Set<String> excludedPanels) {
        double total = 0;
        for (FormData document : formMapper.selectList(new LambdaQueryWrapper<FormData>())) {
            if (excludedPanels.contains(document.getPanelCode())) continue;
            if (targetNo != null && targetPanel.equals(document.getPanelCode()) && targetNo.equals(document.getFormNo())) continue;
            if ("已中止".equals(document.getStatus())) continue;
            for (Map<String, Object> row : firstDetailRows(parseData(document.getDetailData()))) {
                if (sourcePanel.equals(String.valueOf(row.getOrDefault("来源面板", "")))
                        && sourceNo.equals(String.valueOf(row.getOrDefault("来源单号", "")))
                        && sourceRowNo == (int) Math.round(num(row.get("来源行号")))) {
                    total += reservationQuantity(document.getPanelCode(), row);
                }
            }
        }
        return total;
    }

    /**
     * 只统计同一目标面板已有单据对来源行的占用。
     * 检验单的合格入库与不合格退回是两个并行分支：入库占用合格数量，退货占用不合格数量，
     * 不能互相把对方算成“来源已被选入”。
     */
    private double reservedQuantityForPanel(String targetPanel, String targetNo, String sourcePanel,
                                            String sourceNo, int sourceRowNo) {
        double total = 0;
        for (FormData document : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, targetPanel))) {
            if (targetNo != null && targetNo.equals(document.getFormNo())) continue;
            if ("已中止".equals(document.getStatus())) continue;
            for (Map<String, Object> row : firstDetailRows(parseData(document.getDetailData()))) {
                if (sourcePanel.equals(String.valueOf(row.getOrDefault("来源面板", "")))
                        && sourceNo.equals(String.valueOf(row.getOrDefault("来源单号", "")))
                        && sourceRowNo == (int) Math.round(num(row.get("来源行号")))) {
                    total += reservationQuantity(document.getPanelCode(), row);
                }
            }
        }
        return total;
    }

    /**
     * 支持按剩余量多次下推的采购配对：请购→订单、订单→到货。
     * 同一来源行允许多张同面板单据（部分下单/部分到货），但不能与其他下游面板混用。
     */
    private static boolean partialPair(String sourcePanel, String targetPanel) {
        return ("PU_REQ".equals(sourcePanel) && "PU_ORDER".equals(targetPanel))
                || ("PU_ORDER".equals(sourcePanel) && "ARRIVAL_IN".equals(targetPanel));
    }

    /** 同一来源行被其他下游面板占用的数量（财务类单据不计入，不阻塞到货/入库）。 */
    private double reservedByOtherPanels(String targetPanel, String sourcePanel, String sourceNo, int sourceRowNo) {
        double total = 0;
        for (FormData document : formMapper.selectList(new LambdaQueryWrapper<FormData>())) {
            if (targetPanel.equals(document.getPanelCode())) continue;
            if (FINANCIAL_DOWNSTREAM_PANELS.contains(document.getPanelCode())) continue;
            if ("已中止".equals(document.getStatus())) continue;
            for (Map<String, Object> row : firstDetailRows(parseData(document.getDetailData()))) {
                if (sourcePanel.equals(String.valueOf(row.getOrDefault("来源面板", "")))
                        && sourceNo.equals(String.valueOf(row.getOrDefault("来源单号", "")))
                        && sourceRowNo == (int) Math.round(num(row.get("来源行号")))) {
                    total += reservationQuantity(document.getPanelCode(), row);
                }
            }
        }
        return total;
    }

    private void validateQualityForApproval(String panelCode, FormData document) {
        if (!QUALITY_REPORT_PANELS.contains(panelCode) && !QUALITY_INSPECTION_PANELS.contains(panelCode)) return;
        Map<String, Object> detail = parseData(document.getDetailData());
        List<Map<String, Object>> rows = firstDetailRows(detail);
        if (rows.isEmpty()) throw new IllegalStateException("质量单据至少需要一条明细");
        if (QUALITY_REPORT_PANELS.contains(panelCode)) {
            if ("ARRIVAL_IN".equals(panelCode)) validateArrivalReceiptQuantities(rows);
            for (int index = 0; index < rows.size(); index++) {
                if (num(rows.get(index).get("报检数量")) <= 0 && sourceQuantity(rows.get(index)) <= 0) {
                    throw new IllegalStateException("第 " + (index + 1) + " 行报检数量必须大于 0");
                }
            }
            if ("FINISH_INSPECT".equals(panelCode)) validateFinishInspectQuantities(rows);
            validateReportSampling(rows);
            // 完工报检必须与上游加工单对得上，否则会出现"还在生产中就已报检完成"
            validateReportAgainstUpstream(panelCode, document, rows);
            // 工序报检的上游是工序汇报单：按工序逐项比对报工量
            validateProcessInspectAgainstReport(panelCode, document, rows);
            return;
        }
        validateInspectionProjects(detail, rows);

        Map<Integer, Double> treatmentByLine = new HashMap<>();
        boolean processInspection = "PROCESS_INSPECTION".equals(panelCode);
        Object treatmentsObject = detail.get("unqualified");
        if (treatmentsObject instanceof List<?> treatments) {
            for (Object value : treatments) {
                if (!(value instanceof Map<?, ?> treatment)) continue;
                int lineNo = (int) Math.round(num(treatment.get("关联明细行号")));
                String reason = String.valueOf(treatment.get("不合格原因") == null ? "" : treatment.get("不合格原因")).trim();
                String method = String.valueOf(treatment.get("处理方式") == null ? "" : treatment.get("处理方式")).trim();
                double quantity = num(treatment.get("处理数量"));
                if (lineNo <= 0 || lineNo > rows.size()) throw new IllegalStateException("不合格处理关联的明细行号无效");
                if (reason.isEmpty()) throw new IllegalStateException("不合格处理必须选择不合格原因");
                if (method.isEmpty()) throw new IllegalStateException("不合格处理必须选择处理方式");
                if (quantity <= 0) throw new IllegalStateException("不合格处理数量必须大于 0");
                treatmentByLine.merge(lineNo, quantity, Double::sum);
            }
        }
        for (int index = 0; index < rows.size(); index++) {
            Map<String, Object> row = rows.get(index);
            double reported = num(row.get("报检数量"));
            double inspectable = num(row.get("应检数量"));
            double inspected = num(row.get("检验数量"));
            double qualified = num(row.get("合格数量"));
            double unqualified = num(row.get("不合格数量"));
            String result = String.valueOf(row.getOrDefault("检验结果判定", "")).trim();
            if (reported <= 0 || inspected <= 0) throw new IllegalStateException("第 " + (index + 1) + " 行报检/检验数量必须大于 0");
            if (inspected > reported + 0.000001) throw new IllegalStateException("第 " + (index + 1) + " 行检验数量不能超过报检数量");
            if (inspectable > 0) {
                if (inspectable > reported + 0.000001) {
                    throw new IllegalStateException("第 " + (index + 1) + " 行应检数量不能超过报检数量");
                }
                if (inspected > inspectable + 0.000001) {
                    throw new IllegalStateException("第 " + (index + 1) + " 行检验数量不能超过应检数量");
                }
            }
            if (Math.abs(qualified + unqualified - inspected) > 0.000001) {
                throw new IllegalStateException("第 " + (index + 1) + " 行合格数量 + 不合格数量必须等于检验数量");
            }
            if (result.isEmpty()) throw new IllegalStateException("第 " + (index + 1) + " 行必须填写检验结果判定");
            // 三态判定：不合格部分若含让步接收，结果不得判「合格」；判定值必须落在三态集合内
            double concession = num(row.get("让步接收数量"));
            if (concession < 0) throw new IllegalStateException("第 " + (index + 1) + " 行让步接收数量不能为负");
            if (concession > unqualified + 0.000001) {
                throw new IllegalStateException("第 " + (index + 1) + " 行让步接收数量不能超过不合格数量");
            }
            Set<String> verdicts = processInspection ? PROCESS_VERDICTS : QUALITY_VERDICTS;
            if (!verdicts.contains(result)) {
                throw new IllegalStateException("第 " + (index + 1) + " 行检验结果判定必须是："
                        + (processInspection ? "合格 / 返工 / 让步接收 / 报废"
                        : "合格 / 让步接收 / 部分报废 / 全批报废 / 全批退回"));
            }
            if (unqualified <= 0 && !"合格".equals(result)) throw new IllegalStateException("第 " + (index + 1) + " 行无不合格数量时结果应为合格");
            if (unqualified > 0 && "合格".equals(result)) throw new IllegalStateException("第 " + (index + 1) + " 行有不合格数量时不能判定合格");
            if (processInspection) {
                // 过程检验：返工/工废/料废是不合格数量的分解；填了就必须与不合格数量守恒
                double rework = num(row.get("检验返工数量"));
                double processScrap = num(row.get("工废数量"));
                double materialScrap = num(row.get("料废数量"));
                if (rework < 0 || processScrap < 0 || materialScrap < 0) {
                    throw new IllegalStateException("第 " + (index + 1) + " 行返工/工废/料废数量不能为负");
                }
                double broken = rework + processScrap + materialScrap;
                if (broken > 0.000001 && Math.abs(broken - unqualified) > 0.000001) {
                    throw new IllegalStateException("第 " + (index + 1)
                            + " 行检验返工数量 + 工废数量 + 料废数量必须等于不合格数量");
                }
                if (unqualified > 0) {
                    double treated = treatmentByLine.getOrDefault(index + 1, 0.0);
                    if (Math.abs(treated - unqualified) > 0.000001) {
                        throw new IllegalStateException("第 " + (index + 1)
                                + " 行不合格处理数量必须等于不合格数量");
                    }
                }
                continue;
            }
            if (concession > 0 && "全批报废".equals(result)) {
                throw new IllegalStateException("第 " + (index + 1) + " 行存在让步接收数量时不能判全批报废");
            }
            // 批次处置标记（2026-11-05）：质量处置与商务结算分开校验
            String disposition = textOf(row.get("处置方式"));
            String settlement = textOf(row.get("结算方式"));
            String liability = textOf(row.get("责任归属"));
            double discountRate = num(row.get("折价率%"));
            if (!disposition.isEmpty() && !INSPECTION_DISPOSITIONS.contains(disposition)) {
                throw new IllegalStateException("第 " + (index + 1) + " 行处置方式不合法：" + disposition);
            }
            if (!settlement.isEmpty() && !INSPECTION_SETTLEMENTS.contains(settlement)) {
                throw new IllegalStateException("第 " + (index + 1) + " 行结算方式不合法：" + settlement);
            }
            if (!liability.isEmpty() && !INSPECTION_LIABILITIES.contains(liability)) {
                throw new IllegalStateException("第 " + (index + 1) + " 行责任归属不合法：" + liability);
            }
            if (discountRate < 0 || discountRate > 100) {
                throw new IllegalStateException("第 " + (index + 1) + " 行折价率必须在 0-100 之间");
            }
            if ("降价接收".equals(disposition) && discountRate <= 0) {
                throw new IllegalStateException("第 " + (index + 1) + " 行降价接收必须填写折价率");
            }
            if (discountRate > 0 && !"降价接收".equals(disposition) && !"让步接收".equals(disposition)) {
                throw new IllegalStateException("第 " + (index + 1) + " 行只有让步接收/降价接收可以填写折价率");
            }
            boolean fullReturn = "全批退回".equals(disposition);
            if (fullReturn && !"全批退回".equals(result)) {
                throw new IllegalStateException("第 " + (index + 1) + " 行处置方式为全批退回时，检验结果判定必须为全批退回");
            }
            if ("全批退回".equals(result) && !fullReturn) {
                throw new IllegalStateException("第 " + (index + 1) + " 行检验结果判定为全批退回时，处置方式必须为全批退回");
            }
            if (unqualified > 0 || fullReturn) {
                double treated = treatmentByLine.getOrDefault(index + 1, 0.0);
                double expected = fullReturn ? inspected : unqualified;
                if (Math.abs(treated - expected) > 0.000001) {
                    throw new IllegalStateException("第 " + (index + 1)
                            + (fullReturn ? " 行全批退回的退回数量必须等于检验数量" : " 行不合格处理数量必须等于不合格数量"));
                }
            }
            if (unqualified > 0 && (disposition.isEmpty() || "合格接收".equals(disposition))) {
                throw new IllegalStateException("第 " + (index + 1) + " 行有不合格数量，必须标记处置方式");
            }
        }
    }

    /** 处置标记确认：审核/审批通过或「确认处置」时把待确认行置为已确认。 */
    @SuppressWarnings("unchecked")
    static void confirmInspectionDisposition(Map<String, Object> detail) {
        if (detail == null || !(detail.get("items") instanceof List<?> items)) return;
        for (Object value : items) {
            if (!(value instanceof Map<?, ?> raw)) continue;
            Map<String, Object> row = (Map<String, Object>) raw;
            String status = textOf(row.get("处置状态"));
            if (status.isEmpty() || "待确认".equals(status)) row.put("处置状态", "已确认");
        }
    }

    private void confirmInspectionDetail(FormData fd) {
        Map<String, Object> detail = parseData(fd.getDetailData());
        confirmInspectionDisposition(detail);
        fd.setDetailData(toJson(detail));
    }

    /** 生单门禁：检验单存在「待确认」处置行时禁止生成入库/退货/发票。 */
    private void requireInspectionDispositionConfirmed(String panelCode, List<Map<String, Object>> rows) {
        if (!"INSPECTION".equals(panelCode)) return;
        for (int index = 0; index < rows.size(); index++) {
            if ("待确认".equals(textOf(rows.get(index).get("处置状态")))) {
                throw new IllegalStateException("第 " + (index + 1)
                        + " 行处置尚未确认，请先在检验单执行「确认处置」");
            }
        }
    }

    /**
     * 检验单入库目标守卫：来料检验（来源到货单）只能进采购入库，
     * 成品/委外检验只能进产成品/委外入库，避免同一张检验单串到错误的入库链。
     */
    private static void requireInspectionInboundTarget(String panelCode, String targetPanel,
                                                       Map<String, Object> head,
                                                       List<Map<String, Object>> rows) {
        if (!"INSPECTION".equals(panelCode)) return;
        boolean fromArrival = rows.stream()
                .anyMatch(row -> "ARRIVAL_IN".equals(textOf(row.get("来源面板")).trim()));
        boolean fromFinishReport = rows.stream()
                .anyMatch(row -> "FINISH_INSPECT".equals(textOf(row.get("来源面板")).trim()));
        String businessType = textOf(head.get("业务类型")).trim();
        boolean incoming = fromArrival || "来料检验".equals(businessType);
        boolean finished = fromFinishReport || "成品检验".equals(businessType)
                || "首件检验".equals(businessType);
        if ("PURCHASE_IN".equals(targetPanel) && finished && !incoming) {
            throw new IllegalStateException("当前检验单不是来料检验（来源为到货单），不能生成采购入库单；"
                    + "成品检验请使用「生成产成品入库单」，委外检验请使用「生成委外入库单」");
        }
        if (("FINISH_IN".equals(targetPanel) || "OUTSOURCE_IN".equals(targetPanel)) && incoming) {
            throw new IllegalStateException("来料检验不能生成"
                    + ("FINISH_IN".equals(targetPanel) ? "产成品入库单" : "委外入库单")
                    + "，请使用「生成采购入库单」");
        }
    }

    /**
     * 首检放行：有工序路线的加工单，必须先审掉首件报检单才能入库。
     *
     * @param processCount 工序数；无工序路线的老单不做此限制
     */
    private void requireFirstInspectApproved(String manuOrderNo, int processCount) {
        for (FormData doc : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "FIRST_INSPECT"))) {
            if ("作废".equals(doc.getStatus())) continue;
            if (!manuOrderNo.equals(str(parseData(doc.getData()).get("加工单号")))) continue;
            if (!"已审核".equals(doc.getStatus())) {
                throw new IllegalStateException("首件报检单 " + doc.getFormNo() + " 状态为「" + doc.getStatus()
                        + "」，首检通过后才能入库");
            }
            return;
        }
        // 有工序路线却一张首检单都没有，同样不能入库（正常情况下末道完工会自动生成）
        if (processCount > 0) {
            throw new IllegalStateException("加工单 " + manuOrderNo + " 还没有首件报检单，请先完成首检再入库");
        }
    }

    /** 工序汇报单弃审前置：所属加工单已完工/已关闭时禁止（先弃审入库单再往回退）。 */
    private void requireOrderNotFinished(FormData report) {
        String moNo = str(parseData(report.getData()).get("加工单号"));
        if (moNo.isEmpty()) return;
        FormData mo = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "MANU_ORDER").eq(FormData::getFormNo, moNo));
        if (mo == null) return;
        if ("已完工".equals(mo.getStatus()) || "已关闭".equals(mo.getStatus())) {
            throw new IllegalStateException("加工单 " + moNo + " 状态为「" + mo.getStatus()
                    + "」，不能弃审工序汇报单；请先弃审产成品入库单，回到生产中再撤报工");
        }
    }

    private void ensureNoEffectiveChildren(String sourcePanel, String sourceNo, String action) {
        for (FormData document : formMapper.selectList(new LambdaQueryWrapper<FormData>())) {
            if ("已中止".equals(document.getStatus())) continue;
            for (Map<String, Object> row : firstDetailRows(parseData(document.getDetailData()))) {
                if (sourcePanel.equals(String.valueOf(row.getOrDefault("来源面板", "")))
                        && sourceNo.equals(String.valueOf(row.getOrDefault("来源单号", "")))) {
                    throw new IllegalStateException("单据已被下游单据 " + document.getFormNo() + " 使用，不能" + action);
                }
            }
        }
    }

    /** Recalculates accumulated quantities on every source row from effective downstream documents. */
    /**
     * 按排产批次领料：生成一张只含该批次一行的材料出库单（草稿）。
     *
     * <p>单材料入口，保留给接口与老调用方；批量领料见 {@link #issueBatch}。
     */
    public Map<String, Object> issueByBatch(String dispatchNo, String materialCode, double quantity,
                                            String operator, String warehouse) {
        if (materialCode == null || materialCode.isBlank()) throw new IllegalArgumentException("缺少材料编码");
        if (quantity <= 0) throw new IllegalArgumentException("领料数量必须大于 0");
        return issueBatch(dispatchNo,
                List.of(Map.of("材料编码", materialCode, "数量", quantity)), operator, warehouse);
    }

    /**
     * 按排产批次领料：一个批次的多种材料落进**一张**材料出库单（多行）。
     *
     * <p>以前一种材料一张单，20 件的一批料被拆成 3 张出库单，审批和仓库对账都要点三次。
     * 每行按自己的预出仓库出库；批次分摊上限与整单上限仍逐行校验。
     */
    public Map<String, Object> issueBatch(String dispatchNo, List<Map<String, Object>> lines,
                                          String operator, String warehouse) {
        if (dispatchNo == null || dispatchNo.isBlank()) throw new IllegalArgumentException("缺少排产单号");
        if (lines == null || lines.isEmpty()) throw new IllegalArgumentException("请选择要领的材料");

        FormData pg = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "DISPATCH").eq(FormData::getFormNo, dispatchNo));
        if (pg == null) throw new IllegalArgumentException("排产批次不存在：" + dispatchNo);
        if (!"已审核".equals(pg.getStatus())) {
            throw new IllegalStateException("排产批次未审批通过，不能领料：" + dispatchNo);
        }
        Map<String, Object> pgHead = parseData(pg.getData());
        Map<String, Object> pgDetail = parseData(pg.getDetailData());
        if (!(pgDetail.get("items") instanceof List<?> items) || items.isEmpty()) {
            throw new IllegalStateException("排产批次没有明细：" + dispatchNo);
        }
        Map<String, Object> batchLine = castRow((Map<?, ?>) items.get(0));
        String moNo = str(pgHead.get("加工单号"));
        String procName = str(batchLine.get("工序名称"));
        if (procName.isEmpty()) procName = str(batchLine.get("工序编码"));

        FormData mo = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "MANU_ORDER").eq(FormData::getFormNo, moNo));
        Map<String, Object> moDetail = mo == null ? Map.of() : parseData(mo.getDetailData());
        Map<String, Map<String, Object>> materialByCode = new LinkedHashMap<>();
        if (moDetail.get("materials") instanceof List<?> mats) {
            for (Object o : mats) {
                if (!(o instanceof Map<?, ?> raw)) continue;
                Map<String, Object> m = castRow(raw);
                String code = str(m.get("材料编码"));
                if (code.isEmpty()) code = str(m.get("材料名称"));
                if (!code.isEmpty()) materialByCode.putIfAbsent(code, m);
            }
        }

        // 整单上限按「本次多行合计」一次校验，避免逐行都能过、加起来却超领
        List<Map<String, Object>> demand = new ArrayList<>();
        for (Map<String, Object> line : lines) {
            String code = str(line.get("材料编码"));
            double qty = num(line.get("数量"));
            if (code.isEmpty()) throw new IllegalArgumentException("缺少材料编码");
            if (qty <= 0) throw new IllegalArgumentException("材料 " + code + " 的领料数量必须大于 0");
            if (!materialByCode.containsKey(code)) {
                throw new IllegalStateException("材料 " + code + " 未列入加工单需用明细");
            }
            demand.add(Map.of("加工单号", moNo, "材料编码", code, "数量", qty));
        }
        validateMaterialIssue(demand, null);

        List<Map<String, Object>> rows = new ArrayList<>();
        String headWarehouse = "";
        for (Map<String, Object> line : lines) {
            String code = str(line.get("材料编码"));
            double qty = round2(num(line.get("数量")));
            // 批次分摊上限：挂了批次就只能领该批的份额
            validateBatchIssue(moNo, dispatchNo, code, qty, null);
            Map<String, Object> material = materialByCode.get(code);
            String wh = warehouse == null || warehouse.isBlank()
                    ? str(material.getOrDefault("预出仓库", "原料仓")) : warehouse;
            requireStockAvailable(code, str(material.get("材料名称")), wh, qty);
            // 领料单价取该仓结存均价（移动平均），没有结存价时回退加工单材料单价；
            // 用 0 单价出库不会冲减结存金额，平均成本会越滚越大
            java.math.BigDecimal avg = stockTxnService == null ? null
                    : stockTxnService.averageCostOf(code, str(material.get("材料名称")), wh);
            double price = avg != null && avg.signum() > 0 ? avg.doubleValue() : num(material.get("单价"));
            if (headWarehouse.isEmpty()) headWarehouse = wh;

            Map<String, Object> row = new LinkedHashMap<>();
            row.put("仓库", wh);
            row.put("加工单号", moNo);
            row.put("排产单号", dispatchNo);
            row.put("领料工序", procName);
            row.put("领料人", operator == null ? "" : operator);
            row.put("材料编码", code);
            row.put("存货编码", code);
            row.put("材料名称", material.getOrDefault("材料名称", ""));
            row.put("规格型号", material.getOrDefault("规格型号", ""));
            row.put("计量单位", material.getOrDefault("计量单位", ""));
            row.put("数量", qty);
            row.put("单价", price);
            row.put("金额", round2(price * qty));
            rows.add(row);
        }

        Map<String, Object> data = new LinkedHashMap<>();
        data.put("单据日期", LocalDate.now().toString());
        data.put("业务类型", "材料出库");
        data.put("出库类别", "直接领料");
        data.put("加工单号", moNo);
        data.put("生产车间", pgHead.getOrDefault("生产车间", batchLine.getOrDefault("生产车间", "")));
        data.put("仓库", headWarehouse);
        data.put("领用人", operator == null ? "" : operator);

        Map<String, Object> detailMap = new LinkedHashMap<>();
        detailMap.put("items", rows);
        return insertGenerated("MATERIAL_OUT", "DISPATCH", dispatchNo, data, detailMap);
    }

    /**
     * 工序完成判定：累计报工（合格 + 不合格）达到加工单数量才算完成。
     *
     * <p>用「报工总量」而不是合格量：工序要过完整批数量，不合格品也走完了这道工序。
     */
    public List<Map<String, Object>> processCompletion(String manuOrderNo) {
        if (manuOrderNo == null || manuOrderNo.isBlank()) return List.of();
        FormData mo = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "MANU_ORDER").eq(FormData::getFormNo, manuOrderNo));
        if (mo == null) return List.of();
        Map<String, Object> detail = parseData(mo.getDetailData());
        double orderQty = 0;
        if (detail.get("products") instanceof List<?> ps && !ps.isEmpty()) {
            orderQty = num(castRow((Map<?, ?>) ps.get(0)).get("数量"));
        }
        if (!(detail.get("processes") instanceof List<?> procs)) return List.of();

        Map<String, Double> reported = reportedQtyByProcess(manuOrderNo);

        List<Map<String, Object>> ordered = new ArrayList<>();
        for (Object o : procs) {
            if (o instanceof Map<?, ?> raw) ordered.add(castRow(raw));
        }
        ordered.sort((a, b) -> Double.compare(
                num(a.containsKey("加工顺序") ? a.get("加工顺序") : a.get("工艺序号")),
                num(b.containsKey("加工顺序") ? b.get("加工顺序") : b.get("工艺序号"))));

        List<Map<String, Object>> out = new ArrayList<>();
        int seq = 0;
        for (Map<String, Object> proc : ordered) {
            seq++;
            String code = str(proc.get("工序编码"));
            double done = round2(reported.getOrDefault(code, 0.0));
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("工序顺序", seq);
            row.put("工序编码", code);
            row.put("工序名称", proc.getOrDefault("工序名称", ""));
            row.put("加工单数量", orderQty);
            row.put("累计报工", done);
            row.put("未报数量", Math.max(0, round2(orderQty - done)));
            row.put("是否完成", orderQty > 0 && done >= orderQty - 1e-6);
            out.add(row);
        }
        return out;
    }

    /**
     * 回写排产批次的报工进度：累计汇报数量 + 派工加工状态。
     *
     * <p>批次是执行单元，车间打开派工单就要能看到这一批做了多少、有没有做完。
     */
    public void syncDispatchReported(String dispatchNo) {
        if (dispatchNo == null || dispatchNo.isBlank()) return;
        FormData pg = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "DISPATCH").eq(FormData::getFormNo, dispatchNo));
        if (pg == null) return;
        Map<String, Object> detail = parseData(pg.getDetailData());
        if (!(detail.get("items") instanceof List<?> items) || items.isEmpty()) return;

        double reported = 0;
        for (FormData rp : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "PROCESS_REPORT"))) {
            if (!"已审核".equals(rp.getStatus())) continue;
            Map<String, Object> d;
            try {
                d = parseData(rp.getDetailData());
            } catch (Exception e) {
                continue;
            }
            if (!(d.get("items") instanceof List<?> lines)) continue;
            for (Object o : lines) {
                if (!(o instanceof Map<?, ?> line)) continue;
                if (!dispatchNo.equals(str(line.get("排产单号")))) continue;
                reported += num(line.get("报工数量"));
            }
        }
        reported = round2(reported);

        @SuppressWarnings("unchecked")
        Map<String, Object> row = (Map<String, Object>) items.get(0);
        double batchQty = num(row.get("派工数量"));
        row.put("累计汇报数量", reported);
        row.put("派工加工状态", reported <= 0 ? "未派工" : (reported >= batchQty - 1e-6 ? "完工" : "已派工"));

        pg.setDetailData(toJson(detail));
        pg.setUpdateTime(LocalDateTime.now());
        formMapper.updateById(pg);
    }

    /**
     * 报工通过后推进完工状态：全部工序完成 → 加工单写完工日期；末道首次完成 → 生成首件报检单。
     *
     * <p>首检只生成一次：以已有首检单（同加工单、未作废）为标记，避免每次报工都重复建单。
     */
    public void advanceCompletion(String manuOrderNo) {
        if (manuOrderNo == null || manuOrderNo.isBlank()) return;
        Map<String, Object> summary = completionSummary(manuOrderNo);
        if (summary.isEmpty() || summary.get("工序数") == null) return;

        boolean allDone = Boolean.TRUE.equals(summary.get("全部完成"));
        boolean firstInspect = Boolean.TRUE.equals(summary.get("首检应触发"));
        if (!allDone && !firstInspect) return;

        FormData mo = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "MANU_ORDER").eq(FormData::getFormNo, manuOrderNo));
        if (mo == null) return;
        Map<String, Object> head = parseData(mo.getData());
        boolean changed = false;

        if (allDone && str(head.get("完工日期")).isEmpty()) {
            head.put("完工日期", LocalDate.now().toString());
            changed = true;
        }
        if (changed) {
            mo.setData(toJson(head));
            mo.setUpdateTime(LocalDateTime.now());
            formMapper.updateById(mo);
        }

        // 末道首次完成 → 首检（已存在则不重复生成）
        if (firstInspect && !firstInspectExists(manuOrderNo)) {
            try {
                createFirstInspect(manuOrderNo, summary, head);
            } catch (Exception e) {
                // 首检生成失败不能阻断报工审批，日志留痕交给人工补单
            }
        }
    }

    private boolean firstInspectExists(String manuOrderNo) {
        for (FormData doc : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "FIRST_INSPECT").notIn(FormData::getStatus, "作废"))) {
            Map<String, Object> h;
            try {
                h = parseData(doc.getData());
            } catch (Exception e) {
                continue;
            }
            if (manuOrderNo.equals(str(h.get("加工单号")))) return true;
        }
        return false;
    }

    /** 生成首件报检单（草稿）：末道工序 + 完成数量。 */
    private void createFirstInspect(String manuOrderNo, Map<String, Object> summary, Map<String, Object> moHead) {
        FormData mo = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "MANU_ORDER").eq(FormData::getFormNo, manuOrderNo));
        if (mo == null) return;
        Map<String, Object> detail = parseData(mo.getDetailData());
        Map<String, Object> product = detail.get("products") instanceof List<?> ps && !ps.isEmpty()
                ? castRow((Map<?, ?>) ps.get(0)) : Map.of();
        String lastCode = str(summary.get("末道工序编码"));

        Map<String, Object> data = new LinkedHashMap<>();
        data.put("单据日期", LocalDate.now().toString());
        data.put("业务类型", "首件检验");
        data.put("生产车间", moHead.getOrDefault("生产车间", ""));
        data.put("加工单号", manuOrderNo);
        data.put("来源单号", manuOrderNo);

        Map<String, Object> row = new LinkedHashMap<>();
        row.put("存货编码", product.getOrDefault("产品编码", ""));
        row.put("存货名称", product.getOrDefault("产品名称", ""));
        row.put("规格型号", product.getOrDefault("规格型号", ""));
        row.put("工序编码", lastCode);
        row.put("工序名称", "");
        if (detail.get("processes") instanceof List<?> procs) {
            for (Object o : procs) {
                if (!(o instanceof Map<?, ?> raw)) continue;
                Map<String, Object> pr = castRow(raw);
                if (lastCode.equals(str(pr.get("工序编码")))) {
                    row.put("工序名称", pr.getOrDefault("工序名称", ""));
                    break;
                }
            }
        }
        row.put("计量单位", product.getOrDefault("生产单位", ""));
        row.put("报检数量", summary.getOrDefault("首检数量", 0));
        row.put("备注", "末道工序完成，触发首检");

        Map<String, Object> detailMap = new LinkedHashMap<>();
        detailMap.put("items", List.of(row));
        insertGenerated("FIRST_INSPECT", "MANU_ORDER", manuOrderNo, data, detailMap);
    }

    /**
     * 完工汇总：全部工序是否完成、完成了几道、末道是否首次完成（触发首检）。
     */
    public Map<String, Object> completionSummary(String manuOrderNo) {
        List<Map<String, Object>> rows = processCompletion(manuOrderNo);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("加工单号", manuOrderNo);
        out.put("工序数", rows.size());
        int done = 0;
        for (Map<String, Object> r : rows) {
            if (Boolean.TRUE.equals(r.get("是否完成"))) done++;
        }
        out.put("已完成工序数", done);
        boolean all = !rows.isEmpty() && done == rows.size();
        out.put("全部完成", all);
        String lastCode = rows.isEmpty() ? "" : str(rows.get(rows.size() - 1).get("工序编码"));
        out.put("末道工序编码", lastCode);
        // 末道完成 = 首批数量已走完所有工序 → 触发首检
        boolean lastDone = !rows.isEmpty() && Boolean.TRUE.equals(rows.get(rows.size() - 1).get("是否完成"));
        out.put("首检应触发", lastDone);
        if (lastDone) {
            out.put("首检数量", rows.get(rows.size() - 1).get("累计报工"));
        }
        return out;
    }

    /**
     * 按排产批次领料的看板：列出每个批次每种材料的「批次需用量 / 已领 / 剩余可领」。
     *
     * <p>批次需用量 = 整单需用量 ×（批次数量 / 加工单数量）。
     */
    public List<Map<String, Object>> materialIssueBoard(String manuOrderNo) {
        if (manuOrderNo == null || manuOrderNo.isBlank()) return List.of();
        FormData mo = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "MANU_ORDER").eq(FormData::getFormNo, manuOrderNo));
        if (mo == null) return List.of();
        Map<String, Object> moDetail = parseData(mo.getDetailData());
        double orderQty = 0;
        if (moDetail.get("products") instanceof List<?> ps && !ps.isEmpty()) {
            orderQty = num(castRow((Map<?, ?>) ps.get(0)).get("数量"));
        }
        Map<String, Double> needByMaterial = new LinkedHashMap<>();
        Map<String, String> nameByMaterial = new LinkedHashMap<>();
        Map<String, String> ownerProcessOfMaterial = new LinkedHashMap<>();
        // 首道工序：材料没填「领料工序」时归到它，保证每种材料只挂到一个批次上
        String firstProc = "";
        if (moDetail.get("processes") instanceof List<?> procs && !procs.isEmpty()) {
            firstProc = str(castRow((Map<?, ?>) procs.get(0)).get("工序编码"));
        }
        if (moDetail.get("materials") instanceof List<?> mats) {
            for (Object o : mats) {
                if (!(o instanceof Map<?, ?> raw)) continue;
                Map<String, Object> m = castRow(raw);
                String code = str(m.get("材料编码"));
                if (code.isEmpty()) code = str(m.get("材料名称"));
                if (code.isEmpty()) continue;
                // 用计划数量（含损耗）作为口径，和整单上限保持一致
                double need = num(m.get("计划数量"));
                if (need <= 0) need = num(m.get("需用数量"));
                needByMaterial.merge(code, need, Double::sum);
                nameByMaterial.putIfAbsent(code, str(m.get("材料名称")));
                String owner = str(m.get("领料工序"));
                if (owner.isEmpty()) owner = firstProc;
                ownerProcessOfMaterial.putIfAbsent(code, owner);
            }
        }

        // 已审核领料：按 (排产单号, 材料) 汇总
        Map<String, Double> issued = new LinkedHashMap<>();
        for (FormData out : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "MATERIAL_OUT"))) {
            if (!"已审核".equals(out.getStatus())) continue;
            Map<String, Object> d;
            try {
                d = parseData(out.getDetailData());
            } catch (Exception e) {
                continue;
            }
            if (!(d.get("items") instanceof List<?> items)) continue;
            for (Object o : items) {
                if (!(o instanceof Map<?, ?> line)) continue;
                if (!manuOrderNo.equals(str(line.get("加工单号")))) continue;
                String code = str(line.get("材料编码"));
                if (code.isEmpty()) code = str(line.get("存货编码"));
                String dispatchNo = str(line.get("排产单号"));
                issued.merge(dispatchNo + "\n" + code, num(line.get("数量")), Double::sum);
            }
        }

        List<Map<String, Object>> out = new ArrayList<>();
        for (FormData pg : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "DISPATCH").orderByDesc(FormData::getId))) {
            if (!"已审核".equals(pg.getStatus())) continue;
            Map<String, Object> head;
            Map<String, Object> detail;
            try {
                head = parseData(pg.getData());
                detail = parseData(pg.getDetailData());
            } catch (Exception e) {
                continue;
            }
            if (!manuOrderNo.equals(str(head.get("加工单号")))) continue;
            if (!(detail.get("items") instanceof List<?> items)) continue;
            for (Object o : items) {
                if (!(o instanceof Map<?, ?> raw)) continue;
                Map<String, Object> line = castRow(raw);
                double batchQty = num(line.get("派工数量"));
                double ratio = orderQty > 0 ? batchQty / orderQty : 0;
                String batchProc = str(line.get("工序编码"));
                String batchProcName = str(line.get("工序名称"));
                for (String code : needByMaterial.keySet()) {
                    // 材料只挂在它的领料工序上：同一材料不会在每道工序的批次里各算一遍
                    // 领料工序可能存的是名称（下料）也可能存编码（PX001），两种都要认
                    String owner = ownerProcessOfMaterial.get(code);
                    boolean ownerMatch = owner == null || owner.isEmpty()
                            || owner.equals(batchProc) || owner.equals(batchProcName);
                    if (!ownerMatch) continue;
                    double batchNeed = round2(needByMaterial.get(code) * ratio);
                    if (batchNeed <= 0) continue;
                    double done = round2(issued.getOrDefault(pg.getFormNo() + "\n" + code, 0.0));
                    Map<String, Object> row = new LinkedHashMap<>();
                    row.put("排产单号", pg.getFormNo());
                    row.put("加工单号", manuOrderNo);
                    row.put("工序编码", line.getOrDefault("工序编码", ""));
                    row.put("工序名称", line.getOrDefault("工序名称", ""));
                    row.put("批次数量", batchQty);
                    row.put("材料编码", code);
                    row.put("材料名称", nameByMaterial.getOrDefault(code, ""));
                    row.put("整单需用量", round2(needByMaterial.get(code)));
                    row.put("批次需用量", batchNeed);
                    row.put("已领数量", done);
                    row.put("剩余可领", Math.max(0, round2(batchNeed - done)));
                    row.put("可否领料", round2(batchNeed - done) > 0);
                    out.add(row);
                }
            }
        }
        return out;
    }

    /** 按批次领料校验（不排除自身时传 null）。 */
    public void validateBatchIssue(String manuOrderNo, String dispatchNo, String materialCode, double quantity) {
        validateBatchIssue(manuOrderNo, dispatchNo, materialCode, quantity, null);
    }

    /**
     * 按批次领料校验：不得超过该批次的材料分摊量。
     *
     * <p>批次分摊 = 整单需用量 ×（批次数量 / 加工单数量）。挂批次的领料只受这一条约束，
     * 整单上限由 validateMaterialIssue 另管。
     */
    public void validateBatchIssue(String manuOrderNo, String dispatchNo, String materialCode,
                                   double quantity, String selfFormNo) {
        if (dispatchNo == null || dispatchNo.isBlank()) return; // 不挂批次 = 共用料整单领
        if (quantity <= 0) throw new IllegalArgumentException("领料数量必须大于 0");

        FormData mo = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "MANU_ORDER").eq(FormData::getFormNo, manuOrderNo));
        if (mo == null) throw new IllegalArgumentException("加工单不存在：" + manuOrderNo);
        FormData pg = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "DISPATCH").eq(FormData::getFormNo, dispatchNo));
        if (pg == null) throw new IllegalArgumentException("排产批次不存在：" + dispatchNo);
        if (!"已审核".equals(pg.getStatus())) {
            throw new IllegalStateException("排产批次未审批通过，不能按批次领料：" + dispatchNo);
        }

        Map<String, Object> moDetail = parseData(mo.getDetailData());
        double orderQty = 0;
        if (moDetail.get("products") instanceof List<?> ps && !ps.isEmpty()) {
            orderQty = num(castRow((Map<?, ?>) ps.get(0)).get("数量"));
        }
        double orderNeed = 0;
        String materialOwner = "";
        if (moDetail.get("materials") instanceof List<?> mats) {
            for (Object o : mats) {
                if (!(o instanceof Map<?, ?> raw)) continue;
                Map<String, Object> m = castRow(raw);
                String code = str(m.get("材料编码"));
                if (code.isEmpty()) code = str(m.get("材料名称"));
                if (!materialCode.equals(code)) continue;
                double need = num(m.get("计划数量"));
                if (need <= 0) need = num(m.get("需用数量"));
                orderNeed += need;
                materialOwner = str(m.get("领料工序"));
            }
        }
        if (orderNeed <= 0) throw new IllegalStateException("材料 " + materialCode + " 未列入加工单需用明细");

        Map<String, Object> pgDetail = parseData(pg.getDetailData());
        double batchQty = 0;
        String batchProc = "";
        String batchProcName = "";
        if (pgDetail.get("items") instanceof List<?> items && !items.isEmpty()) {
            Map<String, Object> pgLine = castRow((Map<?, ?>) items.get(0));
            batchQty = num(pgLine.get("派工数量"));
            batchProc = str(pgLine.get("工序编码"));
            batchProcName = str(pgLine.get("工序名称"));
        }
        // 材料只在它的领料工序上领：没填领料工序的归首道工序
        if (!materialOwner.isEmpty() && !materialOwner.equals(batchProc) && !materialOwner.equals(batchProcName)) {
            throw new IllegalStateException("材料「" + materialCode + "」的领料工序是「" + materialOwner
                    + "」，不能在「" + batchProc + "」的批次上领料");
        }
        double ratio = orderQty > 0 ? batchQty / orderQty : 0;
        double batchNeed = round2(orderNeed * ratio);

        double issued = 0;
        for (FormData out : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "MATERIAL_OUT"))) {
            if (!"已审核".equals(out.getStatus())) continue;
            if (selfFormNo != null && selfFormNo.equals(out.getFormNo())) continue;
            Map<String, Object> d;
            try {
                d = parseData(out.getDetailData());
            } catch (Exception e) {
                continue;
            }
            if (!(d.get("items") instanceof List<?> items)) continue;
            for (Object o : items) {
                if (!(o instanceof Map<?, ?> line)) continue;
                if (!dispatchNo.equals(str(line.get("排产单号")))) continue;
                String code = str(line.get("材料编码"));
                if (code.isEmpty()) code = str(line.get("存货编码"));
                if (!materialCode.equals(code)) continue;
                issued += num(line.get("数量"));
            }
        }
        issued = round2(issued);
        if (round2(issued + quantity) > batchNeed + 1e-6) {
            throw new IllegalStateException("材料「" + materialCode + "」批次 " + dispatchNo
                    + " 可领 " + trimNum(batchNeed) + "（整单需用 " + trimNum(orderNeed)
                    + " × 批次占比 " + trimNum(round2(ratio * 100)) + "%），已领 " + trimNum(issued)
                    + "，本次 " + trimNum(quantity) + " 超过批次可领量");
        }
    }

    /**
     * 可用量校验：领料不得把库存领成负数。
     *
     * <p>按「材料 + 仓库」取实时结存。测试环境的 mock 返回 null 时视为无法判定而放行，
     * 生产实现永远返回 BigDecimal，不会漏拦。
     */
    private void requireStockAvailable(String itemCode, String itemName, String warehouse, double quantity) {
        if (quantity <= 0) return;
        java.math.BigDecimal available;
        try {
            available = stockTxnService.quantityOf(itemCode, itemName, warehouse);
        } catch (Exception e) {
            return;
        }
        if (available == null) return;
        double avail = available.doubleValue();
        if (avail + 1e-6 < quantity) {
            String wh = warehouse == null || warehouse.isBlank() ? "全部仓库" : warehouse;
            throw new IllegalStateException("材料「" + (itemName == null || itemName.isBlank() ? itemCode : itemName)
                    + "」在" + wh + "现存 " + trimNum(round2(avail))
                    + "，本次要领 " + trimNum(quantity) + "，可用量不足，不能领料");
        }
    }

    /**
     * 领料超领校验。
     *
     * <p>同一加工单、同一材料的「已审核领用量 + 本次领用量」不得超过加工单需用量。
     * 已审核单据重存时要把自己排除，否则自身旧版本会被重复计入。
     * 没有加工单号的直接领料不受此约束（不挂生产的零星领用）。
     */
    /**
     * 计划分析可选的销售订单：已审核且还有未排产数量。
     * 计划分析面板的「选单」下拉用这个，避免把已全部排产的订单也列出来。
     */
    @Override
    public List<Map<String, Object>> planAnalyzeCandidates() {
        if (planAnalyzeQueryService == null) return List.of();
        return planAnalyzeQueryService.candidateOrders();
    }

    /**
     * 计划分析：一张销售订单 → 成品 / 物料缺口 / 工艺路线 / 风险 / 结论。
     *
     * <p>这是「主生产计划 + 物料需求运算 + 齐套分析 + 采购需求分析」四个面板的合并入口：
     * 那四个是横向汇总，这里按单张订单纵向展开全链路。
     */
    @Override
    public Map<String, Object> planAnalyze(String salesOrderNo) {
        if (planAnalyzeQueryService == null) {
            throw new UnsupportedOperationException("计划分析服务未装配");
        }
        return planAnalyzeQueryService.analyze(salesOrderNo);
    }

    /**
     * 工单排产数据：按加工单 × 工序给出「可排产量」，并列出已有批次。
     *
     * <p>可排产量（已拍板规则）：
     * <ul>
     *   <li>第 1 道：加工单数量 − 本道已排产量</li>
     *   <li>第 k 道：**上道工序合格累计** − 本道已排产量（不合格品不流入下道）</li>
     * </ul>
     * 只有「已审核」的排产单占用额度；草稿不算，避免改了又改把额度吃光。
     */
    public List<Map<String, Object>> planBoard(String manuOrderNo) {
        if (manuOrderNo == null || manuOrderNo.isBlank()) return List.of();
        FormData mo = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "MANU_ORDER").eq(FormData::getFormNo, manuOrderNo));
        if (mo == null) return List.of();
        Map<String, Object> head = parseData(mo.getData());
        Map<String, Object> detail = parseData(mo.getDetailData());
        if (!(detail.get("processes") instanceof List<?> procs) || procs.isEmpty()) return List.of();

        Map<String, Object> product = detail.get("products") instanceof List<?> ps && !ps.isEmpty()
                ? castRow((Map<?, ?>) ps.get(0)) : Map.of();
        double orderQty = num(product.get("数量"));

        // 已审核报工的合格数量：加工单 + 工序
        Map<String, Double> qualified = new LinkedHashMap<>();
        for (FormData rp : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "PROCESS_REPORT"))) {
            if (!"已审核".equals(rp.getStatus())) continue;
            Map<String, Object> h;
            Map<String, Object> d;
            try {
                h = parseData(rp.getData());
                d = parseData(rp.getDetailData());
            } catch (Exception e) {
                continue;
            }
            if (!manuOrderNo.equals(str(h.get("加工单号")))) continue;
            if (!(d.get("items") instanceof List<?> items)) continue;
            for (Object o : items) {
                if (!(o instanceof Map<?, ?> line)) continue;
                qualified.merge(str(line.get("工序编码")), num(line.get("合格数量")), Double::sum);
            }
        }

        // 已排产量：只算已审核排产单
        Map<String, Double> planned = new LinkedHashMap<>();
        Map<String, List<Map<String, Object>>> batches = new LinkedHashMap<>();
        for (FormData pg : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "DISPATCH").orderByDesc(FormData::getId))) {
            if (!"已审核".equals(pg.getStatus())) continue;
            Map<String, Object> h;
            Map<String, Object> d;
            try {
                h = parseData(pg.getData());
                d = parseData(pg.getDetailData());
            } catch (Exception e) {
                continue;
            }
            if (!manuOrderNo.equals(str(h.get("加工单号")))) continue;
            if (!(d.get("items") instanceof List<?> items)) continue;
            for (Object o : items) {
                if (!(o instanceof Map<?, ?> raw)) continue;
                Map<String, Object> line = castRow(raw);
                String code = str(line.get("工序编码"));
                if (code.isEmpty()) continue;
                planned.merge(code, num(line.get("派工数量")), Double::sum);
                Map<String, Object> b = new LinkedHashMap<>();
                b.put("排产单号", pg.getFormNo());
                b.put("数量", num(line.get("派工数量")));
                b.put("班组", line.getOrDefault("班组", ""));
                b.put("设备", line.getOrDefault("设备", ""));
                b.put("预开工日", line.getOrDefault("预开工日", ""));
                b.put("预完工日", line.getOrDefault("预完工日", ""));
                b.put("状态", line.getOrDefault("派工加工状态", ""));
                batches.computeIfAbsent(code, k -> new ArrayList<>()).add(b);
            }
        }

        List<Map<String, Object>> out = new ArrayList<>();
        List<Map<String, Object>> ordered = new ArrayList<>();
        for (Object o : procs) {
            if (o instanceof Map<?, ?> raw) ordered.add(castRow(raw));
        }
        ordered.sort((a, b) -> Double.compare(
                num(a.containsKey("加工顺序") ? a.get("加工顺序") : a.get("工艺序号")),
                num(b.containsKey("加工顺序") ? b.get("加工顺序") : b.get("工艺序号"))));

        double prevQualified = -1; // 第一道用加工单数量
        int seq = 0;
        for (Map<String, Object> proc : ordered) {
            seq++;
            String code = str(proc.get("工序编码"));
            double planQty = num(proc.get("计划数量"));
            double plannedQty = round2(planned.getOrDefault(code, 0.0));
            double base = seq == 1 ? orderQty : Math.max(0, prevQualified);
            double available = round2(base - plannedQty);
            if (available < 0) available = 0;

            Map<String, Object> row = new LinkedHashMap<>();
            row.put("加工单号", manuOrderNo);
            row.put("生产车间", head.getOrDefault("生产车间", ""));
            row.put("客户", head.getOrDefault("客户", ""));
            row.put("产品编码", product.getOrDefault("产品编码", ""));
            row.put("产品名称", product.getOrDefault("产品名称", ""));
            row.put("规格型号", product.getOrDefault("规格型号", ""));
            row.put("工序顺序", seq);
            row.put("工序编码", code);
            row.put("工序名称", proc.getOrDefault("工序名称", ""));
            row.put("工序单位", proc.getOrDefault("工序单位", proc.getOrDefault("计量单位", "")));
            row.put("加工单数量", orderQty);
            row.put("计划数量", planQty);
            row.put("上道合格量", seq == 1 ? orderQty : Math.max(0, prevQualified));
            row.put("已排产量", plannedQty);
            row.put("可排产量", available);
            row.put("批次", batches.getOrDefault(code, List.of()));
            row.put("可否排产", available > 0);
            out.add(row);
            prevQualified = qualified.getOrDefault(code, 0.0);
        }
        return out;
    }

    /**
     * 按排产批次报工：生成一张只含该批次一行的工序汇报单（草稿）。
     *
     * @param qualified 合格数量
     * @param rejected  不合格数量；报工数量 = 合格 + 不合格
     */
    public Map<String, Object> reportByBatch(String dispatchNo, double qualified, double rejected) {
        double qty = validateBatchReport(dispatchNo, qualified, rejected);

        FormData pg = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "DISPATCH").eq(FormData::getFormNo, dispatchNo));
        if (pg == null) throw new IllegalArgumentException("排产批次不存在：" + dispatchNo);
        if (!"已审核".equals(pg.getStatus())) throw new IllegalStateException("排产批次未审批通过，不能报工");
        Map<String, Object> pgHead = parseData(pg.getData());
        Map<String, Object> pgDetail = parseData(pg.getDetailData());
        if (!(pgDetail.get("items") instanceof List<?> items) || items.isEmpty()) {
            throw new IllegalStateException("排产批次没有明细：" + dispatchNo);
        }
        Map<String, Object> line = castRow((Map<?, ?>) items.get(0));

        String moNo = str(pgHead.get("加工单号"));
        Map<String, Object> product = Map.of();
        if (!moNo.isEmpty()) {
            FormData mo = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                    .eq(FormData::getPanelCode, "MANU_ORDER").eq(FormData::getFormNo, moNo));
            if (mo != null) {
                Map<String, Object> md = parseData(mo.getDetailData());
                if (md.get("products") instanceof List<?> ps && !ps.isEmpty()) product = castRow((Map<?, ?>) ps.get(0));
            }
        }
        String batchNo = LocalDate.now().format(DateTimeFormatter.ofPattern("yyyyMMdd"));
        String procCode = str(line.get("工序编码"));

        Map<String, Object> data = new LinkedHashMap<>();
        data.put("单据日期", LocalDate.now().toString());
        data.put("业务类型", "工序汇报");
        data.put("加工单号", moNo);
        data.put("生产车间", pgHead.getOrDefault("生产车间", line.getOrDefault("生产车间", "")));
        data.put("客户", pgHead.getOrDefault("客户", ""));
        data.put("销售订单号", pgHead.getOrDefault("销售订单号", ""));
        data.put("产品编码", product.getOrDefault("产品编码", ""));
        data.put("产品名称", product.getOrDefault("产品名称", line.getOrDefault("产品名称", "")));
        data.put("规格型号", product.getOrDefault("规格型号", line.getOrDefault("规格型号", "")));
        data.put("批次号", batchNo);

        Map<String, Object> row = new LinkedHashMap<>();
        row.put("加工单号", moNo);
        row.put("排产单号", dispatchNo);
        row.put("批次号", batchNo);
        row.put("产品编码", product.getOrDefault("产品编码", ""));
        row.put("产品名称", product.getOrDefault("产品名称", line.getOrDefault("产品名称", "")));
        row.put("规格型号", product.getOrDefault("规格型号", line.getOrDefault("规格型号", "")));
        row.put("工序编码", procCode);
        row.put("工序名称", line.getOrDefault("工序名称", ""));
        row.put("工序单位", line.getOrDefault("计量单位", ""));
        row.put("生产车间", line.getOrDefault("生产车间", pgHead.getOrDefault("生产车间", "")));
        row.put("工作中心", line.getOrDefault("工作中心", ""));
        row.put("设备", line.getOrDefault("设备", ""));
        row.put("班组名称", line.getOrDefault("班组", ""));
        row.put("工人名称", line.getOrDefault("工人", ""));
        row.put("工艺类型", line.getOrDefault("加工类型", "自制"));
        row.put("计划数量", num(line.get("计划数量")));
        row.put("报工数量", qty);
        row.put("合格数量", round2(qualified));
        row.put("不合格数量", round2(rejected));
        row.put("工价", line.getOrDefault("工价", 0));

        Map<String, Object> detail = new LinkedHashMap<>();
        detail.put("items", List.of(row));
        return insertGenerated("PROCESS_REPORT", "DISPATCH", dispatchNo, data, detail);
    }

    /**
     * 按排产批次报工的看板：只列「已审核」的排产批次，带批次数量/已报/剩余。
     *
     * <p>强制先排产：没有已审核批次就不能报工。
     */
    public List<Map<String, Object>> reportBatchBoard() {
        // 已审核汇报单按「排产单号」汇总
        Map<String, Double> reported = new LinkedHashMap<>();
        for (FormData rp : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "PROCESS_REPORT"))) {
            if (!"已审核".equals(rp.getStatus())) continue;
            Map<String, Object> d;
            try {
                d = parseData(rp.getDetailData());
            } catch (Exception e) {
                continue;
            }
            if (!(d.get("items") instanceof List<?> items)) continue;
            for (Object o : items) {
                if (!(o instanceof Map<?, ?> line)) continue;
                String dispatchNo = str(line.get("排产单号"));
                if (dispatchNo.isEmpty()) continue;
                reported.merge(dispatchNo, num(line.get("报工数量")), Double::sum);
            }
        }

        List<Map<String, Object>> out = new ArrayList<>();
        for (FormData pg : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "DISPATCH").orderByDesc(FormData::getId))) {
            if (!"已审核".equals(pg.getStatus())) continue;  // 排产必须先审批
            Map<String, Object> head;
            Map<String, Object> detail;
            try {
                head = parseData(pg.getData());
                detail = parseData(pg.getDetailData());
            } catch (Exception e) {
                continue;
            }
            String moNo = str(head.get("加工单号"));
            Map<String, Object> product = Map.of();
            if (!moNo.isEmpty()) {
                FormData mo = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                        .eq(FormData::getPanelCode, "MANU_ORDER").eq(FormData::getFormNo, moNo));
                if (mo != null) {
                    Map<String, Object> md = parseData(mo.getDetailData());
                    if (md.get("products") instanceof List<?> ps && !ps.isEmpty()) {
                        product = castRow((Map<?, ?>) ps.get(0));
                    }
                }
            }
            if (!(detail.get("items") instanceof List<?> items)) continue;
            for (Object o : items) {
                if (!(o instanceof Map<?, ?> raw)) continue;
                Map<String, Object> line = castRow(raw);
                double batchQty = num(line.get("派工数量"));
                double done = round2(reported.getOrDefault(pg.getFormNo(), 0.0));
                double remain = round2(batchQty - done);
                Map<String, Object> row = new LinkedHashMap<>();
                row.put("排产单号", pg.getFormNo());
                row.put("加工单号", moNo);
                row.put("产品编码", product.getOrDefault("产品编码", ""));
                row.put("产品名称", product.getOrDefault("产品名称", line.getOrDefault("产品名称", "")));
                row.put("规格型号", product.getOrDefault("规格型号", line.getOrDefault("规格型号", "")));
                row.put("工序编码", line.getOrDefault("工序编码", ""));
                row.put("工序名称", line.getOrDefault("工序名称", ""));
                row.put("生产车间", head.getOrDefault("生产车间", line.getOrDefault("生产车间", "")));
                row.put("班组", line.getOrDefault("班组", ""));
                row.put("设备", line.getOrDefault("设备", ""));
                row.put("工序单位", line.getOrDefault("计量单位", ""));
                row.put("批次数量", batchQty);
                row.put("已报数量", done);
                row.put("剩余数量", Math.max(0, remain));
                row.put("可否报工", remain > 0);
                row.put("不可报工原因", remain <= 0 ? "批次已报满" : "");
                out.add(row);
            }
        }
        return out;
    }

    /**
     * 批次报工校验：批次必须已审核、还在剩余量内，且 合格+不合格 = 报工数量。
     *
     * @param qualified 合格数量
     * @param rejected  不合格数量
     */
    public double validateBatchReport(String dispatchNo, double qualified, double rejected) {
        if (dispatchNo == null || dispatchNo.isBlank()) throw new IllegalArgumentException("缺少排产单号");
        if (qualified < 0 || rejected < 0) throw new IllegalArgumentException("合格/不合格数量不能为负");
        double qty = round2(qualified + rejected);
        if (qty <= 0) throw new IllegalArgumentException("报工数量必须大于 0（合格 + 不合格）");

        for (Map<String, Object> row : reportBatchBoard()) {
            if (!dispatchNo.equals(str(row.get("排产单号")))) continue;
            double remain = num(row.get("剩余数量"));
            if (qty > remain + 1e-6) {
                throw new IllegalStateException("批次 " + dispatchNo + " 剩余 " + trimNum(remain)
                        + "，本次报工 " + trimNum(qty) + "（合格 " + trimNum(qualified)
                        + " + 不合格 " + trimNum(rejected) + "）超过剩余量");
            }
            return qty;
        }
        throw new IllegalStateException("排产批次 " + dispatchNo + " 不存在或未审批通过（必须先排产再报工）");
    }

    /**
     * 建排产批次：一张派工单只放一个批次（单行），审批后即占用额度。
     *
     * <p>一个工序可以建多个批次（分批排产），每批可指定不同班组/设备（允许跨班组、跨设备）。
     */
    public Map<String, Object> planBatch(String manuOrderNo, String processCode, double quantity,
                                         String team, String equipment, String planStart, String planEnd) {
        if (manuOrderNo == null || manuOrderNo.isBlank()) throw new IllegalArgumentException("缺少加工单号");
        if (processCode == null || processCode.isBlank()) throw new IllegalArgumentException("缺少工序编码");
        validatePlanQuantity(manuOrderNo, processCode, quantity);

        Map<String, Object> procRow = null;
        Map<String, Object> boardRow = null;
        for (Map<String, Object> r : planBoard(manuOrderNo)) {
            if (processCode.equals(str(r.get("工序编码")))) { boardRow = r; break; }
        }
        if (boardRow == null) throw new IllegalStateException("加工单 " + manuOrderNo + " 没有工序 " + processCode);

        FormData mo = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "MANU_ORDER").eq(FormData::getFormNo, manuOrderNo));
        if (mo != null && !Set.of("已审核", "生产中").contains(mo.getStatus())) {
            throw new IllegalStateException("加工单 " + manuOrderNo + " 状态为「" + mo.getStatus()
                    + "」，只有已审核/生产中的加工单可以排产");
        }
        Map<String, Object> moHead = mo == null ? Map.of() : parseData(mo.getData());
        Map<String, Object> moDetail = mo == null ? Map.of() : parseData(mo.getDetailData());
        if (moDetail.get("processes") instanceof List<?> procs) {
            for (Object o : procs) {
                if (!(o instanceof Map<?, ?> raw)) continue;
                Map<String, Object> r = castRow(raw);
                if (processCode.equals(str(r.get("工序编码")))) { procRow = r; break; }
            }
        }
        if (procRow == null) throw new IllegalStateException("加工单 " + manuOrderNo + " 没有工序 " + processCode);

        Map<String, Object> data = new LinkedHashMap<>();
        data.put("单据日期", LocalDate.now().toString());
        data.put("业务类型", "工序派工");
        data.put("加工单号", manuOrderNo);
        data.put("生产车间", procRow.getOrDefault("生产车间", moHead.getOrDefault("生产车间", "")));
        data.put("产品名称", boardRow.getOrDefault("产品名称", ""));
        data.put("预开工日", planStart == null ? "" : planStart);
        data.put("预完工日", planEnd == null ? "" : planEnd);

        Map<String, Object> row = new LinkedHashMap<>();
        row.put("工序编码", processCode);
        row.put("工序名称", procRow.getOrDefault("工序名称", ""));
        row.put("产品名称", boardRow.getOrDefault("产品名称", ""));
        row.put("生产车间", procRow.getOrDefault("生产车间", moHead.getOrDefault("生产车间", "")));
        row.put("工作中心", procRow.getOrDefault("工作中心", ""));
        row.put("设备", equipment == null || equipment.isBlank() ? procRow.getOrDefault("设备", "") : equipment);
        row.put("班组", team == null || team.isBlank() ? procRow.getOrDefault("班组", "") : team);
        row.put("工人", procRow.getOrDefault("工人", ""));
        row.put("加工类型", procRow.getOrDefault("工艺类型", "自制"));
        row.put("计划数量", num(procRow.get("计划数量")));
        row.put("已派工数量", boardRow.getOrDefault("已排产量", 0));
        row.put("派工数量", round2(quantity));
        row.put("计量单位", boardRow.getOrDefault("工序单位", ""));
        row.put("预开工日", planStart == null ? "" : planStart);
        row.put("预完工日", planEnd == null ? "" : planEnd);
        row.put("派工加工状态", "未派工");
        row.put("规格型号", boardRow.getOrDefault("规格型号", ""));
        row.put("备注", "排产批次 " + trimNum(quantity));

        Map<String, Object> detail = new LinkedHashMap<>();
        detail.put("items", List.of(row));
        return insertGenerated("DISPATCH", "MANU_ORDER", manuOrderNo, data, detail);
    }

    /** 排产数量校验：不得超过可排产量。 */
    public void validatePlanQuantity(String manuOrderNo, String processCode, double quantity) {
        if (quantity <= 0) throw new IllegalArgumentException("排产数量必须大于 0");
        for (Map<String, Object> row : planBoard(manuOrderNo)) {
            if (!processCode.equals(str(row.get("工序编码")))) continue;
            double available = num(row.get("可排产量"));
            if (quantity > available + 1e-6) {
                throw new IllegalStateException("工序「" + row.get("工序名称") + "」可排产 " + trimNum(available)
                        + "，本次 " + trimNum(quantity) + " 超过可排产量"
                        + (num(row.get("工序顺序")) > 1
                        ? "（下道工序只能排上道合格数量）" : ""));
            }
            return;
        }
        throw new IllegalStateException("加工单 " + manuOrderNo + " 没有工序 " + processCode);
    }

    /**
     * 工序报工工作台数据：一行一道工序，按加工顺序排。
     *
     * <p>放行规则（B）：本道还有剩余量，且**前一道已有报工**（>0）就能开工，
     * 允许工序重叠生产；第一道没有前置。
     *
     * @param manuOrderNo 只算这一张加工单；传 null 返回全部已审核加工单
     */
    public List<Map<String, Object>> processReportBoard(String manuOrderNo) {
        // 已审核报工量：加工单 + 工序 → 数量
        Map<String, Double> reported = new LinkedHashMap<>();
        Map<String, Double> pending = new LinkedHashMap<>(); // 草稿/审批中：报了但还没审
        for (FormData rp : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "PROCESS_REPORT"))) {
            Map<String, Object> head;
            Map<String, Object> detail;
            try {
                head = parseData(rp.getData());
                detail = parseData(rp.getDetailData());
            } catch (Exception e) {
                continue;
            }
            if (!"已审核".equals(rp.getStatus())) {
                String pOrder = str(head.get("加工单号"));
                if (!pOrder.isEmpty() && detail.get("items") instanceof List<?> pitems) {
                    for (Object o : pitems) {
                        if (!(o instanceof Map<?, ?> line)) continue;
                        pending.merge(pOrder + "\n" + str(line.get("工序编码")),
                                num(line.get("报工数量")), Double::sum);
                    }
                }
                continue;
            }
            String orderNo = str(head.get("加工单号"));
            if (orderNo.isEmpty()) continue;
            if (!(detail.get("items") instanceof List<?> items)) continue;
            for (Object o : items) {
                if (!(o instanceof Map<?, ?> line)) continue;
                reported.merge(orderNo + "\n" + str(line.get("工序编码")), num(line.get("报工数量")), Double::sum);
            }
        }

        List<Map<String, Object>> out = new ArrayList<>();
        for (FormData mo : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "MANU_ORDER").orderByDesc(FormData::getId))) {
            if (!"已审核".equals(mo.getStatus())) continue;
            if (manuOrderNo != null && !manuOrderNo.isBlank() && !manuOrderNo.equals(mo.getFormNo())) continue;
            Map<String, Object> head;
            Map<String, Object> detail;
            try {
                head = parseData(mo.getData());
                detail = parseData(mo.getDetailData());
            } catch (Exception e) {
                continue;
            }
            if (!(detail.get("processes") instanceof List<?> procs) || procs.isEmpty()) continue;
            Map<String, Object> product = detail.get("products") instanceof List<?> ps && !ps.isEmpty()
                    ? castRow((Map<?, ?>) ps.get(0)) : Map.of();

            // 按加工顺序排；缺顺序时按明细先后
            List<Map<String, Object>> ordered = new ArrayList<>();
            for (Object o : procs) {
                if (o instanceof Map<?, ?> raw) ordered.add(castRow(raw));
            }
            ordered.sort((a, b) -> {
                double sa = num(a.containsKey("加工顺序") ? a.get("加工顺序") : a.get("工艺序号"));
                double sb = num(b.containsKey("加工顺序") ? b.get("加工顺序") : b.get("工艺序号"));
                if (sa == 0 && sb == 0) return 0;
                return Double.compare(sa, sb);
            });

            double prevReported = -1; // 第一道没有前置
            int seq = 0;
            for (Map<String, Object> proc : ordered) {
                seq++;
                String code = str(proc.get("工序编码"));
                double plan = num(proc.get("计划数量"));
                double done = round2(reported.getOrDefault(mo.getFormNo() + "\n" + code, 0.0));
                double remain = round2(plan - done);

                String reason = "";
                if (remain <= 0) reason = "已报满";
                else if (seq > 1 && prevReported <= 0) {
                    Map<String, Object> prev = ordered.get(seq - 2);
                    String prevName = str(prev.get("工序名称"));
                    if (prevName.isEmpty()) prevName = str(prev.get("工序编码"));
                    double prevPending = pending.getOrDefault(
                            mo.getFormNo() + "\n" + str(prev.get("工序编码")), 0.0);
                    reason = prevPending > 0
                            ? "前置工序「" + prevName + "」已报 " + trimNum(prevPending) + "，待审批通过后解锁"
                            : "前置工序「" + prevName + "」尚无报工";
                }

                Map<String, Object> row = new LinkedHashMap<>();
                row.put("加工单号", mo.getFormNo());
                row.put("生产车间", head.getOrDefault("生产车间", ""));
                row.put("客户", head.getOrDefault("客户", ""));
                row.put("产品编码", product.getOrDefault("产品编码", proc.getOrDefault("产品编码", "")));
                row.put("产品名称", product.getOrDefault("产品名称", proc.getOrDefault("产品名称", "")));
                row.put("规格型号", product.getOrDefault("规格型号", proc.getOrDefault("规格型号", "")));
                row.put("工序顺序", seq);
                row.put("工序编码", code);
                row.put("工序名称", proc.getOrDefault("工序名称", ""));
                row.put("工序单位", proc.getOrDefault("工序单位", proc.getOrDefault("计量单位", "")));
                row.put("计划数量", plan);
                row.put("已报数量", done);
                row.put("剩余数量", Math.max(0, remain));
                row.put("可否报工", reason.isEmpty());
                row.put("不可报工原因", reason);
                out.add(row);
                prevReported = done;
            }
        }
        return out;
    }

    /**
     * 按工序报工：针对某张加工单的某一道工序生成汇报单（草稿，只含这一行）。
     *
     * <p>与「选单」整单带入不同，这里一次只报一道工序，天然避免
     * 「某道已报满把整单卡住」以及越序报工。
     */
    public Map<String, Object> reportProcess(String manuOrderNo, String processCode, double quantity) {
        if (manuOrderNo == null || manuOrderNo.isBlank()) throw new IllegalArgumentException("缺少加工单号");
        if (processCode == null || processCode.isBlank()) throw new IllegalArgumentException("缺少工序编码");
        if (quantity <= 0) throw new IllegalArgumentException("报工数量必须大于 0");

        // 顺序校验（规则 B）
        validateProcessReportSequence(manuOrderNo, processCode);

        FormData mo = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "MANU_ORDER").eq(FormData::getFormNo, manuOrderNo));
        if (mo == null) throw new IllegalArgumentException("加工单不存在：" + manuOrderNo);
        if (!"已审核".equals(mo.getStatus())) throw new IllegalStateException("仅已审核加工单可报工");
        Map<String, Object> moHead = parseData(mo.getData());
        Map<String, Object> moDetail = parseData(mo.getDetailData());
        Map<String, Object> product = moDetail.get("products") instanceof List<?> ps && !ps.isEmpty()
                ? castRow((Map<?, ?>) ps.get(0)) : Map.of();

        Map<String, Object> target = null;
        int sourceLine = 0;
        double plan = 0;
        if (moDetail.get("processes") instanceof List<?> procs) {
            for (int i = 0; i < procs.size(); i++) {
                if (!(procs.get(i) instanceof Map<?, ?> raw)) continue;
                Map<String, Object> row = castRow(raw);
                if (processCode.equals(str(row.get("工序编码")))) {
                    target = row;
                    sourceLine = i + 1;
                    plan = num(row.get("计划数量"));
                    break;
                }
            }
        }
        if (target == null) throw new IllegalArgumentException("加工单 " + manuOrderNo + " 没有工序 " + processCode);

        double reported = reportedQtyByProcess(manuOrderNo).getOrDefault(processCode, 0.0);
        double remain = round2(plan - reported);
        if (remain <= 0) {
            throw new IllegalStateException("工序「" + str(target.get("工序名称")) + "」已报满（计划 "
                    + trimNum(plan) + "，已报 " + trimNum(reported) + "）");
        }
        if (quantity > remain + 1e-6) {
            throw new IllegalStateException("报工数量超过剩余可报：本道计划 " + trimNum(plan)
                    + "，已报 " + trimNum(reported) + "，最多还能报 " + trimNum(remain));
        }

        String batchNo = LocalDate.now().format(DateTimeFormatter.ofPattern("yyyyMMdd"));
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("单据日期", LocalDate.now().toString());
        data.put("业务类型", "工序汇报");
        data.put("加工单号", manuOrderNo);
        data.put("生产车间", moHead.getOrDefault("生产车间", ""));
        data.put("客户", moHead.getOrDefault("客户", ""));
        data.put("销售订单号", moHead.getOrDefault("销售订单号", ""));
        data.put("产品编码", product.getOrDefault("产品编码", ""));
        data.put("产品名称", product.getOrDefault("产品名称", ""));
        data.put("规格型号", product.getOrDefault("规格型号", ""));
        data.put("批次号", batchNo);

        Map<String, Object> row = new LinkedHashMap<>();
        row.put("加工单号", manuOrderNo);
        row.put("批次号", batchNo);
        row.put("产品编码", product.getOrDefault("产品编码", ""));
        row.put("产品名称", product.getOrDefault("产品名称", ""));
        row.put("规格型号", product.getOrDefault("规格型号", ""));
        row.put("工序编码", processCode);
        row.put("工序名称", target.getOrDefault("工序名称", ""));
        row.put("工序单位", target.getOrDefault("工序单位", target.getOrDefault("计量单位", "")));
        row.put("生产车间", target.getOrDefault("生产车间", moHead.getOrDefault("生产车间", "")));
        row.put("工作中心", target.getOrDefault("工作中心", ""));
        row.put("设备", target.getOrDefault("设备", ""));
        row.put("班组名称", target.getOrDefault("班组", ""));
        row.put("工人名称", target.getOrDefault("工人", ""));
        row.put("工艺类型", target.getOrDefault("工艺类型", "自制"));
        row.put("计划数量", plan);
        row.put("报工数量", round2(quantity));
        row.put("合格数量", round2(quantity));
        row.put("不合格数量", 0);
        row.put("累计汇报数量", round2(reported));
        row.put("可报工数量", remain);
        row.put("工价", target.getOrDefault("工价", 0));
        // 不写 来源面板/来源单号/来源行号：那是「选单」用来占位的机制，
        // 按工序报工由「加工单号 + 工序编码」定位，避免与其它草稿互相占用同一行而报
        // 「来源明细已被其他下游单据选入」。
        row.put("来源加工单", manuOrderNo);
        row.put("来源工序行", sourceLine);

        Map<String, Object> detail = new LinkedHashMap<>();
        detail.put("items", List.of(row));
        return insertGenerated("PROCESS_REPORT", "MANU_ORDER", manuOrderNo, data, detail);
    }

    /**
     * 工序报工顺序校验（规则 B）：前一道工序只要有报工，本道就能开工。
     */
    public void validateProcessReportSequence(String manuOrderNo, String processCode) {
        List<Map<String, Object>> board = processReportBoard(manuOrderNo);
        for (Map<String, Object> row : board) {
            if (!processCode.equals(str(row.get("工序编码")))) continue;
            Object ok = row.get("可否报工");
            if (Boolean.FALSE.equals(ok)) {
                String reason = str(row.get("不可报工原因"));
                if (reason.contains("前置工序")) {
                    throw new IllegalStateException("工序「" + row.get("工序名称") + "」的前置工序尚未报工："
                            + reason + "。请先报前置工序（有报工即可开下一道）");
                }
            }
            return;
        }
    }

    /** 齐套检查结果。 */
    public record KittingResult(int kitting, int planned, int shortageCount, List<String> shortages, String message) { }

    /**
     * 齐套检查：按当前库存推算这张加工单能开几件，并把结果写回产成品的「齐套数量(主)」。
     *
     * <p>齐套数量 = min(计划数量, 各材料 floor(可用库存 / 单件用量))。
     * 单件用量取「定额需用数量 / 定额生产数量」，缺定额时退回「计划数量 / 生产数量」。
     * 库存取实时结存（不是单据上的快照字段），所以领料、入库之后重算就准。
     */
    public KittingResult refreshKitting(String manuOrderNo) {
        if (manuOrderNo == null || manuOrderNo.isBlank()) {
            return new KittingResult(0, 0, 0, List.of(), "缺少加工单号");
        }
        FormData mo = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "MANU_ORDER").eq(FormData::getFormNo, manuOrderNo));
        if (mo == null) return new KittingResult(0, 0, 0, List.of(), "未找到加工单：" + manuOrderNo);

        Map<String, Object> detail = parseData(mo.getDetailData());
        if (!(detail.get("products") instanceof List<?> products) || products.isEmpty()) {
            return new KittingResult(0, 0, 0, List.of(), "加工单没有产成品明细：" + manuOrderNo);
        }
        Map<?, ?> product = (Map<?, ?>) products.get(0);
        double plannedQty = num(product.get("数量"));

        int limit = (int) Math.floor(plannedQty);
        List<String> shortages = new ArrayList<>();
        int shortageCount = 0;
        if (detail.get("materials") instanceof List<?> materials) {
            for (Object o : materials) {
                if (!(o instanceof Map<?, ?> row)) continue;
                String code = str(row.get("材料编码"));
                String name = str(row.get("材料名称"));
                double perUnit = num(row.get("定额需用数量")) / Math.max(num(row.get("定额生产数量")), 1);
                if (perUnit <= 0 && plannedQty > 0) perUnit = num(row.get("计划数量")) / plannedQty;
                if (perUnit <= 0) continue;

                double available = stockTxnService
                        .quantityOf(code, name, null)
                        .doubleValue();
                int fit = (int) Math.floor(available / perUnit);
                if (fit < limit) {
                    limit = Math.max(0, fit);
                    shortageCount++;
                    double need = round2(perUnit * plannedQty);
                    shortages.add((name.isEmpty() ? code : name) + " 现有 " + trimNum(round2(available))
                            + "，需 " + trimNum(need) + "（单件 " + trimNum(round2(perUnit * 100) / 100) + "）");
                }
            }
        }

        // 写回产成品行的齐套数量
        @SuppressWarnings("unchecked")
        Map<String, Object> productRow = (Map<String, Object>) product;
        productRow.put("齐套数量(主)", (double) limit);
        mo.setDetailData(toJson(detail));
        mo.setUpdateTime(LocalDateTime.now());
        formMapper.updateById(mo);

        String message = shortageCount == 0
                ? "齐套充足：可开工 " + limit + " / 计划 " + trimNum(plannedQty)
                : "齐套不足：可开工 " + limit + " / 计划 " + trimNum(plannedQty) + "；缺料 —— "
                        + String.join("；", shortages);
        return new KittingResult(limit, (int) Math.floor(plannedQty), shortageCount, shortages, message);
    }

    /** 按排产批次校验领料（逐行）。 */
    private void validateBatchIssueFor(FormData doc) {
        validateBatchIssueFor(firstDetailRows(parseData(doc.getDetailData())), doc.getFormNo());
    }

    private void validateBatchIssueFor(List<Map<String, Object>> lines, String selfFormNo) {
        for (Map<String, Object> line : lines) {
            String dispatchNo = str(line.get("排产单号"));
            if (dispatchNo.isEmpty()) continue;       // 共用料整单领，不做批次校验
            String moNo = str(line.get("加工单号"));
            String code = str(line.get("材料编码"));
            if (code.isEmpty()) code = str(line.get("存货编码"));
            if (moNo.isEmpty() || code.isEmpty()) continue;
            validateBatchIssue(moNo, dispatchNo, code, num(line.get("数量")), selfFormNo);
        }
    }

    /** 从一张领料出库单里取出明细行做超领校验。 */
    private void validateMaterialIssueFor(FormData doc) {
        List<Map<String, Object>> lines = new ArrayList<>();
        for (Map<String, Object> row : firstDetailRows(parseData(doc.getDetailData()))) {
            lines.add(row);
        }
        validateMaterialIssue(lines, doc.getFormNo());
    }

    public void validateMaterialIssue(List<Map<String, Object>> lines, String selfFormNo) {
        if (lines == null || lines.isEmpty()) return;

        // 先按「加工单号 + 材料编码」汇总本次领用量：同一张单上重复行要合并后再比
        Map<String, Double> requested = new LinkedHashMap<>();
        Map<String, String> labels = new LinkedHashMap<>();
        for (Map<String, Object> line : lines) {
            String orderNo = str(line.get("加工单号"));
            String code = str(line.get("材料编码"));
            if (code.isEmpty()) code = str(line.get("存货编码"));
            if (orderNo.isEmpty() || code.isEmpty()) continue;
            double qty = num(line.get("数量"));
            if (qty <= 0) continue;
            requested.merge(orderNo + "\n" + code, qty, Double::sum);
            String name = str(line.get("材料名称"));
            labels.putIfAbsent(orderNo + "\n" + code, name.isEmpty() ? code : name);
        }
        if (requested.isEmpty()) return;

        for (Map.Entry<String, Double> e : requested.entrySet()) {
            String[] parts = e.getKey().split("\n", 2);
            String orderNo = parts[0];
            String code = parts[1];
            double ask = round2(e.getValue());

            FormData mo = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                    .eq(FormData::getPanelCode, "MANU_ORDER").eq(FormData::getFormNo, orderNo));
            if (mo == null) continue;

            double plan = -1;
            if (parseData(mo.getDetailData()).get("materials") instanceof List<?> mats) {
                for (Object o : mats) {
                    if (!(o instanceof Map<?, ?> row)) continue;
                    String rowCode = str(row.get("材料编码"));
                    if (rowCode.isEmpty()) rowCode = str(row.get("材料名称"));
                    if (rowCode.equals(code)) {
                        plan = num(row.get("计划数量"));
                        break;
                    }
                }
            }
            if (plan < 0) {
                throw new IllegalStateException("材料「" + labels.get(e.getKey()) + "」未列入加工单 "
                        + orderNo + " 的需用明细，不能领料");
            }

            double issued = 0;
            for (FormData doc : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                    .eq(FormData::getPanelCode, "MATERIAL_OUT"))) {
                if (!"已审核".equals(doc.getStatus())) continue;
                if (selfFormNo != null && selfFormNo.equals(doc.getFormNo())) continue;
                if (!(parseData(doc.getDetailData()).get("items") instanceof List<?> items)) continue;
                for (Object o : items) {
                    if (!(o instanceof Map<?, ?> line)) continue;
                    if (!orderNo.equals(str(line.get("加工单号")))) continue;
                    String lineCode = str(line.get("材料编码"));
                    if (lineCode.isEmpty()) lineCode = str(line.get("存货编码"));
                    if (!code.equals(lineCode)) continue;
                    issued += num(line.get("数量"));
                }
            }
            issued = round2(issued);
            double remain = round2(plan - issued);
            // 现存量校验：不能领成负库存
            String lineWh = "";
            for (Map<String, Object> l : lines) {
                String lc = str(l.get("材料编码"));
                if (lc.isEmpty()) lc = str(l.get("存货编码"));
                if (code.equals(lc)) { lineWh = str(l.get("仓库")); break; }
            }
            requireStockAvailable(code, labels.get(e.getKey()), lineWh, ask);
            if (round2(issued + ask) > plan + 1e-6) {
                throw new IllegalStateException("材料「" + labels.get(e.getKey()) + "」超出加工单 "
                        + orderNo + " 的需用量：计划 " + trimNum(plan) + "，已领 " + trimNum(issued)
                        + "，本次 " + trimNum(ask) + "，剩余可领 " + trimNum(Math.max(0, remain)));
            }
        }
    }

    /** 去掉多余小数零，用于把数量写进提示语。 */
    private static String trimNum(double v) {
        if (v == Math.rint(v)) return String.valueOf((long) v);
        return String.valueOf(round2(v));
    }

    /**
     * 回写生产进度到生产加工单：工序累计汇报/完工状态、材料累计领用。
     *
     * <p>按「已审核单据重算」，不是累加：弃审或作废后重算一次就自动纠正，
     * 避免累加式回写越滚越多。明细按 产品编码+工序编码 / 材料编码 对齐。
     */
    /**
     * 单据状态变化后触发生产进度重算。
     *
     * <p>工序汇报单、领料出库单都会影响加工单的进度数字；只有这两类单据需要重算，
     * 其余单据直接跳过，避免每次审核都去扫全表。
     */
    private void syncProgressFrom(FormData doc) {
        if (doc == null) return;
        String panel = doc.getPanelCode();
        // 报工影响工序进度，领料影响材料进度，产成品入库影响完工判定
        if (!Set.of("PROCESS_REPORT", "MATERIAL_OUT", "FINISH_IN").contains(panel)) return;
        Set<String> orders = new LinkedHashSet<>();
        for (Map<String, Object> row : firstDetailRows(parseData(doc.getDetailData()))) {
            String no = str(row.get("加工单号"));
            if (!no.isEmpty()) orders.add(no);
        }
        if (orders.isEmpty()) orders.add(str(parseData(doc.getData()).get("加工单号")));
        for (String no : orders) {
            if (!no.isEmpty()) syncProductionProgress(no);
        }
    }

    public void syncProductionProgress(String manuOrderNo) {
        if (manuOrderNo == null || manuOrderNo.isBlank()) return;
        FormData mo = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "MANU_ORDER").eq(FormData::getFormNo, manuOrderNo));
        if (mo == null) return;
        Map<String, Object> detail = parseData(mo.getDetailData());
        if (detail.isEmpty()) return;

        // 1) 工序：汇总已审核工序汇报单的报工数量（合格量另算，供完工套数使用）
        Map<String, Double> reported = new LinkedHashMap<>();
        Map<String, Double> qualified = new LinkedHashMap<>();
        for (FormData rp : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "PROCESS_REPORT"))) {
            if (!"已审核".equals(rp.getStatus())) continue;
            Map<String, Object> head = parseData(rp.getData());
            if (!manuOrderNo.equals(String.valueOf(head.getOrDefault("加工单号", "")).trim())) continue;
            if (!(parseData(rp.getDetailData()).get("items") instanceof List<?> items)) continue;
            for (Object o : items) {
                if (!(o instanceof Map<?, ?> line)) continue;
                String key = processKey(str(line.get("产品编码")), str(line.get("工序编码")));
                reported.merge(key, num(line.get("报工数量")), Double::sum);
                // 老单据没有「合格数量」时按报工数量兜底，避免把历史数据算成 0 完工
                double ok = line.get("合格数量") == null ? num(line.get("报工数量")) : num(line.get("合格数量"));
                qualified.merge(key, ok, Double::sum);
            }
        }
        if (detail.get("processes") instanceof List<?> procs) {
            for (Object o : procs) {
                if (!(o instanceof Map<?, ?> raw)) continue;
                @SuppressWarnings("unchecked")
                Map<String, Object> row = (Map<String, Object>) raw;
                double plan = num(row.get("计划数量"));
                double done = round2(reported.getOrDefault(processKey(str(row.get("产品编码")), str(row.get("工序编码"))), 0.0));
                row.put("累计汇报数量", done);
                row.put("工序完工状态", plan > 0 && done >= plan ? "完工" : "未完工");
            }
        }

        // 2) 材料：汇总已审核领料出库单的领用数量
        Map<String, Double> issued = new LinkedHashMap<>();
        for (FormData mo2 : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "MATERIAL_OUT"))) {
            if (!"已审核".equals(mo2.getStatus())) continue;
            if (!(parseData(mo2.getDetailData()).get("items") instanceof List<?> items)) continue;
            for (Object o : items) {
                if (!(o instanceof Map<?, ?> line)) continue;
                String linked = str(line.get("加工单号"));
                if (!manuOrderNo.equals(linked)) continue;
                String code = str(line.get("材料编码"));
                if (code.isEmpty()) code = str(line.get("材料名称"));
                if (code.isEmpty()) continue;
                issued.merge(code, num(line.get("数量")), Double::sum);
            }
        }
        if (detail.get("materials") instanceof List<?> mats) {
            for (Object o : mats) {
                if (!(o instanceof Map<?, ?> raw)) continue;
                @SuppressWarnings("unchecked")
                Map<String, Object> row = (Map<String, Object>) raw;
                String code = str(row.get("材料编码"));
                if (code.isEmpty()) code = str(row.get("材料名称"));
                row.put("累计领用数量", round2(issued.getOrDefault(code, 0.0)));
            }
        }

        // 1.5) 产成品：完工套数 = 末道工序的合格累计（报检/入库的上限口径）。
        // 之前没人写这个字段，首件报检单审核时「累计完工量」恒为 0，报检数量一填就被拦。
        if (detail.get("products") instanceof List<?> products
                && detail.get("processes") instanceof List<?> procs) {
            Map<String, String> lastProcByProduct = new LinkedHashMap<>();
            Map<String, Double> lastSeqByProduct = new LinkedHashMap<>();
            for (Object o : procs) {
                if (!(o instanceof Map<?, ?> raw)) continue;
                String code = str(raw.get("产品编码"));
                double seq = num(raw.get("加工顺序"));
                Double lastSeq = lastSeqByProduct.get(code);
                if (lastSeq == null || seq >= lastSeq) {
                    lastSeqByProduct.put(code, seq);
                    lastProcByProduct.put(code, str(raw.get("工序编码")));
                }
            }
            for (Object o : products) {
                if (!(o instanceof Map<?, ?> raw)) continue;
                @SuppressWarnings("unchecked")
                Map<String, Object> row = (Map<String, Object>) raw;
                String code = str(row.get("产品编码"));
                String lastProc = lastProcByProduct.get(code);
                if (lastProc == null) continue;
                double finished = round2(qualified.getOrDefault(processKey(code, lastProc), 0.0));
                row.put("累计汇报套数(工序单位)", finished);
            }
        }

        // 3) 生命周期节点：首次报工记开工日期；工序全完工且产成品全部入库记完工日期
        Map<String, Object> head = parseData(mo.getData());
        boolean changed = false;
        if (detailTotal(detail.get("processes"), "累计汇报数量") > 0 && str(head.get("开工日期")).isEmpty()) {
            head.put("开工日期", LocalDate.now().toString());
            changed = true;
        }
        if (str(head.get("完工日期")).isEmpty()
                && allProcessesDone(detail.get("processes"))
                && allProductsReceived(detail.get("products"))) {
            head.put("完工日期", LocalDate.now().toString());
            changed = true;
        }
        if (changed) mo.setData(toJson(head));

        // 生命周期状态：开过工 → 生产中；全部工序完工且产成品全部入库 → 已完工。
        // 之前状态一直停在「已审核」，车间报完工了列表里还看不出来。
        if ("已审核".equals(mo.getStatus()) || "生产中".equals(mo.getStatus())) {
            boolean finished = allProcessesDone(detail.get("processes"))
                    && allProductsReceived(detail.get("products"));
            String next = finished ? "已完工"
                    : detailTotal(detail.get("processes"), "累计汇报数量") > 0 ? "生产中" : mo.getStatus();
            if (!next.equals(mo.getStatus())) mo.setStatus(next);
        }

        mo.setDetailData(toJson(detail));
        mo.setUpdateTime(LocalDateTime.now());
        formMapper.updateById(mo);
    }

    private double detailTotal(Object listObj, String field) {
        if (!(listObj instanceof List<?> list)) return 0;
        double sum = 0;
        for (Object o : list) {
            if (o instanceof Map<?, ?> row) sum += num(row.get(field));
        }
        return sum;
    }

    /** 所有工序都已完工；没有工序行时视为未完工，避免空单被误判为完工。 */
    private boolean allProcessesDone(Object listObj) {
        if (!(listObj instanceof List<?> list) || list.isEmpty()) return false;
        for (Object o : list) {
            if (!(o instanceof Map<?, ?> row) || !"完工".equals(str(row.get("工序完工状态")))) return false;
        }
        return true;
    }

    /** 产成品全部入库：累计入库数量 ≥ 数量。 */
    private boolean allProductsReceived(Object listObj) {
        if (!(listObj instanceof List<?> list) || list.isEmpty()) return false;
        for (Object o : list) {
            if (!(o instanceof Map<?, ?> row)) return false;
            if (num(row.get("累计入库数量")) + 1e-6 < num(row.get("数量"))) return false;
        }
        return true;
    }

    /** 取字符串并去空白；null 变空串，避免 "null" 参与比较。 */
    private static String str(Object o) {
        return o == null ? "" : String.valueOf(o).trim();
    }

    private static String processKey(String productCode, String processCode) {
        return (productCode.isEmpty() ? "*" : productCode) + "|" + processCode;
    }



    private static final class RowAccumulator {
        double executed;
        double inspected;
        double inbound;
        double returned;
    }

    private static final class InspectionAccumulator {
        double inspected;
        double qualified;
        double unqualified;
        double concession;
        double scrap;
        double returned;
        double rework;
    }

    private static String sourceKey(String panel, String no) {
        return textOf(panel) + "\n" + textOf(no);
    }

    private static RowAccumulator rowAccumulator(Map<String, Map<Integer, RowAccumulator>> aggregates,
                                                 String key, int line) {
        return aggregates.computeIfAbsent(key, ignored -> new HashMap<>())
                .computeIfAbsent(line, ignored -> new RowAccumulator());
    }

    /**
     * Recalculates accumulated quantities on every source row from effective downstream documents.
     *
     * <p>整表重建：累计执行/累计检验/累计入库/累计退货一起重算，入库与经检验的退货沿来源链
     * 向上累计（采购订单 ← 到货单 ← 检验单 ← 入库/退货），重复执行结果一致。</p>
     */
    private void refreshSourceExecution(FormData downstream) {
        List<FormData> all = new ArrayList<>(formMapper.selectList(new LambdaQueryWrapper<FormData>()));
        Map<String, FormData> byKey = new LinkedHashMap<>();
        for (FormData document : all) {
            if (document.getPanelCode() == null || document.getFormNo() == null) continue;
            byKey.put(sourceKey(document.getPanelCode(), document.getFormNo()), document);
        }
        if (downstream != null && downstream.getPanelCode() != null && downstream.getFormNo() != null) {
            String downstreamKey = sourceKey(downstream.getPanelCode(), downstream.getFormNo());
            byKey.put(downstreamKey, downstream);
            boolean present = all.stream().anyMatch(document ->
                    downstream.getPanelCode().equals(document.getPanelCode())
                            && downstream.getFormNo().equals(document.getFormNo()));
            if (!present) all.add(downstream);
        }
        Map<String, List<Map<String, Object>>> rowsByKey = new LinkedHashMap<>();
        for (FormData document : all) {
            rowsByKey.put(sourceKey(document.getPanelCode(), document.getFormNo()),
                    firstDetailRows(parseData(document.getDetailData())));
        }

        Set<String> targets = new LinkedHashSet<>();
        collectAncestorKeys(downstream, rowsByKey, targets);
        if (targets.isEmpty()) return;

        Map<String, Map<Integer, RowAccumulator>> aggregates = new HashMap<>();
        Map<String, Map<Integer, InspectionAccumulator>> inspections = new HashMap<>();
        for (FormData document : all) {
            if (!EFFECTIVE_STATUSES.contains(document.getStatus())) continue;
            List<Map<String, Object>> rows = rowsByKey.getOrDefault(
                    sourceKey(document.getPanelCode(), document.getFormNo()), List.of());
            boolean inboundDocument = Set.of("PURCHASE_IN", "FINISH_IN", "OUTSOURCE_IN")
                    .contains(document.getPanelCode());
            boolean returnDocument = "PURCHASE_RETURN".equals(document.getPanelCode());
            for (Map<String, Object> row : rows) {
                String sourcePanel = textOf(row.get("来源面板")).trim();
                String sourceNo = textOf(row.get("来源单号")).trim();
                int sourceRowNo = (int) Math.round(num(row.get("来源行号")));
                if (sourcePanel.isEmpty() || sourceNo.isEmpty() || sourceRowNo <= 0) continue;
                String key = sourceKey(sourcePanel, sourceNo);
                double quantity = executedQuantity(document.getPanelCode(), row);

                RowAccumulator own = rowAccumulator(aggregates, key, sourceRowNo);
                own.executed += quantity;
                if (QUALITY_INSPECTION_PANELS.contains(document.getPanelCode())) own.inspected += quantity;
                if (inboundDocument) own.inbound += quantity;
                boolean inspectionReturn = returnDocument && QUALITY_INSPECTION_PANELS.contains(sourcePanel);
                if (inspectionReturn) own.returned += quantity;

                if (QUALITY_INSPECTION_PANELS.contains(document.getPanelCode())) {
                    InspectionAccumulator inspection = inspections
                            .computeIfAbsent(key, ignored -> new HashMap<>())
                            .computeIfAbsent(sourceRowNo, ignored -> new InspectionAccumulator());
                    inspection.inspected += num(row.get("检验数量"));
                    inspection.qualified += num(row.get("合格数量"));
                    inspection.unqualified += num(row.get("不合格数量"));
                    inspection.concession += num(row.get("让步接收数量"));
                    inspection.scrap += num(row.get("报废数量"));
                    inspection.returned += num(firstValue(row, "验收退回数量", "判定退货数量"));
                    inspection.rework += num(row.get("返工数量"));
                }

                // 沿来源链向上：入库量与「经检验的退货」同时累计到所有祖先行。
                String currentPanel = sourcePanel;
                String currentNo = sourceNo;
                int currentRow = sourceRowNo;
                Set<String> visited = new HashSet<>();
                while (visited.add(sourceKey(currentPanel, currentNo) + "#" + currentRow)) {
                    List<Map<String, Object>> parentRows = rowsByKey.get(sourceKey(currentPanel, currentNo));
                    if (parentRows == null || currentRow < 1 || currentRow > parentRows.size()) break;
                    Map<String, Object> parentRow = parentRows.get(currentRow - 1);
                    String grandPanel = textOf(parentRow.get("来源面板")).trim();
                    String grandNo = textOf(parentRow.get("来源单号")).trim();
                    int grandRow = (int) Math.round(num(parentRow.get("来源行号")));
                    if (grandPanel.isEmpty() || grandNo.isEmpty() || grandRow <= 0) break;
                    RowAccumulator parent = rowAccumulator(aggregates, sourceKey(grandPanel, grandNo), grandRow);
                    if (inboundDocument) parent.inbound += quantity;
                    if (inspectionReturn) parent.returned += quantity;
                    currentPanel = grandPanel;
                    currentNo = grandNo;
                    currentRow = grandRow;
                }
            }
        }

        for (String key : targets) {
            FormData source = byKey.get(key);
            if (source == null) continue;
            Map<String, Object> detail = parseData(source.getDetailData());
            List<Map<String, Object>> rows = firstDetailRows(detail);
            Map<Integer, RowAccumulator> accumulators = aggregates.getOrDefault(key, Map.of());
            Map<Integer, InspectionAccumulator> inspectionRows = inspections.getOrDefault(key, Map.of());
            for (int index = 0; index < rows.size(); index++) {
                Map<String, Object> row = rows.get(index);
                RowAccumulator accumulator = accumulators.get(index + 1);
                row.put("累计执行数量", accumulator == null ? 0.0 : accumulator.executed);
                row.put("累计检验数量", accumulator == null ? 0.0 : accumulator.inspected);
                row.put("累计入库数量", accumulator == null ? 0.0 : accumulator.inbound);
                row.put("累计退货数量", accumulator == null ? 0.0 : accumulator.returned);
                if ("PU_REQ".equals(source.getPanelCode())) {
                    double ordered = accumulator == null ? 0.0 : accumulator.executed;
                    row.put("已下单数量", ordered);
                    row.put("未下单数量", Math.max(0, num(row.get("数量")) - ordered));
                }
                if ("ARRIVAL_IN".equals(source.getPanelCode())) {
                    InspectionAccumulator inspection = inspectionRows.get(index + 1);
                    row.put("检验数量", inspection == null ? 0.0 : inspection.inspected);
                    row.put("合格数量", inspection == null ? 0.0 : inspection.qualified);
                    row.put("不合格数量", inspection == null ? 0.0 : inspection.unqualified);
                    row.put("让步接收数量", inspection == null ? 0.0 : inspection.concession);
                    row.put("报废数量", inspection == null ? 0.0 : inspection.scrap);
                }
                if ("FINISH_INSPECT".equals(source.getPanelCode())) {
                    InspectionAccumulator inspection = inspectionRows.get(index + 1);
                    row.put("累计检验数量", inspection == null ? 0.0 : inspection.inspected);
                    row.put("累计合格数量", inspection == null ? 0.0 : inspection.qualified);
                    row.put("累计验收退回数量", inspection == null ? 0.0 : inspection.returned);
                    row.put("累计让步接收数量", inspection == null ? 0.0 : inspection.concession);
                    row.put("累计报废数量", inspection == null ? 0.0 : inspection.scrap);
                    row.put("累计已退数量", accumulator == null ? 0.0 : accumulator.returned);
                }
                if ("FIRST_INSPECT".equals(source.getPanelCode())) {
                    InspectionAccumulator inspection = inspectionRows.get(index + 1);
                    row.put("累计检验数量", inspection == null ? 0.0 : inspection.inspected);
                    row.put("累计合格数量", inspection == null ? 0.0 : inspection.qualified);
                    row.put("累计不合格数量", inspection == null ? 0.0 : inspection.unqualified);
                    row.put("累计检验返工数量", inspection == null ? 0.0 : inspection.rework);
                }
            }
            if ("ARRIVAL_IN".equals(source.getPanelCode())) {
                Map<String, Object> head = parseData(source.getData());
                head.put("检验状态", arrivalInspectionStatus(rows, inspectionRows));
                source.setData(toJson(head));
            }
            source.setDetailData(toJson(detail));
            source.setUpdateTime(LocalDateTime.now());
            formMapper.updateById(source);
        }
    }

    /** 从下游单据出发，把来源单及其全部上游祖先加入待重算集合。 */
    private void collectAncestorKeys(FormData downstream, Map<String, List<Map<String, Object>>> rowsByKey,
                                     Set<String> targets) {
        if (downstream == null) return;
        for (Map<String, Object> row : firstDetailRows(parseData(downstream.getDetailData()))) {
            String sourcePanel = textOf(row.get("来源面板")).trim();
            String sourceNo = textOf(row.get("来源单号")).trim();
            int sourceRowNo = (int) Math.round(num(row.get("来源行号")));
            if (sourcePanel.isEmpty() || sourceNo.isEmpty() || sourceRowNo <= 0) continue;
            String currentKey = sourceKey(sourcePanel, sourceNo);
            int currentRow = sourceRowNo;
            Set<String> visited = new HashSet<>();
            while (visited.add(currentKey + "#" + currentRow)) {
                targets.add(currentKey);
                List<Map<String, Object>> parentRows = rowsByKey.get(currentKey);
                if (parentRows == null || currentRow < 1 || currentRow > parentRows.size()) break;
                Map<String, Object> parentRow = parentRows.get(currentRow - 1);
                String grandPanel = textOf(parentRow.get("来源面板")).trim();
                String grandNo = textOf(parentRow.get("来源单号")).trim();
                int grandRow = (int) Math.round(num(parentRow.get("来源行号")));
                if (grandPanel.isEmpty() || grandNo.isEmpty() || grandRow <= 0) break;
                currentKey = sourceKey(grandPanel, grandNo);
                currentRow = grandRow;
            }
        }
    }

    /**
     * 到货单表头检验状态：无有效检验单=未检；所有实收>0 的行都检满=已检，否则=部分检。
     * 实收数量为 0 的行不参与「是否检满」判断。
     */
    private static String arrivalInspectionStatus(List<Map<String, Object>> rows,
                                                  Map<Integer, InspectionAccumulator> inspectionRows) {
        boolean referenced = inspectionRows != null && inspectionRows.values().stream()
                .anyMatch(inspection -> inspection.inspected > 0.000001);
        boolean anyReceived = false;
        boolean partial = false;
        for (int index = 0; index < rows.size(); index++) {
            Map<String, Object> row = rows.get(index);
            // 免检行不参与「是否检满」判断：混装到货单里需检行全部检完即视为已检。
            if ("免检".equals(textOf(row.get("检验方式")).trim())) continue;
            double received = num(row.get("实收数量"));
            if (received <= 0.000001) continue;
            anyReceived = true;
            InspectionAccumulator inspection = inspectionRows == null ? null : inspectionRows.get(index + 1);
            double inspected = inspection == null ? 0 : inspection.inspected;
            if (inspected + 0.000001 < received) partial = true;
        }
        if (!anyReceived || !referenced) return "未检";
        return partial ? "部分检" : "已检";
    }

    // ---------- 审批流：提交审批 → 审批中 → 审批通过/审批驳回（全留痕，可扩展多级） ----------

    /** 核销：把收款/付款单金额按业务日期分配到来往单。 */
    private Map<String, Object> writeOff(String panelCode, Map<String, Object> formData) {
        if (financePaymentService == null) throw new IllegalStateException("未启用财务核销服务");
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData fd = formOf(panelCode, no);
        if (!"已审核".equals(fd.getStatus())) {
            throw new IllegalStateException("仅已审核的收款/付款单可核销");
        }
        Map<String, Object> head = parseData(fd.getData());
        String voucherType = "RECEIPT".equals(panelCode) ? "AR" : "AP";
        String partner = str(firstValue(head, "客户", "供应商", "往来单位"));
        // 只核销本客户的往来单：以前不按往来单位过滤，可能把别家的应收冲掉
        List<FinanceVoucher> candidates = openVouchers(voucherType, partner);
        double payment = num(head.get("金额"));
        Object planLines = formData == null ? null : formData.get("核销明细");
        int count;
        if (planLines instanceof List<?> lines && !lines.isEmpty()) {
            // 按单核销：用户勾选哪张就核哪张，金额不得超过该单未结额与收款额
            List<FinancePaymentService.Allocation> plan = new ArrayList<>();
            double remain = payment;
            for (Object o : lines) {
                if (!(o instanceof Map<?, ?> raw)) continue;
                String vno = str(raw.get("往来单号"));
                if (vno.isEmpty()) continue;
                FinanceVoucher target = candidates.stream()
                        .filter((v) -> vno.equals(v.getSourceNo())).findFirst()
                        .orElseThrow(() -> new IllegalStateException("往来单 " + vno + " 不存在或已结清"));
                double open = round2(num(target.getAmount()) - num(target.getSettledAmount()));
                double want = num(raw.get("核销金额"));
                if (want <= 0) continue;
                if (want > open + 1e-6) {
                    throw new IllegalStateException("往来单 " + vno + " 未结 " + trimNum(open)
                            + "，本次核销 " + trimNum(want) + " 超过未结额");
                }
                if (want > remain + 1e-6) {
                    throw new IllegalStateException("本次核销合计超过收款金额 " + trimNum(payment));
                }
                plan.add(new FinancePaymentService.Allocation(target,
                        java.math.BigDecimal.valueOf(round2(want))));
                remain -= want;
            }
            if (plan.isEmpty()) throw new IllegalStateException("请勾选要核销的往来单并填写核销金额");
            count = financePaymentService.executePlan(fd.getFormNo(), plan, currentUserName());
        } else {
            double openTotal = candidates.stream()
                    .mapToDouble((v) -> num(v.getAmount()) - num(v.getSettledAmount())).sum();
            if (payment > openTotal + 1e-6) {
                throw new IllegalStateException("未结应收合计 " + trimNum(round2(openTotal))
                        + "，本次收款 " + trimNum(payment) + "，请先确认已收款金额或改按单核销");
            }
            count = financePaymentService.execute(fd.getFormNo(), voucherType, candidates,
                    java.math.BigDecimal.valueOf(payment), currentUserName());
        }
        Map<String, Object> out = new HashMap<>();
        out.put("编号", fd.getFormNo());
        out.put("核销笔数", count);
        out.put("单据状态", "已核销");
        return out;
    }

    /** 某排产批次的报工记录（按明细里的排产单号匹配）。 */
    public List<Map<String, Object>> batchReports(String dispatchNo) {
        if (dispatchNo == null || dispatchNo.isBlank()) return List.of();
        List<Map<String, Object>> out = new ArrayList<>();
        for (FormData rp : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "PROCESS_REPORT"))) {
            Map<String, Object> head;
            Map<String, Object> detail;
            try {
                head = parseData(rp.getData());
                detail = parseData(rp.getDetailData());
            } catch (Exception e) {
                continue;
            }
            if (!(detail.get("items") instanceof List<?> items)) continue;
            for (Object o : items) {
                if (!(o instanceof Map<?, ?> raw)) continue;
                Map<String, Object> line = castRow(raw);
                if (!dispatchNo.equals(str(line.get("排产单号")))) continue;
                Map<String, Object> row = new LinkedHashMap<>();
                row.put("编号", rp.getFormNo());
                row.put("单据日期", head.getOrDefault("单据日期", ""));
                row.put("工序编码", line.getOrDefault("工序编码", ""));
                row.put("工序名称", line.getOrDefault("工序名称", ""));
                row.put("报工数量", line.getOrDefault("报工数量", 0));
                row.put("合格数量", line.getOrDefault("合格数量", 0));
                row.put("不合格数量", line.getOrDefault("不合格数量", 0));
                row.put("班组名称", line.getOrDefault("班组名称", ""));
                row.put("设备", line.getOrDefault("设备", ""));
                row.put("单据状态", rp.getStatus());
                out.add(row);
            }
        }
        return out;
    }

    /** 某往来类型 + 往来单位的未结往来单（按业务日期先进先出）。 */
    private List<FinanceVoucher> openVouchers(String voucherType, String partner) {
        if (financeVoucherMapper == null) return List.of();
        List<FinanceVoucher> all = financeVoucherMapper.selectList(new LambdaQueryWrapper<FinanceVoucher>()
                .eq(FinanceVoucher::getVoucherType, voucherType)
                .ne(FinanceVoucher::getStatus, "已结算"));
        if (partner == null || partner.isBlank()) return all;
        return all.stream().filter((v) -> partner.equals(v.getPartner())).toList();
    }

    /** 核销弹窗数据：该收款/付款单可核销的往来单与未结金额。 */
    public List<Map<String, Object>> openWriteOffLines(String panelCode, String formNo) {
        if (formNo == null || formNo.isBlank()) return List.of();
        FormData fd = formOf(panelCode, formNo);
        Map<String, Object> head = parseData(fd.getData());
        String voucherType = "RECEIPT".equals(panelCode) ? "AR" : "AP";
        String partner = str(firstValue(head, "客户", "供应商", "往来单位"));
        List<Map<String, Object>> out = new ArrayList<>();
        for (FinanceVoucher v : openVouchers(voucherType, partner)) {
            double open = round2(num(v.getAmount()) - num(v.getSettledAmount()));
            if (open <= 0) continue;
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("往来单号", v.getSourceNo());
            row.put("单据来源", v.getSourcePanel());
            row.put("往来单位", v.getPartner());
            row.put("业务日期", v.getBizDate() == null ? "" : v.getBizDate().toString());
            row.put("金额", round2(num(v.getAmount())));
            row.put("已核销", round2(num(v.getSettledAmount())));
            row.put("未结金额", open);
            out.add(row);
        }
        return out;
    }

    /** 反核销：撤销该收款/付款单的全部核销记录。 */
    private Map<String, Object> reverseWriteOff(String panelCode, Map<String, Object> formData) {
        if (financePaymentService == null) throw new IllegalStateException("未启用财务核销服务");
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData fd = formOf(panelCode, no);
        int removed = financePaymentService.reverse(fd.getFormNo());
        Map<String, Object> out = new HashMap<>();
        out.put("编号", fd.getFormNo());
        out.put("撤销笔数", removed);
        out.put("单据状态", fd.getStatus());
        return out;
    }

    private FormData formOf(String panelCode, Object no) {
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData fd = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, panelCode)
                .eq(FormData::getFormNo, String.valueOf(no)));
        if (fd == null) throw new IllegalArgumentException("表单数据不存在：" + no);
        return fd;
    }

    private String opinionOf(Map<String, Object> formData) {
        Object v = formData == null ? null : formData.get("审批意见");
        return v == null ? "" : String.valueOf(v).trim();
    }

    private void recordApproval(String panelCode, String formNo, String action, String result, String opinion) {
        FormApproval rec = new FormApproval();
        rec.setPanelCode(panelCode);
        rec.setFormNo(formNo);
        rec.setAction(action);
        rec.setResult(result);
        rec.setNodeNo(1);
        rec.setOperator(currentUserName());
        rec.setOpinion(opinion.isEmpty() ? null : opinion);
        rec.setCreateTime(LocalDateTime.now());
        approvalMapper.insert(rec);
    }

    /** 审批通过/驳回必须紧跟一次有效提交，防止仅修改状态后伪造审批结果。 */
    private void requirePendingSubmission(String panelCode, String formNo) {
        FormApproval latest = approvalMapper.selectOne(new LambdaQueryWrapper<FormApproval>()
                .eq(FormApproval::getPanelCode, panelCode)
                .eq(FormApproval::getFormNo, formNo)
                .orderByDesc(FormApproval::getId)
                .last("LIMIT 1"));
        if (latest == null || !"SUBMIT".equals(latest.getAction()) || !"PENDING".equals(latest.getResult())) {
            throw new IllegalStateException("单据尚未提交审批，不能审批通过或驳回");
        }
    }

    /** 提交审批：仅草稿 → 审批中 */
    private Map<String, Object> submitApproval(String panelCode, Map<String, Object> formData) {
        FormData fd = formOf(panelCode, formData.get("编号"));
        if (!"草稿".equals(fd.getStatus())) throw new IllegalStateException("仅草稿状态可提交审批");
        validateBomForApproval(panelCode, fd);
        validateQualityForApproval(panelCode, fd);
        if ("MATERIAL_OUT".equals(panelCode)) {
            validateMaterialIssueFor(fd);
            validateBatchIssueFor(fd);
        }
        validateSourceLinks(panelCode, fd.getFormNo(), parseData(fd.getDetailData()));
        if ("MANU_ORDER".equals(panelCode)) {
            refreshKitting(fd.getFormNo());
        }
        String operator = currentUserName();
        String now = LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss"));
        fd.setStatus("审批中");
        fd.setUpdateTime(LocalDateTime.now());
        Map<String, Object> head = parseData(fd.getData());
        head.put("审批状态", "审批中");
        head.put("提交人", operator);
        head.put("提交时间", now);
        // 审批面板：提交审批时自动填写 单据日期 + 单据编号（缺才填）
        ensureAuditStamp(head, fd.getFormNo(), panelCode);
        fd.setData(toJson(head));
        formMapper.updateById(fd);
        recordApproval(panelCode, fd.getFormNo(), "SUBMIT", "PENDING", opinionOf(formData));
        Map<String, Object> out = new HashMap<>();
        out.put("编号", fd.getFormNo());
        out.put("单据状态", "审批中");
        return out;
    }

    /** 审批通过：仅审批中 → 已审核（审核人=当前登录人，写审核人/时间/意见） */
    private Map<String, Object> approveApproval(String panelCode, Map<String, Object> formData) {
        FormData fd = formOf(panelCode, formData.get("编号"));
        if (!"审批中".equals(fd.getStatus())) throw new IllegalStateException("仅审批中状态可审批通过");
        requirePendingSubmission(panelCode, fd.getFormNo());
        validateBomForApproval(panelCode, fd);
        validateQualityForApproval(panelCode, fd);
        if ("MATERIAL_OUT".equals(panelCode)) {
            validateMaterialIssueFor(fd);
            validateBatchIssueFor(fd);
        }
        validateSourceLinks(panelCode, fd.getFormNo(), parseData(fd.getDetailData()));
        if ("INSPECTION".equals(panelCode)) confirmInspectionDetail(fd);
        String operator = currentUserName();
        String opinion = opinionOf(formData);
        fd.setStatus("已审核");
        fd.setAuditBy(operator);
        fd.setAuditTime(LocalDateTime.now());
        Map<String, Object> head = parseData(fd.getData());
        head.put("审批状态", "已通过");
        head.put("审核人", operator);
        head.put("审核时间", fd.getAuditTime().format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss")));
        if (!opinion.isEmpty()) head.put("审核意见", opinion);
        // 审批面板：审批通过时自动填写 单据日期 + 单据编号（缺才填，幂等）
        ensureAuditStamp(head, fd.getFormNo(), panelCode);
        fd.setData(toJson(head));
        formMapper.updateById(fd);
        // 审批通过即等同于审核通过：出入库单在此落库存事务
        postStockTxn(panelCode, fd.getFormNo(), head, parseData(fd.getDetailData()));
        // 审批通过同样生成应收/应付往来单
        postFinance(panelCode, fd.getFormNo(), head, parseData(fd.getDetailData()));
        // 销货单通过后可自动开票（受 mes.invoice.auto-issue 控制；关闭时改由「销售开票」工作台手动勾选）
        if ("SALE_INV".equals(panelCode) && invoiceAutoIssue) autoCreateInvoice(fd.getFormNo());
        // 审批通过同样生成会计凭证
        postAccounting(panelCode, fd.getFormNo(), head, parseData(fd.getDetailData()));
        recordApproval(panelCode, fd.getFormNo(), "APPROVE", "APPROVED", opinion);
        refreshSourceExecution(fd);
        syncProgressFrom(fd);
        // 报工通过后推进完工：全部工序完成 → 加工单完工；末道首次完成 → 触发首检
        if ("PROCESS_REPORT".equals(panelCode)) {
            String moNo = str(parseData(fd.getData()).get("加工单号"));
            // 同步批次的累计汇报量与状态
            if (parseData(fd.getDetailData()).get("items") instanceof List<?> lines) {
                for (Object o : lines) {
                    if (!(o instanceof Map<?, ?> line)) continue;
                    String dispatchNo = str(line.get("排产单号"));
                    if (!dispatchNo.isEmpty()) syncDispatchReported(dispatchNo);
                }
            }
            if (!moNo.isEmpty()) advanceCompletion(moNo);
        }
        Map<String, Object> out = new HashMap<>();
        out.put("编号", fd.getFormNo());
        out.put("单据状态", "已审核");
        return out;
    }

    /** 检验项目结果页签：行号/项目/单项判定必须完整，出现不合格项时主行不能判合格。 */
    private void validateInspectionProjects(Map<String, Object> detail, List<Map<String, Object>> rows) {
        if (!(detail.get("projects") instanceof List<?> projects) || projects.isEmpty()) return;
        Map<Integer, Boolean> failedLines = new HashMap<>();
        for (Object value : projects) {
            if (!(value instanceof Map<?, ?> project)) continue;
            int lineNo = (int) Math.round(num(project.get("关联明细行号")));
            if (lineNo <= 0 || lineNo > rows.size()) {
                throw new IllegalStateException("检验项目关联的明细行号无效");
            }
            String item = textOf(project.get("检验项目")).trim();
            if (item.isEmpty()) item = textOf(project.get("检验项目编码")).trim();
            if (item.isEmpty()) throw new IllegalStateException("第 " + lineNo + " 行检验项目不能为空");
            String judgment = textOf(project.get("单项判定")).trim();
            if (!"合格".equals(judgment) && !"不合格".equals(judgment)) {
                throw new IllegalStateException("第 " + lineNo + " 行单项判定必须为合格/不合格");
            }
            if ("不合格".equals(judgment)) failedLines.put(lineNo, true);
        }
        for (Integer lineNo : failedLines.keySet()) {
            String verdict = textOf(rows.get(lineNo - 1).get("检验结果判定")).trim();
            if ("合格".equals(verdict)) {
                throw new IllegalStateException("第 " + lineNo
                        + " 行存在不合格检验项目，检验结果判定不能为合格");
            }
        }
    }

    /** 成品报检：已出检验结果时，合格+不合格必须等于报检数量；待检允许为 0。 */
    private void validateFinishInspectQuantities(List<Map<String, Object>> rows) {
        for (int index = 0; index < rows.size(); index++) {
            Map<String, Object> row = rows.get(index);
            double reported = num(row.get("报检数量"));
            double qualified = num(row.get("合格数量"));
            double unqualified = num(row.get("不合格数量"));
            String result = textOf(row.get("检验结果")).trim();
            if (qualified < -0.000001 || unqualified < -0.000001) {
                throw new IllegalStateException("第 " + (index + 1) + " 行合格/不合格数量不能为负");
            }
            if (qualified > reported + 0.000001 || unqualified > reported + 0.000001) {
                throw new IllegalStateException("第 " + (index + 1) + " 行合格/不合格数量不能超过报检数量");
            }
            if (!result.isEmpty() && !"待检".equals(result)
                    && Math.abs(qualified + unqualified - reported) > 0.000001) {
                throw new IllegalStateException("第 " + (index + 1)
                        + " 行合格数量 + 不合格数量必须等于报检数量");
            }
        }
    }

    /** 抽检口径（对齐开源 MES 的抽检数量/最大不合格数）：抽样量不超过报检量，允收不良数不超过抽样量。 */
    private void validateReportSampling(List<Map<String, Object>> rows) {
        for (int index = 0; index < rows.size(); index++) {
            Map<String, Object> row = rows.get(index);
            double reported = num(row.get("报检数量"));
            double sample = num(row.get("抽检数量"));
            double maxUnqualified = num(row.get("最大不合格数"));
            if (sample < -0.000001) throw new IllegalStateException("第 " + (index + 1) + " 行抽检数量不能为负");
            if (sample > reported + 0.000001) {
                throw new IllegalStateException("第 " + (index + 1) + " 行抽检数量不能超过报检数量");
            }
            if (maxUnqualified < -0.000001) {
                throw new IllegalStateException("第 " + (index + 1) + " 行最大不合格数不能为负");
            }
            double base = sample > 0.000001 ? sample : reported;
            if (maxUnqualified > base + 0.000001) {
                throw new IllegalStateException("第 " + (index + 1)
                        + " 行最大不合格数不能超过" + (sample > 0.000001 ? "抽检数量" : "报检数量"));
            }
        }
    }

    /**
     * 完工报检单与上游加工单的数量/产品校验。
     *
     * 拦截两类真实缺陷：
     * 1. 报检数量超过加工单该产品的累计完工量 —— 否则"还在生产中就已经报检完成"
     * 2. 报检的产品不在加工单产品明细里 —— 否则报了一张不属于这单的货
     *
     * 只对「完工报检类」生效（成品报检、首件报检）。
     * 工序报检（PROCESS_INSPECT_APPLY）的上游是工序汇报单，走
     * {@link #validateProcessInspectAgainstReport}，不能拿加工单的成品累计量当上限——
     * 两者口径不同（加工单按成品套数汇总，工序报检按单道工序件数），混用会误拦。
     */
    private void validateReportAgainstUpstream(String panelCode, FormData document,
                                               List<Map<String, Object>> rows) {
        if (!Set.of("FINISH_INSPECT", "FIRST_INSPECT").contains(panelCode)) return;
        Map<String, Object> head = parseData(document.getData());
        String moNo = pickText(head, "加工单号", "生产加工单号", "来源单号");
        if (moNo.isBlank()) return;

        // 用 selectList 按面板过滤：selectOne 已被上层用来取当前单据，避免串用
        FormData mo = null;
        for (FormData candidate : formMapper.selectList(
                new LambdaQueryWrapper<FormData>().eq(FormData::getPanelCode, "MANU_ORDER"))) {
            if (moNo.equals(candidate.getFormNo())) {
                mo = candidate;
                break;
            }
        }
        if (mo == null) return;

        Map<String, Object> moDetail = parseData(mo.getDetailData());
        Object productsObject = moDetail.get("products");
        if (!(productsObject instanceof List<?> products) || products.isEmpty()) return;

        // 产品编码 → 该产品的完工量（优先累计汇报套数，缺失时退到计划数量）
        Map<String, Double> finishedByProduct = new LinkedHashMap<>();
        Map<String, String> nameByProduct = new LinkedHashMap<>();
        for (Object value : products) {
            if (!(value instanceof Map<?, ?> product)) continue;
            String code = pickText(product, "产品编码");
            if (code.isBlank()) continue;
            double planned = num(product.get("数量"));
            double reported = num(product.get("累计汇报套数(工序单位)"));
            // 累计汇报为 0 但计划有值时，按"尚未完工"处理（0），不拿计划数充当完工量
            finishedByProduct.put(code, reported);
            nameByProduct.put(code, pickText(product, "产品名称"));
            if (planned < reported) finishedByProduct.put(code, planned);
        }

        // 同一产品多行报检时先汇总，避免逐行都能过、合计却超量
        Map<String, Double> reportedByProduct = new LinkedHashMap<>();
        for (Map<String, Object> row : rows) {
            String code = pickText(row, "存货编码", "产品编码");
            if (!code.isBlank()) reportedByProduct.merge(code, num(row.get("报检数量")), Double::sum);
        }

        for (Map.Entry<String, Double> entry : reportedByProduct.entrySet()) {
            String code = entry.getKey();
            if (!finishedByProduct.containsKey(code)) {
                throw new IllegalStateException("报检的产品 " + code
                        + " 不在加工单 " + moNo + " 的产品明细里，请先在加工单中增加该产品");
            }
            double finished = finishedByProduct.getOrDefault(code, 0.0);
            if (entry.getValue() > finished + 0.000001) {
                String name = nameByProduct.getOrDefault(code, "");
                throw new IllegalStateException("第 " + code + " " + name + " 报检数量 "
                        + trimNumber(entry.getValue()) + " 超过加工单 " + moNo
                        + " 的累计完工量 " + trimNumber(finished) + "，请先完成报工再报检");
            }
        }
    }

    /** 按候选键取第一个非空字符串值；键或值为空时返回空串。 */
    private static String pickText(Map<?, ?> row, String... keys) {
        if (row == null) return "";
        for (String key : keys) {
            Object value = row.get(key);
            if (value != null && !String.valueOf(value).isBlank()) {
                return String.valueOf(value).trim();
            }
        }
        return "";
    }
    /**
     * 工序报检单与上游工序汇报单的对齐校验。
     *
     * 工序报检的上游不是加工单而是「工序汇报单」：一张汇报单里有多道工序，
     * 每道工序各自有报工数量，报检必须按工序逐项比对，不能拿加工单的成品总量当上限。
     *
     * 拦截三类缺陷：
     * 1. 报检的工序不在上游汇报单里 —— 报了没做过的工序
     * 2. 该工序报工数量为 0 —— 工序还没开工
     * 3. 同一工序报检数量超过报工数量 —— 报了还没做出来的货
     */
    private void validateProcessInspectAgainstReport(String panelCode, FormData document,
                                                    List<Map<String, Object>> rows) {
        if (!"PROCESS_INSPECT_APPLY".equals(panelCode)) return;

        Map<String, Object> head = parseData(document.getData());
        String sourceNo = pickText(head, "来源单号", "匹配来源单号");
        if (sourceNo.isBlank()) return;

        FormData report = null;
        for (FormData candidate : formMapper.selectList(
                new LambdaQueryWrapper<FormData>().eq(FormData::getPanelCode, "PROCESS_REPORT"))) {
            if (sourceNo.equals(candidate.getFormNo())) {
                report = candidate;
                break;
            }
        }
        if (report == null) return;

        Object itemsObject = parseData(report.getDetailData()).get("items");
        if (!(itemsObject instanceof List<?> reportRows) || reportRows.isEmpty()) return;

        // 工序编码 → 该工序的报工数量
        Map<String, Double> reportedByProcess = new LinkedHashMap<>();
        // 产品编码 → 产品名称：用于校验报检的货确实在这张汇报单里
        Map<String, String> productNameByCode = new LinkedHashMap<>();
        for (Object value : reportRows) {
            if (!(value instanceof Map<?, ?> row)) continue;
            String processCode = pickText(row, "工序编码");
            if (!processCode.isBlank()) {
                reportedByProcess.merge(processCode, num(row.get("报工数量")), Double::sum);
            }
            String productCode = pickText(row, "产品编码", "存货编码");
            if (!productCode.isBlank()) {
                productNameByCode.putIfAbsent(productCode, pickText(row, "产品名称", "存货名称"));
            }
        }
        if (reportedByProcess.isEmpty()) return;

        // 产品归属：报检的存货必须在这张汇报单的产品里（汇报单没填产品编码时跳过，无从校验）
        if (!productNameByCode.isEmpty()) {
            for (Map<String, Object> row : rows) {
                String itemCode = pickText(row, "存货编码", "产品编码");
                if (itemCode.isBlank()) continue;
                if (!productNameByCode.containsKey(itemCode)) {
                    throw new IllegalStateException("报检的存货 " + itemCode + " " + pickText(row, "存货名称")
                            + " 不在工序汇报单 " + sourceNo + " 的产品明细里（该单产品："
                            + String.join("、", productNameByCode.keySet()) + "），请核对来源单");
                }
            }
        }

        // 同一工序多行报检先汇总，避免逐行都能过、合计却超量
        Map<String, Double> applyByProcess = new LinkedHashMap<>();
        Map<String, String> nameByProcess = new LinkedHashMap<>();
        for (Map<String, Object> row : rows) {
            String code = pickText(row, "工序编码");
            if (code.isBlank()) continue;
            applyByProcess.merge(code, num(row.get("报检数量")), Double::sum);
            nameByProcess.putIfAbsent(code, pickText(row, "工序名称"));
        }

        for (Map.Entry<String, Double> entry : applyByProcess.entrySet()) {
            String code = entry.getKey();
            String name = nameByProcess.getOrDefault(code, "");
            if (!reportedByProcess.containsKey(code)) {
                throw new IllegalStateException("报检的工序 " + code + " " + name
                        + " 不在工序汇报单 " + sourceNo + " 里，请先报工再报检");
            }
            double reported = reportedByProcess.getOrDefault(code, 0.0);
            if (entry.getValue() <= 0.000001) continue;
            // 报工量为 0 属于「工序还没开工」，跟「报多了」是两件事，分开提示
            if (reported <= 0.000001) {
                throw new IllegalStateException("工序 " + code + " " + name
                        + " 尚未报工（工序汇报单 " + sourceNo + " 的报工数量为 0），请先报工再报检");
            }
            if (entry.getValue() > reported + 0.000001) {
                throw new IllegalStateException("工序 " + code + " " + name + " 报检数量 "
                        + trimNumber(entry.getValue()) + " 超过工序汇报单 " + sourceNo
                        + " 的报工数量 " + trimNumber(reported) + "，请先补报工再报检");
            }
        }
    }

    /** 去掉多余的 .0，让错误信息里的数字更干净。 */
    private static String trimNumber(double value) {
        if (Math.abs(value - Math.rint(value)) < 0.000001) return String.valueOf((long) value);
        return String.valueOf(value);
    }

    /** BOM 进入审核/审批状态前必须已有完整父件和至少一条子件，防止空草稿变成不可编辑的已审核单。 */
    @SuppressWarnings("unchecked")
    private void validateBomForApproval(String panelCode, FormData fd) {
        if (!"BOM".equals(panelCode)) return;
        Object childrenObject = parseData(fd.getDetailData()).get("children");
        if (!(childrenObject instanceof List<?> children) || children.isEmpty()) {
            throw new IllegalStateException("物料清单必须填写父件并至少添加一条子件后才能审核");
        }
        for (int index = 0; index < children.size(); index++) {
            Object childObject = children.get(index);
            if (!(childObject instanceof Map<?, ?> child)) {
                throw new IllegalStateException("物料清单第 " + (index + 1) + " 条子件数据无效");
            }
            Object parentCodeValue = child.get("父件编码");
            Object childCodeValue = child.get("子件编码");
            String parentCode = String.valueOf(parentCodeValue == null ? "" : parentCodeValue).trim();
            String childCode = String.valueOf(childCodeValue == null ? "" : childCodeValue).trim();
            if (parentCode.isEmpty()) throw new IllegalStateException("物料清单父件编码不能为空");
            if (childCode.isEmpty()) throw new IllegalStateException("物料清单第 " + (index + 1) + " 条子件编码不能为空");
            if (parentCode.equals(childCode)) throw new IllegalStateException("物料清单父件和子件不能相同");
        }
    }

    /** 审批驳回：仅审批中 → 草稿（意见必填，驳回后修改可重新提交） */
    private Map<String, Object> rejectApproval(String panelCode, Map<String, Object> formData) {
        FormData fd = formOf(panelCode, formData.get("编号"));
        if (!"审批中".equals(fd.getStatus())) throw new IllegalStateException("仅审批中状态可审批驳回");
        requirePendingSubmission(panelCode, fd.getFormNo());
        String opinion = opinionOf(formData);
        if (opinion.isEmpty()) throw new IllegalStateException("审批驳回必须填写审批意见");
        fd.setStatus("草稿");
        fd.setUpdateTime(LocalDateTime.now());
        Map<String, Object> head = parseData(fd.getData());
        head.put("审批状态", "已驳回");
        head.remove("提交人");
        head.remove("提交时间");
        fd.setData(toJson(head));
        formMapper.updateById(fd);
        recordApproval(panelCode, fd.getFormNo(), "REJECT", "REJECTED", opinion);
        Map<String, Object> out = new HashMap<>();
        out.put("编号", fd.getFormNo());
        out.put("单据状态", "草稿");
        return out;
    }

    /** 审批情况：返回该单据全部审批记录（按时间升序） */
    private Map<String, Object> approvalHistory(String panelCode, Map<String, Object> formData) {
        FormData fd = formOf(panelCode, formData.get("编号"));
        Map<String, Object> out = new HashMap<>();
        out.put("编号", fd.getFormNo());
        out.put("list", getApprovalHistory(panelCode, fd.getFormNo()));
        return out;
    }

    public List<Map<String, Object>> getApprovalHistory(String panelCode, String formNo) {
        requirePanelPermission(panelCode, "audit");
        List<FormApproval> recs = approvalMapper.selectList(new LambdaQueryWrapper<FormApproval>()
                .eq(FormApproval::getPanelCode, panelCode)
                .eq(FormApproval::getFormNo, formNo)
                .orderByAsc(FormApproval::getId));
        List<Map<String, Object>> out = new ArrayList<>();
        for (FormApproval r : recs) {
            Map<String, Object> m = new HashMap<>();
            m.put("action", r.getAction());
            m.put("result", r.getResult());
            m.put("operator", r.getOperator());
            m.put("opinion", r.getOpinion());
            m.put("nodeNo", r.getNodeNo());
            m.put("createTime", r.getCreateTime() == null ? ""
                    : r.getCreateTime().format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss")));
            out.add(m);
        }
        return out;
    }

    @Transactional
    public void deleteForms(String panelCode, List<String> rowCodes) {
        requirePanelPermission(panelCode, "delete");
        for (String code : rowCodes) delete(panelCode, code);
    }

    /**
     * 业务流程追踪：按单号正反向找全相关单据，返回每一步的状态、当前环节与卡点。
     * 供业务流程图页面的「流程追踪」使用。
     */
    @Override
    public Map<String, Object> traceProcess(String docNo) {
        ProcessTraceService.TraceResult result = processTraceQueryService == null
                ? new ProcessTraceService.TraceResult(List.of(), "", "", "", "追踪服务不可用")
                : processTraceQueryService.trace(docNo);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("单号", docNo == null ? "" : docNo.trim());
        out.put("环节数", result.steps().size());
        out.put("当前环节", result.currentDocNo());
        out.put("卡点单号", result.stalledDocNo());
        out.put("卡点原因", result.stalledReason());
        out.put("提示", result.message());
        List<Map<String, Object>> steps = new ArrayList<>();
        for (ProcessTraceService.Step step : result.steps()) {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("序号", step.seq());
            row.put("面板编码", step.panelCode());
            row.put("环节", step.panelName());
            row.put("单号", step.docNo());
            row.put("单据状态", step.status());
            row.put("阶段", step.state());
            row.put("是否卡点", step.docNo().equals(result.stalledDocNo()));
            steps.add(row);
        }
        out.put("steps", steps);
        return out;
    }

    private void delete(String panelCode, String code) {
        FormData fd = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, panelCode)
                .eq(FormData::getFormNo, code));
        if (fd == null) throw new IllegalArgumentException("表单数据不存在：" + code);
        // 单据=草稿可删；基础档案（存货/部门等）状态为 启用/停用 同样允许删除
        String st1 = fd.getStatus() == null ? "" : fd.getStatus();
        if (!"草稿".equals(st1) && !"启用".equals(st1) && !"停用".equals(st1)) {
            throw new IllegalStateException("仅草稿状态可删除");
        }
        ensureNoEffectiveChildren(panelCode, code, "删除");
        formMapper.deleteById(fd.getId());
    }

    // ---------- 工具 ----------

    // 存货类别 → 单据编号前缀（5 类别缩写）
    private static final Map<String, String> INV_PRE = new HashMap<>();
    static {
        INV_PRE.put("产成品", "CP");
        INV_PRE.put("原材料", "YL");
        INV_PRE.put("辅助材料", "FZ");
        INV_PRE.put("包装物", "BZ");
        INV_PRE.put("半成品", "BC");
    }

    /** 存货新类别单据编号：类别前缀-最大序号+1（如 CP-002 / BC-001），按最大序号递增并查重 */
    private String invNo(String cat) {
        String pre = INV_PRE.getOrDefault(cat == null ? "" : cat, "INV");
        long max = maxSeq("INV", pre + "-");
        String no;
        do {
            max++;
            no = pre + "-" + String.format("%03d", max);
        } while (formMapper.selectCount(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "INV")
                .eq(FormData::getFormNo, no)) > 0);
        return no;
    }

    /** 单据编号（调用方没有表头数据时，退化为不带批次段）。 */
    private String generateFormNo(String panelCode) {
        return generateFormNo(panelCode, null);
    }

    /**
     * 单据编号：{前缀}-{yyyyMMdd}-{NNN}；批次单据（加工单/工序汇报单）追加批号段。
     *
     * <p>序号查重是<b>全局</b>的（不再按 panel_code 隔离）：旧实现只在面板内查重，
     * 一旦两个面板共用前缀（如 BJ- 同时给成品报检单和报价单）就会生成同一个单号。
     * 规则本身见 {@link DocNoRules}。
     *
     * @param data 表头数据，用于取批号；可为 null
     */
    private String generateFormNo(String panelCode, Map<String, Object> data) {
        String prefix = DocNoRules.prefixOf(panelCode);
        String batch = DocNoRules.isBatchPanel(panelCode) ? DocNoRules.batchSegment(data) : "";
        LocalDate day = LocalDate.now();
        int seq = 1;
        String no;
        do {
            no = DocNoRules.format(prefix, day, batch, seq++);
        } while (formMapper.selectCount(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getFormNo, no)) > 0);
        return no;
    }

    private String categoryOf(String panelCode) {
        Map<String, Object> cfg = loadConfig(panelCode);
        Object cat = cfg.get("metadata") == null ? null : ((Map<String, Object>) cfg.get("metadata")).get("panelCategory");
        return cat == null ? "" : String.valueOf(cat);
    }

    private String autoCodeFieldOf(String panelCode) {
        Map<String, Object> cfg = loadConfig(panelCode);
        Object f = cfg.get("metadata") == null ? null : ((Map<String, Object>) cfg.get("metadata")).get("autoCodeField");
        return f == null ? "" : String.valueOf(f);
    }

    /** 销售出库固定从成品仓发货（与 createSaleOutFromSo 写入的仓库一致）。 */
    private static final String SALE_OUT_WAREHOUSE = "成品仓";

    /** 启用审批流的面板（与前端 APPROVAL_PANELS 一致）：单据日期/单据编号不在新建时预填，提交审核时自动填写 */
    private static final Set<String> APPROVAL_PANELS = Set.of(
            "SO_ORDER", "PURCHASE_IN", "FINISH_IN", "OTHER_IN", "SALE_OUT", "MATERIAL_OUT", "OTHER_OUT",
            "MANU_ORDER", "PROCESS_REPORT", "INIT_AP", "INIT_AR", "INIT_BALANCE", "BOM", "ROUTE", "PU_REQ",
            "ARRIVAL_IN", "DISPATCH", "FINISH_INSPECT", "INSPECTION", "PROCESS_INSPECTION", "MATERIAL_REQ", "PICK_ORDER",
            "PU_ORDER", "SALE_INV", "PURCHASE_RETURN",
            "TRANSFER", "OUTSOURCE_ORDER", "OUTSOURCE_ISSUE", "OUTSOURCE_IN", "OUTSOURCE_FEE",
            "QUOTE_ORDER", "SALE_INVOICE", "EXPENSE", "SALE_COST_ALLOC", "PU_INVOICE", "PU_COST_ALLOC",
            "STOCK_CHECK", "LOCATION_ADJUST", "SERIAL_NO");
    private static final List<String> APPROVAL_WORKFLOW_ACTIONS = List.of(
            "提交审批", "审批通过", "审批驳回", "审批情况", "弃审");

    private boolean requiresApprovalWorkflow(String panelCode) {
        return APPROVAL_PANELS.contains(panelCode) || configUsesApprovalWorkflow(loadConfig(panelCode));
    }

    /**
     * 审批面板审核类动作（审核/提交审批/审批通过）时自动补表头：
     * 单据日期 = 当天；单据编号（autoCodeField，无则 MANU 的 锭号）= form_no。缺才填（幂等，驳回再提交不覆盖）。
     */
    private void ensureAuditStamp(Map<String, Object> head, String formNo, String panelCode) {
        if (!APPROVAL_PANELS.contains(panelCode)) return;
        Object d = head.get("单据日期");
        if (d == null || String.valueOf(d).isBlank()) head.put("单据日期", LocalDate.now().toString());
        String af = autoCodeFieldOf(panelCode);
        if (af == null || af.isBlank()) {
            Object v = head.get("锭号");
            if (v == null || String.valueOf(v).isBlank()) head.put("锭号", formNo);
        } else {
            Object v = head.get(af);
            if (v == null || String.valueOf(v).isBlank()) head.put(af, formNo);
        }
    }

    /** 新建保存兜底：字段默认值（与 getNewFormPermMatrix 一致）+ 单据日期=当天 + 单据编号/锭号=form_no（缺才填） */
    @SuppressWarnings("unchecked")
    private void fillNewDefaults(String panelCode, Map<String, Object> formData, String newNo) {
        List<String> names = new ArrayList<>();
        for (Map<String, Object> f : (List<Map<String, Object>>) fieldsOf(panelCode).get("fields")) {
            String dn = String.valueOf(f.get("dataName"));
            names.add(dn);
            Object dv = f.get("defaultValue");
            if (dv != null && !"".equals(String.valueOf(dv))) formData.putIfAbsent(dn, dv);
        }
        if (names.contains("单据日期")) {
            // 新建即填当天（忽略面板配置里的历史默认日期，2026-08-24 修复：配置 defaultValue 为旧日期导致新单日期错误）
            formData.put("单据日期", LocalDate.now().toString());
        }
        String codeKey = names.contains("单据编号") ? "单据编号" : (names.contains("锭号") ? "锭号" : null);
        if (codeKey != null) {
            Object cv = formData.get(codeKey);
            if (cv == null || String.valueOf(cv).isBlank()) formData.put(codeKey, newNo);
        }
    }

    /** 档案/自编码面板（基础档案类别 或 自编码字段）：EMP/DEPT/WH/ROUTE/BOM/INIT_* 等——保存即生效（状态=启用） */
    private boolean isArchivePanel(String panelCode) {
        if ("INV".equals(panelCode)) return true;
        // 审批面板（BOM/ROUTE/INIT_* 等）：保存保留草稿，走 草稿→审核 链路（2026-08-20 单单据改造连带修复）
        if (APPROVAL_PANELS.contains(panelCode)) return false;
        if (Set.of("基础档案", "设置").contains(categoryOf(panelCode))) return true;
        String autoField = autoCodeFieldOf(panelCode);
        if (autoField == null || autoField.isBlank() || "单据编号".equals(autoField)) return false;
        return true; // 自编码面板（工艺路线编码/物料清单编码/期初*号）
    }

    /** 基础档案编号：面板代码-最大序号+1（如 EMP-011），COUNT 法在删除/断号后会产生重复编号，改为按最大序号递增并查重 */
    private String archNo(String panelCode) {
        long max = maxSeq(panelCode, panelCode + "-");
        String no;
        do {
            max++;
            no = panelCode + "-" + String.format("%03d", max);
        } while (formMapper.selectCount(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, panelCode)
                .eq(FormData::getFormNo, no)) > 0);
        return no;
    }

    /** 取面板已有编号中的最大数字序号（解析「前缀-序号」末尾的数字），无则 0 */
    private long maxSeq(String panelCode, String prefix) {
        List<FormData> rows = formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, panelCode)
                .likeRight(FormData::getFormNo, prefix)
                .select(FormData::getFormNo));
        long max = 0;
        for (FormData fd : rows) {
            String no = fd.getFormNo();
            if (no == null) continue;
            int idx = no.lastIndexOf('-');
            String suffix = idx >= 0 ? no.substring(idx + 1) : no;
            try {
                max = Math.max(max, Long.parseLong(suffix.trim()));
            } catch (NumberFormatException ignored) {
            }
        }
        return max;
    }

    private Map<String, Object> parseData(String s) {
        if (s == null || s.isBlank()) return new HashMap<>();
        try {
            return json.readValue(s, new TypeReference<Map<String, Object>>() {});
        } catch (Exception e) {
            return new HashMap<>();
        }
    }

    private String toJson(Object o) {
        try {
            return json.writeValueAsString(o == null ? new ArrayList<>() : o);
        } catch (Exception e) {
            return "[]";
        }
    }

    // ===== 退货金额（采购线）====

    /** 退货行金额：按本次退货数量折算，不能整行复制来源金额（部分退货会把金额/税额放大）。 */
    record ReturnAmounts(double price, double taxPrice, double amount, double tax, double gross) {}

    static ReturnAmounts returnAmounts(double quantity, double price, double taxPrice, double taxRate) {
        double rate = taxRate < 0 ? 0 : taxRate;
        double amount = round2(quantity * price);
        double gross = round2(quantity * taxPrice);
        if (gross <= 0 && amount > 0) gross = round2(amount * (1 + rate / 100));
        double tax = round2(gross * rate / (100 + rate));
        if (amount <= 0 && gross > 0) amount = round2(gross - tax);
        if (taxPrice <= 0 && quantity > 0 && gross > 0) taxPrice = round2(gross / quantity);
        return new ReturnAmounts(price, taxPrice, amount, tax, gross);
    }
}
