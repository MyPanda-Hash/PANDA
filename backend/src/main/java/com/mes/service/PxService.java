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

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Set;
import java.util.List;
import java.util.LinkedHashMap;
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
            Set.of("合格", "让步接收", "部分报废", "全批报废");

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
    private final ObjectMapper json = new ObjectMapper();

    /** 权限码 → 受控按钮名（矩阵过滤映射；未列出的按钮不受权限控制） */
    private static final Map<String, List<String>> ACTION_BUTTONS = Map.of(
            "query", List.of("查找", "查询", "刷新"),
            "add", List.of("新增", "新增流程", "引入常用单据", "选单", "扫描填单", "红冲"),
            "edit", List.of("修改", "保存", "保存新增", "保存为草稿", "中止", "中止执行", "整单中止", "草稿", "取消中止", "关闭", "手工完工", "冲抵", "反冲抵"),
            "delete", List.of("删除", "删除单据", "作废"),
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
        if (af != null && !af.isBlank() && !data.containsKey(af)) data.put(af, generateFormNo(panelCode));
        // T+ 锭号 = 单据编号（自动预览单号）
        if (!data.containsKey("锭号")) data.put("锭号", generateFormNo(panelCode));
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
        // 销售发票：带上来源销货单的应收状态（应收挂账在销货单，发票只做展示）
        if ("SALE_INVOICE".equals(panelCode)) fillReceivable(data, arVoucherBySaleInvNo());
        if (isReceiptPanel(panelCode)) {
            fillReceiptPending(data);
            fillReceiptOpenDocs(panelCode, data);
        }
        Map<String, Object> privilege = new HashMap<>();
        privilege.put("actionPrivileges", actionPrivileges(panelCode, "formPages"));
        privilege.put("fieldPrivileges", new ArrayList<>());
        privilege.put("groupPrivileges", new ArrayList<>());
        out.put("privilege", privilege);
        out.put("detail", fieldsOf(panelCode).get("detail"));
        Map<String, Object> detailData = parseData(fd.getDetailData());
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
        Map<String, FinanceVoucher> arBySaleInv = "SALE_INVOICE".equals(panelCode) ? arVoucherBySaleInvNo() : null;
        for (FormData fd : all) {
            Map<String, Object> head = parseData(fd.getData());
            head.put("编号", fd.getFormNo());
            head.put("单据状态", fd.getStatus());
            head.put("创建时间", fd.getCreateTime());
            head.put("更新时间", fd.getUpdateTime());
            head.put("发起人编号", fd.getCreateBy());
            if (arBySaleInv != null) fillReceivable(head, arBySaleInv);
            if (isReceiptPanel(panelCode)) fillReceiptPending(head);
            Map<String, Object> detail = parseData(fd.getDetailData());
            if (hasCurrentStockField(detail)) {
                if (currentStock == null) currentStock = reportQueryService.currentStockRows();
                refreshCurrentStock(detail, currentStock);
            }
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
                .eq(FormData::getPanelCode, panelCode));
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
                return createMoFromSo(panelCode, formData);
            case "生成工序汇报单（自制汇报）":   // 推式生单：生产加工单 → 工序汇报单（拉取加工单工序明细）
                return createProcReportFromMo(panelCode, formData);
            case "生成采购订单":       // 推式生单：请购单 → 采购订单
                return createPuOrderFromReq(panelCode, formData);
            case "生成采购入库单":     // 推式生单：采购订单 → 采购入库单
                return "INSPECTION".equals(panelCode)
                        ? createInventoryInFromInspection(panelCode, formData, "PURCHASE_IN")
                        : createPurchaseInFromPo(panelCode, formData);
            case "生成进货单":         // 推式生单：采购订单 → 进货单（对齐真实 T+ 采购订单「生单-生成进货单」）
                return createPuInFromPo(panelCode, formData);
            case "生成产成品入库单":   // 推式生单：生产加工单 → 产成品入库单
                return "INSPECTION".equals(panelCode) || "PROCESS_INSPECTION".equals(panelCode)
                        ? createInventoryInFromInspection(panelCode, formData, "FINISH_IN")
                        : createFinishInFromMo(panelCode, formData);
            case "生成检验单":
                return createInspectionFromReport(panelCode, formData);
            case "生成材料出库单":     // 推式生单：领料申请单/生产加工单/调拨单 → 材料出库单
                return createMaterialOut(panelCode, formData);
            case "生成调拨单":         // 推式生单：领料申请单 → 调拨单（对齐真实 T+ 领料申请单「生单-生成调拨单」）
            case "生成调拨单(分单)":
                return createTransferFromMaterialReq(panelCode, formData);
            case "生成委外发料单":     // 推式生单：委外加工单/领料申请单 → 委外发料单
            case "生成委外发料单(分单)":
                return createOutsourceIssue(panelCode, formData);
            case "生成委外入库单":     // 推式生单：委外加工单 → 委外入库单
                return "INSPECTION".equals(panelCode) || "PROCESS_INSPECTION".equals(panelCode)
                        ? createInventoryInFromInspection(panelCode, formData, "OUTSOURCE_IN")
                        : createOutsourceInFromOrder(panelCode, formData);
            case "生成委外加工费用单": // 推式生单：委外加工单 → 委外加工费用单
                return createOutsourceFeeFromOrder(panelCode, formData);
            case "生成销售订单":       // 推式生单：报价单 → 销售订单（对齐真实 T+ 报价单「生单-生成销售订单」）
            case "生成销售订单(普通销售)":
                return createSaleOrderFromQuote(panelCode, formData);
            case "生成销售发票":       // 推式生单：销货单 → 销售发票（对齐真实 T+ 销货单「生单-生成销售发票」）
            case "生成销售发票(普通销售)":
            case "生成销售发票(销售退货)":
                return createSaleInvoiceFromSaleInv(panelCode, formData);
            case "开票":              // 销售发票开票：写发票号码/开票日期，状态置「已开票」
                return issueSaleInvoice(formData);
            case "作废":              // 销售发票作废：仅未开票可作废，作废后释放销货单可开票额度
                return voidSaleInvoice(formData);
            case "红冲":              // 销售发票红冲：已开票发票生成负数红字发票，原票标记已红冲
                return redFlushSaleInvoice(formData);
            case "生成销售费用分摊单": // 推式生单：费用单 → 销售费用分摊单
                return createSaleCostAllocFromExpense(panelCode, formData);
            case "生成采购发票":       // 推式生单：进货单/采购订单 → 采购发票（对齐真实 T+ 进货单/采购订单「生单-生成采购发票」）
            case "生成采购发票（普通采购）":
            case "生成采购发票（采购退货）":
                return createPuInvoice(panelCode, formData);
            case "生成采购费用分摊单": // 推式生单：费用单（采购费用）→ 采购费用分摊单
                return createPuCostAllocFromExpense(panelCode, formData);
            case "生成销售出库单":     // 推式生单：销售订单 → 销售出库单
            case "生成销售出库单(普通销售)":
                return createSaleOutFromSo(panelCode, formData);
            case "生成销售退货单":     // 推式生单：销售订单 → 销售退货单（退货入库 + 冲减应收）
                return createSaleReturnFromSo(panelCode, formData);
            case "生成销货单":         // 推式生单：产成品入库单 → 销货单（O2C 闭环）
            case "生成销货单(普通销售)":
                return createSaleInvFromFinishIn(panelCode, formData);
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
            case "冲抵":              // 退货冲抵：用退货信用冲减同一往来单位的未结应收/应付
                return setOffReturn(panelCode, formData);
            case "反冲抵":
                return reverseSetOffReturn(panelCode, formData);
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
    private Map<String, Object> createMoFromSo(String panelCode, Map<String, Object> formData) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData so = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "SO_ORDER")
                .eq(FormData::getFormNo, String.valueOf(no)));
        if (so == null) throw new IllegalArgumentException("销售订单不存在：" + no);
        if (!"已审核".equals(so.getStatus())) throw new IllegalStateException("仅已审核销售订单可生成生产加工单");

        Map<String, Object> head = parseData(so.getData());
        Map<String, Object> detailMap = parseData(so.getDetailData());
        Object itemsObj = detailMap.get("items");
        List<Map<String, Object>> items = itemsObj instanceof List ? (List<Map<String, Object>>) itemsObj : new ArrayList<>();

        String newNo = generateFormNo("MANU_ORDER");
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
                m.put("预出仓库", "原料仓");
                m.put("材料倒冲方式", "按定额倒冲");
                m.put("领料工序", "下料");
                m.put("允许循环", false);
                m.put("行中止", false);
                m.put("定额生产数量", 1);
                m.put("需用数量", 0);
                m.put("损耗数量", 0);
                m.put("计划数量", 0);
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
        detail.put("processes", defaultProcesses(products, String.valueOf(moData.getOrDefault("生产车间", "熔铸车间"))));

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
            procs = defaultProcesses(products, String.valueOf(head.getOrDefault("生产车间", "熔铸车间")));
        }
        Map<String, Object> p0 = products.get(0);

        String newNo = generateFormNo("PROCESS_REPORT");
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

        List<Map<String, Object>> items = new ArrayList<>();
        for (Map<String, Object> pr : procs) {
            Map<String, Object> it = new HashMap<>();
            it.put("加工单号", String.valueOf(no));
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
            it.put("报工数量", pr.getOrDefault("计划数量", 0));
            it.put("可报工数量", pr.getOrDefault("计划数量", 0));
            it.put("合格数量", 0);
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
            it.put("累计汇报数量", 0);
            it.put("需求令号", pr.getOrDefault("需求令号", p0.getOrDefault("需求令号", "")));
            it.put("图号", p0.getOrDefault("图号", ""));
            it.put("备注", "");
            items.add(it);
        }
        if (items.isEmpty()) throw new IllegalStateException("生产加工单无工序明细：" + no);

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

    /** 通用：读取源单据（必须已审核），插入目标草稿单，返回 {编号, gotoPanel} */
    @SuppressWarnings("unchecked")
    private Map<String, Object> insertGenerated(String targetPanel, String sourcePanel, String sourceNo,
                                                Map<String, Object> head, Map<String, Object> detail) {
        // 生单同时要求目标面板新增权限，防止仅有来源面板权限的用户越权创建下游单据。
        requirePanelPermission(targetPanel, "add");
        validateSourceLinks(targetPanel, null, detail);
        // 1:1 生单组合先查重：同一来源单不能再生成第二张同类单据
        if (isSingleGeneratePair(targetPanel, sourcePanel)) ensureNotGenerated(targetPanel, sourcePanel, sourceNo);
        String newNo = generateFormNo(targetPanel);
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

        List<Map<String, Object>> items = new ArrayList<>();
        for (int index = 0; index < sourceRows.size(); index++) {
            Map<String, Object> sourceRow = sourceRows.get(index);
            double quantity = sourceQuantity(sourceRow);
            if (quantity <= 0) continue;
            Map<String, Object> row = new HashMap<>();
            row.put("存货编码", firstValue(sourceRow, "存货编码", "产品编码", "物料编码"));
            row.put("存货名称", firstValue(sourceRow, "存货名称", "产品名称", "物料名称"));
            row.put("规格型号", sourceRow.getOrDefault("规格型号", ""));
            row.put("工序编码", sourceRow.getOrDefault("工序编码", ""));
            row.put("工序名称", sourceRow.getOrDefault("工序名称", ""));
            row.put("仓库", firstValue(sourceRow, "仓库", "预入仓库"));
            row.put("计量单位", firstValue(sourceRow, "计量单位", "采购单位", "生产单位", "单位", "工序单位"));
            row.put("报检数量", quantity);
            row.put("检验数量", quantity);
            row.put("合格数量", 0);
            row.put("不合格数量", 0);
            row.put("合格率", 0);
            row.put("检验方式", "全检");
            row.put("检验结果判定", "");
            stampSource(row, panelCode, source.getFormNo(), index + 1, quantity);
            items.add(row);
        }
        if (items.isEmpty()) throw new IllegalStateException("报检单没有大于零的报检数量");
        return insertGenerated(targetPanel, panelCode, source.getFormNo(), head, Map.of("items", items));
    }

    /** 已审核检验单 → 合格品采购/产成品/委外入库草稿。 */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createInventoryInFromInspection(String panelCode, Map<String, Object> formData,
                                                                 String targetPanel) {
        FormData source = formOf(panelCode, formData.get("编号"));
        if (!"已审核".equals(source.getStatus())) throw new IllegalStateException("仅已审核检验单可生成入库单");
        Map<String, Object> sourceHead = parseData(source.getData());
        List<Map<String, Object>> sourceRows = firstDetailRows(parseData(source.getDetailData()));
        Map<String, Object> head = new HashMap<>();
        copyFirst(sourceHead, head, "供应商编码", "供应商", "仓库", "生产车间", "加工单号", "部门", "项目", "经手人", "委外供应商");
        head.put("业务类型", switch (targetPanel) {
            case "PURCHASE_IN" -> "采购入库";
            case "OUTSOURCE_IN" -> "委外入库";
            default -> "产成品入库";
        });
        head.put("入库类别", targetPanel.equals("PURCHASE_IN") ? "采购入库" : "检验合格入库");
        head.put("检验单号", source.getFormNo());

        List<Map<String, Object>> items = new ArrayList<>();
        for (int index = 0; index < sourceRows.size(); index++) {
            Map<String, Object> sourceRow = sourceRows.get(index);
            double inspected = num(sourceRow.get("检验数量"));
            double qualified = num(sourceRow.get("合格数量"));
            String result = String.valueOf(sourceRow.getOrDefault("检验结果判定", ""));
            double accepted = "让步接收".equals(result) ? inspected : qualified;
            if (accepted <= 0 || "报废".equals(result)) continue;
            Map<String, Object> row = new HashMap<>();
            Object code = firstValue(sourceRow, "存货编码", "产品编码");
            Object name = firstValue(sourceRow, "存货名称", "产品名称");
            row.put("存货编码", code);
            row.put("产品编码", code);
            row.put("存货名称", name);
            row.put("产品名称", name);
            row.put("规格型号", sourceRow.getOrDefault("规格型号", ""));
            Object warehouse = sourceRow.get("仓库");
            row.put("仓库", warehouse == null || String.valueOf(warehouse).isBlank()
                    ? (targetPanel.equals("PURCHASE_IN") ? "原料仓" : "成品仓") : warehouse);
            Object unit = sourceRow.get("计量单位");
            row.put("计量单位", unit == null || String.valueOf(unit).isBlank() ? "件" : unit);
            row.put("实收数量", accepted);
            row.put("数量", accepted);
            row.put("单价", 0);
            row.put("金额", 0);
            stampSource(row, panelCode, source.getFormNo(), index + 1, accepted);
            items.add(row);
        }
        if (items.isEmpty()) throw new IllegalStateException("检验单没有可入库的合格或让步接收数量");
        return insertGenerated(targetPanel, panelCode, source.getFormNo(), head, Map.of("items", items));
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
        if (!"已审核".equals(src.getStatus())) throw new IllegalStateException("仅已审核请购单可生成采购订单");
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
        poData.put("交货日期", head.getOrDefault("需求日期", ""));
        poData.put("发货状态", "未发货");
        poData.put("合同号", String.valueOf(no));
        poData.put("订金金额", 0);
        poData.put("付款方式", "现付");
        poData.put("数据来源", "请购单");
        List<Map<String, Object>> rows = new ArrayList<>();
        for (int index = 0; index < items.size(); index++) {
            Map<String, Object> it = items.get(index);
            Map<String, Object> r = new HashMap<>();
            r.put("物料编码", it.getOrDefault("存货编码", ""));
            r.put("物料名称", it.getOrDefault("存货名称", ""));
            r.put("规格型号", it.getOrDefault("规格型号", ""));
            r.put("单位", it.getOrDefault("采购单位", "件"));
            r.put("数量", it.getOrDefault("数量", 0));
            r.put("单价", it.getOrDefault("单价", 0));
            r.put("金额", it.getOrDefault("金额", 0));
            r.put("税率%", it.getOrDefault("税率%", 13));
            r.put("含税单价", it.getOrDefault("含税单价", 0));
            r.put("含税金额", it.getOrDefault("含税金额", 0));
            r.put("预计到货日期", it.getOrDefault("需求日期", ""));
            r.put("现存量", it.getOrDefault("现存量", 0));
            r.put("现存量说明", it.getOrDefault("现存量说明", ""));
            rows.add(r);
        }
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("PU_ORDER", "PU_REQ", String.valueOf(no), poData, detail);
    }

    /** 推式生单：采购订单（PU_ORDER）→ 采购入库单（PURCHASE_IN） */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createPurchaseInFromPo(String panelCode, Map<String, Object> formData) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData src = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "PU_ORDER").eq(FormData::getFormNo, String.valueOf(no)));
        if (src == null) throw new IllegalArgumentException("采购订单不存在：" + no);
        if (!"已审核".equals(src.getStatus())) throw new IllegalStateException("仅已审核采购订单可生成采购入库单");
        Map<String, Object> head = parseData(src.getData());
        Map<String, Object> dm = parseData(src.getDetailData());
        List<Map<String, Object>> items = dm.get("items") instanceof List
                ? (List<Map<String, Object>>) dm.get("items") : new ArrayList<>();
        if (items.isEmpty()) throw new IllegalStateException("采购订单无明细：" + no);

        Map<String, Object> piData = new HashMap<>();
        piData.put("单据日期", LocalDate.now().toString());
        piData.put("供应商编码", head.getOrDefault("供应商编码", ""));
        piData.put("供应商", head.getOrDefault("供应商", ""));
        piData.put("采购订单号", String.valueOf(no));
        piData.put("数据来源", "采购订单");
        List<Map<String, Object>> rows = new ArrayList<>();
        for (int index = 0; index < items.size(); index++) {
            Map<String, Object> it = items.get(index);
            Map<String, Object> r = new HashMap<>();
            r.put("仓库", "原料仓");
            r.put("存货名称", it.getOrDefault("物料名称", ""));
            r.put("规格型号", it.getOrDefault("规格型号", ""));
            r.put("实收数量", it.getOrDefault("数量", 0));
            r.put("计量单位", it.getOrDefault("单位", "件"));
            r.put("单价", it.getOrDefault("单价", 0));
            r.put("税率%", it.getOrDefault("税率%", 13));
            r.put("含税单价", it.getOrDefault("含税单价", 0));
            r.put("金额", it.getOrDefault("金额", 0));
            r.put("含税金额", it.getOrDefault("含税金额", 0));
            r.put("现存量", it.getOrDefault("现存量", 0));
            stampSource(r, "PU_ORDER", String.valueOf(no), index + 1, num(it.getOrDefault("数量", 0)));
            rows.add(r);
        }
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("PURCHASE_IN", "PU_ORDER", String.valueOf(no), piData, detail);
    }

    /** 推式生单：采购订单（PU_ORDER）→ 进货单（PU_IN）：明细按采购订单物料带入，含税金额拆分税额 */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createPuInFromPo(String panelCode, Map<String, Object> formData) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData src = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "PU_ORDER").eq(FormData::getFormNo, String.valueOf(no)));
        if (src == null) throw new IllegalArgumentException("采购订单不存在：" + no);
        if (!"已审核".equals(src.getStatus())) throw new IllegalStateException("仅已审核采购订单可生成进货单");
        Map<String, Object> head = parseData(src.getData());
        Map<String, Object> dm = parseData(src.getDetailData());
        List<Map<String, Object>> items = dm.get("items") instanceof List
                ? (List<Map<String, Object>>) dm.get("items") : new ArrayList<>();
        if (items.isEmpty()) throw new IllegalStateException("采购订单无明细：" + no);

        Map<String, Object> data = new HashMap<>();
        data.put("单据日期", LocalDate.now().toString());
        data.put("业务类型", "进货");
        data.put("供应商编码", head.getOrDefault("供应商编码", ""));
        data.put("供应商", head.getOrDefault("供应商", ""));
        data.put("供应商简称", head.getOrDefault("供应商", ""));
        data.put("经手人", head.getOrDefault("经手人", ""));
        data.put("部门", head.getOrDefault("部门", ""));
        data.put("仓库", "原料仓");
        data.put("来源单据", "PU_ORDER");
        data.put("来源单号", String.valueOf(no));
        List<Map<String, Object>> rows = new ArrayList<>();
        for (Map<String, Object> it : items) {
            double qty = num(it.getOrDefault("数量", 0));
            double price = num(it.getOrDefault("含税单价", it.getOrDefault("单价", 0)));
            double taxRate = num(it.getOrDefault("税率%", 13));
            double gross = Math.round(qty * price * 100) / 100.0;
            double tax = Math.round(gross * taxRate / (100 + taxRate) * 100) / 100.0;
            Map<String, Object> r = new HashMap<>();
            r.put("仓库", "原料仓");
            r.put("存货编码", it.getOrDefault("物料编码", ""));
            r.put("存货名称", it.getOrDefault("物料名称", ""));
            r.put("规格型号", it.getOrDefault("规格型号", ""));
            r.put("数量", qty);
            r.put("采购单位", it.getOrDefault("单位", "件"));
            r.put("单价", it.getOrDefault("单价", 0));
            r.put("税率%", taxRate);
            r.put("含税单价", price);
            r.put("金额", Math.round(qty * num(it.getOrDefault("单价", 0)) * 100) / 100.0);
            r.put("税额", tax);
            r.put("含税金额", gross);
            r.put("现存量", it.getOrDefault("现存量", 0));
            rows.add(r);
        }
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("PU_IN", "PU_ORDER", String.valueOf(no), data, detail);
    }

    /** 推式生单：生产加工单（MANU_ORDER）→ 产成品入库单（FINISH_IN） */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createFinishInFromMo(String panelCode, Map<String, Object> formData) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData src = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "MANU_ORDER").eq(FormData::getFormNo, String.valueOf(no)));
        if (src == null) throw new IllegalArgumentException("生产加工单不存在：" + no);
        if (!"已审核".equals(src.getStatus()) && !"生产中".equals(src.getStatus())) {
            throw new IllegalStateException("仅已审核/生产中的生产加工单可生成产成品入库单");
        }
        Map<String, Object> head = parseData(src.getData());
        Map<String, Object> dm = parseData(src.getDetailData());
        List<Map<String, Object>> products = dm.get("products") instanceof List
                ? (List<Map<String, Object>>) dm.get("products") : new ArrayList<>();
        if (products.isEmpty()) throw new IllegalStateException("生产加工单无产成品明细：" + no);

        Map<String, Object> fiData = new HashMap<>();
        fiData.put("单据日期", LocalDate.now().toString());
        fiData.put("加工单号", String.valueOf(no));
        fiData.put("业务类型", "产成品入库");
        fiData.put("入库类别", "自制加工入库");
        fiData.put("生产车间", head.getOrDefault("生产车间", ""));
        List<Map<String, Object>> rows = new ArrayList<>();
        for (int index = 0; index < products.size(); index++) {
            Map<String, Object> p = products.get(index);
            Map<String, Object> r = new HashMap<>();
            r.put("产品名称", p.getOrDefault("产品名称", ""));
            r.put("仓库", "成品仓");
            r.put("规格型号", p.getOrDefault("规格型号", ""));
            r.put("计量单位", p.getOrDefault("生产单位", "件"));
            r.put("实收数量", p.getOrDefault("数量", 0));
            r.put("单价", 0);
            r.put("金额", 0);
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
                r.put("仓库", head.getOrDefault("调出仓库", "原料仓"));
                r.put("加工单号", head.getOrDefault("来源单号", ""));
                r.put("材料名称", it.getOrDefault("存货名称", ""));
                r.put("计量单位", it.getOrDefault("计量单位", "kg"));
                r.put("数量", it.getOrDefault("数量", 0));
                r.put("单价", it.getOrDefault("单价", 0));
                r.put("金额", it.getOrDefault("金额", 0));
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
                r.put("仓库", it.getOrDefault("仓库", "原料仓"));
                r.put("加工单号", it.getOrDefault("加工单号", ""));
                r.put("材料名称", it.getOrDefault("材料名称", ""));
                r.put("计量单位", it.getOrDefault("计量单位", "kg"));
                r.put("数量", it.getOrDefault("数量", 0));
                r.put("单价", it.getOrDefault("单价", 0));
                r.put("金额", it.getOrDefault("金额", 0));
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
                r.put("仓库", m.getOrDefault("预出仓库", "原料仓"));
                r.put("加工单号", String.valueOf(no));
                r.put("材料名称", m.getOrDefault("材料名称", ""));
                r.put("计量单位", m.getOrDefault("计量单位", "kg"));
                r.put("数量", m.getOrDefault("计划数量", m.getOrDefault("需用数量", 0)));
                r.put("单价", 0);
                r.put("金额", 0);
                r.put("规格型号", m.getOrDefault("规格型号", ""));
                r.put("现存量", m.getOrDefault("现存量", 0));
                rows.add(r);
            }
        }
        if (rows.isEmpty()) throw new IllegalStateException("来源单据无材料明细：" + no);
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("MATERIAL_OUT", panelCode, String.valueOf(no), moData, detail);
    }

    /** 推式生单：领料申请单（MATERIAL_REQ）→ 调拨单（TRANSFER）：明细材料转入调拨单，调出仓库=领料申请单仓库 */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createTransferFromMaterialReq(String panelCode, Map<String, Object> formData) {
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
        trData.put("调出仓库", head.getOrDefault("仓库", "原料仓"));
        trData.put("调入仓库", "");
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
                r.put("仓库", m.getOrDefault("预出仓库", "原料仓"));
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
                r.put("仓库", it.getOrDefault("仓库", "原料仓"));
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
        data.put("仓库", "原料仓");
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
            double price = num(firstValue(it, "单价", "成本价"));
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
    private Map<String, Object> createSaleOutFromSo(String panelCode, Map<String, Object> formData) {
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
            r.put("仓库", "成品仓");
            r.put("存货名称", it.getOrDefault("存货名称", ""));
            r.put("存货编码", it.getOrDefault("存货编码", ""));
            r.put("规格型号", it.getOrDefault("规格型号", ""));
            r.put("计量单位", it.getOrDefault("销售单位", "件"));
            r.put("数量", it.getOrDefault("数量", 0));
            r.put("税率%", it.getOrDefault("税率%", 13));
            double tax = num(it.getOrDefault("税率%", 13));
            double qty = num(it.getOrDefault("数量", 0));
            double price = num(it.getOrDefault("含税单价", 0));
            double amount = qty * price;
            r.put("售价", price);
            r.put("含税售价", price);
            r.put("销售金额", Math.round(amount * 100) / 100.0);
            r.put("含税销售金额", Math.round(amount * 100) / 100.0);
            r.put("税额", Math.round(amount * tax / (100 + tax) * 100) / 100.0);
            r.put("折扣金额", 0);
            r.put("现存量", it.getOrDefault("现存量", 0));
            r.put("现存量说明", it.getOrDefault("现存量说明", ""));
            rows.add(r);
        }
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("SALE_OUT", "SO_ORDER", String.valueOf(no), soData, detail);
    }

    /**
     * 推式生单：销售订单（SO_ORDER）→ 销售退货单（SALE_RETURN）。
     *
     * <p>退货是入库方向：退货单审核后按 SALE_RETURN 增加库存，并按 AR_RETURN 冲减应收。
     * 数量同时写「实收数量」（库存入库取该字段）与「数量」（对账取该字段）。
     */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createSaleReturnFromSo(String panelCode, Map<String, Object> formData) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData src = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "SO_ORDER").eq(FormData::getFormNo, String.valueOf(no)));
        if (src == null) throw new IllegalArgumentException("销售订单不存在：" + no);
        if (!"已审核".equals(src.getStatus())) throw new IllegalStateException("仅已审核销售订单可生成销售退货单");
        Map<String, Object> head = parseData(src.getData());
        Map<String, Object> dm = parseData(src.getDetailData());
        List<Map<String, Object>> items = dm.get("items") instanceof List
                ? (List<Map<String, Object>>) dm.get("items") : new ArrayList<>();
        if (items.isEmpty()) throw new IllegalStateException("销售订单无明细：" + no);

        Map<String, Object> ret = new HashMap<>();
        ret.put("单据日期", LocalDate.now().toString());
        ret.put("退货日期", LocalDate.now().toString());
        ret.put("客户", head.getOrDefault("客户", ""));
        ret.put("客户编码", head.getOrDefault("客户编码", ""));
        ret.put("结算客户", head.getOrDefault("结算客户", ""));
        ret.put("仓库", "成品仓");
        ret.put("来源单号", String.valueOf(no));
        Object reason = formData.get("退货原因");
        ret.put("退货原因", reason == null || String.valueOf(reason).isBlank() ? "客户退货" : String.valueOf(reason));
        ret.put("备注", "由销售订单 " + no + " 生成");

        List<Map<String, Object>> rows = new ArrayList<>();
        for (Map<String, Object> it : items) {
            double qty = num(it.getOrDefault("数量", 0));
            double price = num(it.getOrDefault("单价", 0));
            if (price == 0) {
                // 订单只填了含税单价时反算无税单价
                double taxPrice = num(it.getOrDefault("含税单价", 0));
                double tax = num(it.getOrDefault("税率%", 0));
                price = taxPrice > 0 ? taxPrice / (1 + tax / 100) : 0;
            }
            double amount = Math.round(qty * price * 100) / 100.0;
            Map<String, Object> r = new HashMap<>();
            r.put("仓库", "成品仓");
            r.put("存货编码", it.getOrDefault("存货编码", ""));
            r.put("存货名称", it.getOrDefault("存货名称", ""));
            r.put("规格型号", it.getOrDefault("规格型号", ""));
            r.put("计量单位", it.getOrDefault("销售单位", it.getOrDefault("计量单位", "件")));
            r.put("数量", qty);
            r.put("实收数量", qty);
            r.put("单价", price);
            r.put("售价", price);
            r.put("金额", amount);
            r.put("销售金额", amount);
            // 行级来源：退货占用反查与来源超量校验都按这三个字段走
            r.put("来源面板", "SO_ORDER");
            r.put("来源单号", String.valueOf(no));
            r.put("来源行号", rows.size() + 1);
            rows.add(r);
        }
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("SALE_RETURN", "SO_ORDER", String.valueOf(no), ret, detail);
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
        // 额度口径替代「只允许一张发票」：允许分批开票，但不允许累计超过销货单含税金额。
        // 生单时只要还有剩余额度就放行（草稿会带满全额，用户按需删行/改数量），真正的上限在「开票」时校验。
        double quota = saleInvInvoicableAmount(src);
        double issued = issuedInvoiceAmount(String.valueOf(no), null);
        double remaining = remainingInvoiceQuota(quota, issued);
        if (remaining <= 0) {
            throw new IllegalStateException("该销货单可开票额度已用完：可开票 " + money(quota)
                    + "，已占用 " + money(issued) + "（作废或红冲后可重新开票）");
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
        data.put("开票类型", "增值税专用发票");
        data.put("发票号码", "");
        data.put("开票日期", "");
        data.put("开票状态", "未开票");
        data.put("销货单号", String.valueOf(no));
        data.put("来源单据", "SALE_INV");
        data.put("来源单号", String.valueOf(no));
        List<Map<String, Object>> rows = new ArrayList<>();
        double totalAmount = 0;
        double totalNet = 0;
        double totalTax = 0;
        for (Map<String, Object> it : items) {
            double qty = num(it.getOrDefault("数量", 0));
            double price = num(it.getOrDefault("含税单价", it.getOrDefault("单价", 0)));
            double taxRate = num(it.getOrDefault("税率%", 13));
            double gross = Math.round(qty * price * 100) / 100.0;
            double tax = Math.round(gross * taxRate / (100 + taxRate) * 100) / 100.0;
            double net = Math.round((gross - tax) * 100) / 100.0;
            totalAmount += gross;
            totalNet += net;
            totalTax += tax;
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
        data.put("无税金额", Math.round(totalNet * 100) / 100.0);
        data.put("税额", Math.round(totalTax * 100) / 100.0);
        // 额度快照：让用户在单据上直接看到「这张能开多少、已经占了多少」，不用回销货单算
        data.put("可开票金额", quota);
        data.put("已开票金额", issued);
        data.put("原发票号", "");
        data.put("红冲状态", "未红冲");
        data.put("红字发票号", "");
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
        // 重复分摊由 insertGenerated 里的 1:1 生单校验统一拦截
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
        String allocPartner = String.valueOf(head.getOrDefault("往来单位", "")).trim();
        for (Map<String, Object> it : items) {
            double amount = num(it.getOrDefault("金额", 0));
            total += amount;
            Map<String, Object> r = new HashMap<>();
            r.put("费用单号", String.valueOf(no));
            r.put("费用项目", it.getOrDefault("费用项目", ""));
            // 分摊对象默认落到费用单往来单位（客户）；后续可在明细里改成按销售发票分摊
            r.put("分摊对象", allocPartner.isEmpty() ? "" : "客户");
            r.put("客户", allocPartner);
            r.put("分摊金额", amount);
            r.put("备注", it.getOrDefault("备注", ""));
            rows.add(r);
        }
        data.put("分摊合计", Math.round(total * 100) / 100.0);
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("SALE_COST_ALLOC", "EXPENSE", String.valueOf(no), data, detail);
    }

    /**
     * 只允许「一张来源单 → 一张目标单」的生单组合；其余生单（出库/入库/退货/委外发料等）
     * 本来就允许多张（分批出库、分批入库），不做唯一性校验，否则会把正常业务挡死。
     *
     * <p>报价单→销售订单：整单转只该转一次（2026-11-03 实测同一张报价单被点了两次，
     * 多出一张空转的「审批中」销售订单，还把流程卡点带偏）。要分批下单请走「选报价单」。
     */
    private static final Map<String, Set<String>> SINGLE_GENERATE_PAIRS = Map.of(
            "SO_ORDER", Set.of("QUOTE_ORDER"),
            "SALE_COST_ALLOC", Set.of("EXPENSE"),
            "PU_COST_ALLOC", Set.of("EXPENSE"));

    /** 该「来源 → 目标」是否只允许生一张（纯函数，便于单测）。 */
    static boolean isSingleGeneratePair(String targetPanel, String sourcePanel) {
        if (targetPanel == null || sourcePanel == null) return false;
        return SINGLE_GENERATE_PAIRS.getOrDefault(targetPanel, Set.of()).contains(sourcePanel);
    }

    /**
     * 生单唯一性校验：同一来源单已经生成过同类单据就拒绝，并把已有单号告诉用户。
     * 已作废/已中止的下游单不算数（作废后应该允许重新生单）。
     */
    private void ensureNotGenerated(String targetPanel, String sourcePanel, String sourceNo) {
        for (FormData other : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, targetPanel))) {
            String status = other.getStatus() == null ? "" : other.getStatus();
            if ("已作废".equals(status) || "已中止".equals(status)) continue;
            String srcNo = text(parseData(other.getData()).get("来源单号"));
            if (!srcNo.isEmpty() && srcNo.equals(sourceNo)) {
                throw new IllegalStateException("该" + panelNameOf(sourcePanel) + "已生成"
                        + panelNameOf(targetPanel) + "：" + other.getFormNo() + "，不能重复生成");
            }
        }
    }

    /** 推式生单：进货单（PU_IN）/采购订单（PU_ORDER）→ 采购发票（PU_INVOICE）；明细按含税单价拆分税额/价税合计 */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createPuInvoice(String panelCode, Map<String, Object> formData) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        String source = "PU_IN".equals(panelCode) ? "PU_IN" : "PU_ORDER";
        FormData src = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, source).eq(FormData::getFormNo, String.valueOf(no)));
        if (src == null) throw new IllegalArgumentException("来源单据不存在：" + no);
        if (!"已审核".equals(src.getStatus())) throw new IllegalStateException("仅已审核单据可生成采购发票");
        Map<String, Object> head = parseData(src.getData());
        Map<String, Object> dm = parseData(src.getDetailData());
        List<Map<String, Object>> items = dm.get("items") instanceof List
                ? (List<Map<String, Object>>) dm.get("items") : new ArrayList<>();
        if (items.isEmpty()) throw new IllegalStateException("来源单据无明细：" + no);

        Map<String, Object> data = new HashMap<>();
        data.put("单据日期", LocalDate.now().toString());
        data.put("业务类型", "采购发票");
        data.put("供应商", String.valueOf(head.getOrDefault("供应商", head.getOrDefault("供应商简称", ""))));
        data.put("供应商编码", head.getOrDefault("供应商编码", ""));
        data.put("部门", head.getOrDefault("部门", ""));
        data.put("业务员", String.valueOf(head.getOrDefault("业务员", head.getOrDefault("经手人", ""))));
        data.put("开票类型", "增值税专用发票");
        data.put("进货单号", "PU_IN".equals(source) ? String.valueOf(no) : "");
        data.put("采购订单号", "PU_ORDER".equals(source) ? String.valueOf(no) : "");
        data.put("来源单据", source);
        data.put("来源单号", String.valueOf(no));
        List<Map<String, Object>> rows = new ArrayList<>();
        double total = 0;
        for (Map<String, Object> it : items) {
            double qty = num(it.getOrDefault("数量", 0));
            Object priceVal = it.getOrDefault("含税单价", it.getOrDefault("单价", 0));
            double price = num(priceVal);
            double taxRate = num(it.getOrDefault("税率%", 13));
            double gross = Math.round(qty * price * 100) / 100.0;
            double tax = Math.round(gross * taxRate / (100 + taxRate) * 100) / 100.0;
            double net = Math.round((gross - tax) * 100) / 100.0;
            total += gross;
            Object invCode = it.getOrDefault("存货编码", it.getOrDefault("物料编码", ""));
            Object invName = it.getOrDefault("存货名称", it.getOrDefault("物料名称", ""));
            Object unit = it.getOrDefault("采购单位", it.getOrDefault("单位", it.getOrDefault("计量单位", "件")));
            Map<String, Object> r = new HashMap<>();
            r.put("存货编码", invCode);
            r.put("存货名称", invName);
            r.put("规格型号", it.getOrDefault("规格型号", ""));
            r.put("计量单位", unit);
            r.put("数量", qty);
            r.put("无税单价", Math.round(net / Math.max(qty, 1) * 100) / 100.0);
            r.put("税率%", taxRate);
            r.put("税额", tax);
            r.put("价税合计", gross);
            rows.add(r);
        }
        data.put("价税合计", Math.round(total * 100) / 100.0);
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("PU_INVOICE", source, String.valueOf(no), data, detail);
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
    private List<Map<String, Object>> defaultProcesses(List<Map<String, Object>> products, String workshop) {
        List<Map<String, Object>> out = new ArrayList<>();
        Map<String, Object> p0 = products.isEmpty() ? new HashMap<>() : products.get(0);
        Object qty = p0.getOrDefault("数量", 0);
        Object req = p0.getOrDefault("需求令号", "");
        Object fig = p0.getOrDefault("图号", "");
        addDefaultProc(out, 1, "PX001", "下料", workshop, "锯床-01", "下料班", "王强", qty, 2.5, 0.05, req, fig);
        addDefaultProc(out, 2, "PX002", "机加工", workshop, "数控车床-01", "车工班", "李强", qty, 3.5, 0.1, req, fig);
        addDefaultProc(out, 3, "PX007", "检验", workshop, "检测台-01", "质检班", "赵敏", qty, 1.5, 0.03, req, fig);
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

    /** 单据合计按明细重算（费用单费用合计、销售发票无税金额/税额/价税合计、分摊单分摊合计）。 */
    @SuppressWarnings("unchecked")
    private void recalcVoucherTotals(String panelCode, Map<String, Object> formData, Object detail) {
        if (!Set.of("EXPENSE", "SALE_INVOICE", "SALE_COST_ALLOC").contains(panelCode)) return;
        Map<String, Object> dm = detail instanceof Map<?, ?> map ? (Map<String, Object>) map : null;
        List<Map<String, Object>> items = dm != null && dm.get("items") instanceof List<?> list
                ? (List<Map<String, Object>>) list : List.of();
        if (items.isEmpty()) return;
        switch (panelCode) {
            case "EXPENSE" -> formData.put("费用合计", round2(sumAmount(items, "金额")));
            case "SALE_INVOICE" -> {
                double net = sumAmount(items, "数量", "无税单价");
                double tax = sumAmount(items, "税额");
                double gross = sumAmount(items, "价税合计");
                if (gross == 0 && (net != 0 || tax != 0)) gross = net + tax;
                formData.put("无税金额", round2(net));
                formData.put("税额", round2(tax));
                formData.put("价税合计", round2(gross));
            }
            case "SALE_COST_ALLOC" -> formData.put("分摊合计", round2(sumAmount(items, "分摊金额")));
            default -> {
            }
        }
    }

    private double sumAmount(List<Map<String, Object>> items, String key) {
        double total = 0;
        for (Map<String, Object> item : items) total += num(item.get(key));
        return total;
    }

    private double sumAmount(List<Map<String, Object>> items, String left, String right) {
        double total = 0;
        for (Map<String, Object> item : items) total += num(item.get(left)) * num(item.get(right));
        return total;
    }

    private static double round2(double value) {
        return Math.round(value * 100) / 100.0;
    }

    /** JSON 值转文本：null 统一成空串，避免 String.valueOf(null) 得到 "null" 参与比较。 */
    private static String text(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }

    /** 金额展示：固定两位，错误提示里的数字要能直接拿去对账。 */
    private static String money(double value) {
        return String.format(java.util.Locale.ROOT, "%.2f", value);
    }

    /**
     * 销货单可开票额度（价税合计口径）：明细「含税金额」优先，缺了按 数量×含税单价 兜底，
     * 再缺按 金额+税额 兜底 —— 历史单据也能算出额度，不用改数据。
     */
    @SuppressWarnings("unchecked")
    private double saleInvInvoicableAmount(FormData src) {
        Map<String, Object> dm = parseData(src.getDetailData());
        List<Map<String, Object>> items = dm.get("items") instanceof List<?> list
                ? (List<Map<String, Object>>) list : List.of();
        double gross = 0;
        for (Map<String, Object> it : items) gross += itemGrossAmount(it);
        return round2(gross);
    }

    /**
     * 明细行含税金额（口径优先级）：含税金额 → 数量×含税单价 → 数量×单价（无税单价）→ 金额+税额。
     * 老单明细常常只有「单价」没有「含税金额/含税单价」，若只认含税字段，额度会被算成 0，
     * 用户会看到「额度已用完 0.00」而被挡死 —— 2026-11-03 实测踩到过。
     */
    static double itemGrossAmount(Map<String, Object> it) {
        double byGross = num(it.get("含税金额"));
        if (byGross != 0) return byGross;
        double qty = num(it.get("数量"));
        double price = num(it.get("含税单价"));
        if (price == 0) price = num(it.get("单价"));
        if (qty != 0 && price != 0) return qty * price;
        return num(it.get("金额")) + num(it.get("税额"));
    }

    /**
     * 某销货单已占用的开票额度：非「已作废」发票的价税合计之和。
     * 红字发票金额为负，自动把额度冲回来，不需要额外分支。
     */
    private double issuedInvoiceAmount(String saleInvNo, String excludeInvoiceNo) {
        if (saleInvNo == null || saleInvNo.isBlank()) return 0;
        double issued = 0;
        for (FormData other : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "SALE_INVOICE"))) {
            if ("已作废".equals(text(other.getStatus()))) continue;
            if (excludeInvoiceNo != null && excludeInvoiceNo.equals(other.getFormNo())) continue;
            Map<String, Object> data = parseData(other.getData());
            if (!saleInvNo.equals(text(data.get("销货单号")))) continue;
            issued += num(data.get("价税合计"));
        }
        return round2(issued);
    }

    /**
     * 开票额度校验：本次开票价税合计不得超过销货单剩余可开票额度。
     * 红字/零额发票（gross ≤ 0）用于冲回，不受上限约束。
     */
    private void ensureInvoiceQuota(FormData source, String invoiceNo, double gross) {
        if (gross <= 0) return;
        double quota = saleInvInvoicableAmount(source);
        double issued = issuedInvoiceAmount(source.getFormNo(), invoiceNo);
        double remaining = remainingInvoiceQuota(quota, issued);
        if (!withinInvoiceQuota(remaining, gross)) {
            throw new IllegalStateException("开票金额超出可开票额度：本次 " + money(gross)
                    + "，销货单 " + source.getFormNo() + " 可开票 " + money(quota)
                    + "，已占用 " + money(issued) + "，剩余 " + money(remaining));
        }
    }

    /** 剩余可开票额度。允许为负：已作废/已红冲的票据口径走的是「已占用」，负值只用于提示。 */
    static double remainingInvoiceQuota(double quota, double issued) {
        return round2(quota - issued);
    }

    /** 本次金额是否在额度内：红字票（gross ≤ 0）用于冲回，恒放行。 */
    static boolean withinInvoiceQuota(double remaining, double gross) {
        return gross <= 0 || gross <= remaining + 0.005;
    }

    /**
     * 销售发票开票：发票号码必填且唯一，开票状态置「已开票」并写开票日期。
     * 开票时按销货单重新校验可开票额度 —— 草稿阶段的明细和金额是可以改的，
     * 只在生单时校验会被「先生成、后改大金额」绕过。
     */
    private Map<String, Object> issueSaleInvoice(Map<String, Object> formData) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData fd = formOf("SALE_INVOICE", no);
        if ("已作废".equals(text(fd.getStatus()))) {
            throw new IllegalStateException("发票已作废，不能开票：" + fd.getFormNo());
        }
        Map<String, Object> data = parseData(fd.getData());
        String invoiceNo = text(data.get("发票号码"));
        if (invoiceNo.isEmpty()) throw new IllegalStateException("请先填写发票号码再开票");
        if ("已开票".equals(text(data.get("开票状态")))) {
            throw new IllegalStateException("该发票已开票：" + invoiceNo);
        }
        for (FormData other : formMapper.selectList(new LambdaQueryWrapper<FormData>().eq(FormData::getPanelCode, "SALE_INVOICE"))) {
            if (other.getId().equals(fd.getId())) continue;
            if ("已作废".equals(text(other.getStatus()))) continue;
            String otherInvoiceNo = text(parseData(other.getData()).get("发票号码"));
            if (!otherInvoiceNo.isEmpty() && otherInvoiceNo.equals(invoiceNo)) {
                throw new IllegalStateException("发票号码已存在：" + invoiceNo);
            }
        }
        double gross = num(data.get("价税合计"));
        String saleInvNo = text(data.get("销货单号"));
        if (!saleInvNo.isEmpty()) {
            FormData source = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                    .eq(FormData::getPanelCode, "SALE_INV").eq(FormData::getFormNo, saleInvNo));
            if (source != null) ensureInvoiceQuota(source, fd.getFormNo(), gross);
        }
        data.put("开票状态", "已开票");
        data.put("开票日期", LocalDate.now().toString());
        data.put("已开票金额", issuedInvoiceAmount(saleInvNo, null));
        fd.setData(toJson(data));
        fd.setUpdateTime(LocalDateTime.now());
        formMapper.updateById(fd);
        Map<String, Object> out = new HashMap<>();
        out.put("编号", fd.getFormNo());
        out.put("开票状态", "已开票");
        out.put("开票日期", data.get("开票日期"));
        out.put("发票号码", invoiceNo);
        out.put("价税合计", round2(gross));
        out.put("已开票金额", data.get("已开票金额"));
        return out;
    }

    /**
     * 销售发票作废：只有「未开票」的发票能作废（号码还没开出去，作废即失效）；
     * 已经开出去的发票不能作废，只能红冲 —— 作废会凭空抹掉一张真发票，红冲保留痕迹。
     * 作废后该销货单的占用额度自动释放（作废单不计入已开票金额）。
     */
    private Map<String, Object> voidSaleInvoice(Map<String, Object> formData) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData fd = formOf("SALE_INVOICE", no);
        if ("已作废".equals(text(fd.getStatus()))) {
            throw new IllegalStateException("发票已作废：" + fd.getFormNo());
        }
        Map<String, Object> data = parseData(fd.getData());
        if ("已开票".equals(text(data.get("开票状态")))) {
            throw new IllegalStateException("已开票的发票不能作废，请改用红冲：" + fd.getFormNo());
        }
        if ("已红冲".equals(text(data.get("红冲状态")))) {
            throw new IllegalStateException("该发票已红冲，不能再作废：" + fd.getFormNo());
        }
        data.put("单据状态", "已作废");
        data.put("作废日期", LocalDate.now().toString());
        fd.setData(toJson(data));
        fd.setStatus("已作废");
        fd.setUpdateTime(LocalDateTime.now());
        formMapper.updateById(fd);
        Map<String, Object> out = new HashMap<>();
        out.put("编号", fd.getFormNo());
        out.put("单据状态", "已作废");
        return out;
    }

    /**
     * 销售发票红冲：已开票的发票生成一张负数红字发票把金额冲回来，原票标记「已红冲」并记下红字发票号。
     * 红字发票意外地也走「未开票 → 开票」同一道门：号码要用户按实际红字票填，
     * 所以这里只生成草稿，不在红冲动作里替用户编一个号码。
     */
    @SuppressWarnings("unchecked")
    private Map<String, Object> redFlushSaleInvoice(Map<String, Object> formData) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData fd = formOf("SALE_INVOICE", no);
        if ("已作废".equals(text(fd.getStatus()))) {
            throw new IllegalStateException("发票已作废，无需红冲：" + fd.getFormNo());
        }
        Map<String, Object> data = parseData(fd.getData());
        if (!"已开票".equals(text(data.get("开票状态")))) {
            throw new IllegalStateException("仅已开票的发票可红冲；未开票的请直接作废");
        }
        if ("已红冲".equals(text(data.get("红冲状态")))) {
            throw new IllegalStateException("该发票已红冲：" + text(data.get("红字发票号")));
        }
        if (num(data.get("价税合计")) <= 0) {
            throw new IllegalStateException("红字发票不能再红冲：" + fd.getFormNo());
        }
        Map<String, Object> dm = parseData(fd.getDetailData());
        List<Map<String, Object>> items = dm.get("items") instanceof List<?> list
                ? (List<Map<String, Object>>) list : List.of();
        if (items.isEmpty()) throw new IllegalStateException("发票无明细，无法红冲：" + fd.getFormNo());

        String invoiceNo = text(data.get("发票号码"));
        String saleInvNo = text(data.get("销货单号"));
        Map<String, Object> red = new HashMap<>();
        red.put("单据日期", LocalDate.now().toString());
        red.put("业务类型", "红字发票");
        red.put("客户", data.get("客户"));
        red.put("客户编码", data.get("客户编码"));
        red.put("结算客户", data.get("结算客户"));
        red.put("部门", data.get("部门"));
        red.put("业务员", data.get("业务员"));
        red.put("开票类型", data.getOrDefault("开票类型", "增值税专用发票"));
        red.put("发票号码", "");
        red.put("开票日期", "");
        red.put("开票状态", "未开票");
        red.put("销货单号", saleInvNo);
        red.put("原发票号", fd.getFormNo());
        red.put("红冲状态", "未红冲");
        red.put("可开票金额", data.getOrDefault("可开票金额", 0));
        red.put("已开票金额", issuedInvoiceAmount(saleInvNo, null));
        red.put("备注", "红冲 " + (invoiceNo.isEmpty() ? fd.getFormNo() : invoiceNo));

        List<Map<String, Object>> rows = new ArrayList<>();
        double net = 0;
        double tax = 0;
        double gross = 0;
        for (Map<String, Object> it : items) {
            Map<String, Object> row = negateInvoiceRow(it);
            net += num(row.get("数量")) * num(row.get("无税单价"));
            tax += num(row.get("税额"));
            gross += num(row.get("价税合计"));
            rows.add(row);
        }
        red.put("无税金额", round2(net));
        red.put("税额", round2(tax));
        red.put("价税合计", round2(gross));
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        Map<String, Object> created = insertGenerated("SALE_INVOICE", "SALE_INVOICE", fd.getFormNo(), red, detail);
        String redNo = String.valueOf(created.get("编号"));

        data.put("红冲状态", "已红冲");
        data.put("红字发票号", redNo);
        fd.setData(toJson(data));
        fd.setUpdateTime(LocalDateTime.now());
        formMapper.updateById(fd);

        created.put("原发票号", fd.getFormNo());
        created.put("价税合计", round2(gross));
        return created;
    }

    /**
     * 红字行：数量/税额/价税合计取负，无税单价保持正数 ——
     * 与「无税金额 = Σ 数量×无税单价」的合计口径一致，红蓝两单相加正好归零。
     */
    static Map<String, Object> negateInvoiceRow(Map<String, Object> row) {
        Map<String, Object> red = new LinkedHashMap<>(row);
        for (String key : List.of("数量", "税额", "价税合计")) {
            if (red.containsKey(key)) red.put(key, -Math.abs(num(red.get(key))));
        }
        return red;
    }

    private Map<String, Object> save(String panelCode, Map<String, Object> formData, String userName) {
        Object detail = formData.remove("detail");
        // 合计按明细重算：费用单/发票/分摊单此前「合计」与明细脱节（费用合计恒为 0、发票无税额合计）
        recalcVoucherTotals(panelCode, formData, detail);
        FormData fd;
        Object no = formData.get("编号");
        if (detail instanceof Map<?, ?> map) {
            validateSourceLinks(panelCode, no == null ? null : String.valueOf(no), (Map<String, Object>) map);
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
            // 列表页/弹窗只改一个字段就保存时，用传入字段覆盖原表头、未传字段保留；
            // 此前直接整表覆盖：只改「发票号码」保存后，客户/合计/来源单号全被清空。
            Map<String, Object> merged = parseData(fd.getData());
            merged.putAll(formData);
            fd.setData(toJson(merged));
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
                newNo = generateFormNo(panelCode);
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
            validateSourceLinks(panelCode, fd.getFormNo(), parseData(fd.getDetailData()));
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
            // 收/付款单已核销时先反核销：否则弃审会把往来单的已核销额留在半空，应收再也对不上
            if (Set.of("RECEIPT", "RECEIPT_PAYABLE").contains(panelCode)
                    && ("已核销".equals(fd.getStatus()) || "部分核销".equals(fd.getStatus()))) {
                throw new IllegalStateException("该单已核销，请先反核销再弃审");
            }
            if (!"已审核".equals(fd.getStatus())) throw new IllegalStateException("仅已审核状态可弃审");
            ensureNoEffectiveChildren(panelCode, fd.getFormNo(), "弃审");
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
        if ("审核".equals(action)) refreshSourceExecution(fd);
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

    /** 审核通过时落库存事务；未注入服务或非库存面板时静默跳过，不影响既有流程。 */
    private void postStockTxn(String panelCode, String formNo, Map<String, Object> head,
                              Map<String, Object> detail) {
        if (stockTxnService == null) return;
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
        financeSettleService.post(panelCode, formNo, head, detail);
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
        java.math.BigDecimal amount = java.math.BigDecimal.valueOf(settlementAmount(detail));
        if (amount.signum() == 0) return;
        String partner = textOf(firstValue(head, "客户", "供应商", "往来单位"));
        String summary = textOf(firstValue(head, "业务类型", "备注"));
        accountingService.post(panelCode, formNo, amount, partner, summary, java.time.LocalDate.now());
    }

    /** 弃审时冲销会计凭证。 */
    private void reverseAccounting(String panelCode, String formNo) {
        if (accountingService == null) return;
        accountingService.reverse(panelCode, formNo);
    }

    /** 明细金额合计：与往来/库存服务同一口径。 */
    private double settlementAmount(Map<String, Object> detail) {
        if (detail == null) return 0;
        double total = 0;
        for (Object rowsObject : detail.values()) {
            if (!(rowsObject instanceof List<?> rows)) continue;
            for (Object rowObject : rows) {
                if (!(rowObject instanceof Map<?, ?> raw)) continue;
                @SuppressWarnings("unchecked")
                Map<String, Object> row = (Map<String, Object>) raw;
                double amount = num(firstValue(row, "含税金额", "销售金额", "金额", "成本金额", "实收金额"));
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
        for (String field : List.of("报检数量", "到货数量", "数量", "派工数量", "报工数量", "检验数量", "实收数量", "金额")) {
            if (row.containsKey(field)) return num(row.get(field));
        }
        return 0;
    }

    private double executedQuantity(Map<String, Object> row) {
        for (String field : List.of("检验数量", "实收数量", "入库数量", "处理数量", "报检数量", "到货数量", "数量", "分摊金额", "金额")) {
            if (row.containsKey(field)) return num(row.get(field));
        }
        return num(row.get("来源数量"));
    }

    /**
     * Validates persisted source links. Drafts reserve a source quantity so two
     * users cannot select the same source line and later over-execute it.
     */
    private void validateSourceLinks(String targetPanel, String targetNo, Map<String, Object> detail) {
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
            List<Map<String, Object>> sourceRows = firstDetailRows(parseData(source.getDetailData()));
            if (sourceRowNo > sourceRows.size()) throw new IllegalStateException("来源明细行不存在：" + sourceNo + " 第 " + sourceRowNo + " 行");
            String key = sourcePanel + "|" + sourceNo + "|" + sourceRowNo;
            if (!localKeys.add(key)) throw new IllegalStateException("同一来源明细不能重复选入：" + sourceNo + " 第 " + sourceRowNo + " 行");

            double limit = QUALITY_INSPECTION_PANELS.contains(sourcePanel)
                    ? num(sourceRows.get(sourceRowNo - 1).get("检验数量"))
                    : sourceQuantity(sourceRows.get(sourceRowNo - 1));
            double current = executedQuantity(row);
            double reserved = reservedQuantity(targetPanel, targetNo, sourcePanel, sourceNo, sourceRowNo);
            if (current <= 0) throw new IllegalStateException("第 " + (index + 1) + " 行执行数量必须大于 0");
            if (reserved > 0.000001) {
                throw new IllegalStateException("来源明细已被其他下游单据选入：" + sourceNo + " 第 " + sourceRowNo + " 行");
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

    private double reservedQuantity(String targetPanel, String targetNo, String sourcePanel, String sourceNo, int sourceRowNo) {
        double total = 0;
        for (FormData document : formMapper.selectList(new LambdaQueryWrapper<FormData>())) {
            if (targetNo != null && targetPanel.equals(document.getPanelCode()) && targetNo.equals(document.getFormNo())) continue;
            if ("已中止".equals(document.getStatus())) continue;
            for (Map<String, Object> row : firstDetailRows(parseData(document.getDetailData()))) {
                if (sourcePanel.equals(String.valueOf(row.getOrDefault("来源面板", "")))
                        && sourceNo.equals(String.valueOf(row.getOrDefault("来源单号", "")))
                        && sourceRowNo == (int) Math.round(num(row.get("来源行号")))) {
                    total += executedQuantity(row);
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
            for (int index = 0; index < rows.size(); index++) {
                if (num(rows.get(index).get("报检数量")) <= 0 && sourceQuantity(rows.get(index)) <= 0) {
                    throw new IllegalStateException("第 " + (index + 1) + " 行报检数量必须大于 0");
                }
            }
            // 完工报检必须与上游加工单对得上，否则会出现"还在生产中就已报检完成"
            validateReportAgainstUpstream(panelCode, document, rows);
            // 工序报检的上游是工序汇报单：按工序逐项比对报工量
            validateProcessInspectAgainstReport(panelCode, document, rows);
            return;
        }

        Map<Integer, Double> treatmentByLine = new HashMap<>();
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
            double inspected = num(row.get("检验数量"));
            double qualified = num(row.get("合格数量"));
            double unqualified = num(row.get("不合格数量"));
            String result = String.valueOf(row.getOrDefault("检验结果判定", "")).trim();
            if (reported <= 0 || inspected <= 0) throw new IllegalStateException("第 " + (index + 1) + " 行报检/检验数量必须大于 0");
            if (inspected > reported + 0.000001) throw new IllegalStateException("第 " + (index + 1) + " 行检验数量不能超过报检数量");
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
            if (!QUALITY_VERDICTS.contains(result)) {
                throw new IllegalStateException("第 " + (index + 1) + " 行检验结果判定必须是：合格 / 让步接收 / 部分报废 / 全批报废");
            }
            if (unqualified <= 0 && !"合格".equals(result)) throw new IllegalStateException("第 " + (index + 1) + " 行无不合格数量时结果应为合格");
            if (unqualified > 0 && "合格".equals(result)) throw new IllegalStateException("第 " + (index + 1) + " 行有不合格数量时不能判定合格");
            if (concession > 0 && "全批报废".equals(result)) {
                throw new IllegalStateException("第 " + (index + 1) + " 行存在让步接收数量时不能判全批报废");
            }
            if (unqualified > 0) {
                double treated = treatmentByLine.getOrDefault(index + 1, 0.0);
                if (Math.abs(treated - unqualified) > 0.000001) {
                    throw new IllegalStateException("第 " + (index + 1) + " 行不合格处理数量必须等于不合格数量");
                }
            }
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
    private void refreshSourceExecution(FormData downstream) {
        Set<String> sources = new HashSet<>();
        for (Map<String, Object> row : firstDetailRows(parseData(downstream.getDetailData()))) {
            String sourcePanel = String.valueOf(row.getOrDefault("来源面板", ""));
            String sourceNo = String.valueOf(row.getOrDefault("来源单号", ""));
            if (!sourcePanel.isBlank() && !sourceNo.isBlank()) sources.add(sourcePanel + "\n" + sourceNo);
        }
        for (String key : sources) {
            String[] parts = key.split("\n", 2);
            FormData source = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                    .eq(FormData::getPanelCode, parts[0]).eq(FormData::getFormNo, parts[1]));
            if (source == null) continue;
            Map<String, Object> sourceDetail = parseData(source.getDetailData());
            List<Map<String, Object>> sourceRows = firstDetailRows(sourceDetail);
            for (Map<String, Object> row : sourceRows) {
                row.put("累计执行数量", 0);
                row.put("累计检验数量", 0);
                row.put("累计入库数量", 0);
            }
            for (FormData target : formMapper.selectList(new LambdaQueryWrapper<FormData>())) {
                if (!EFFECTIVE_STATUSES.contains(target.getStatus())) continue;
                for (Map<String, Object> row : firstDetailRows(parseData(target.getDetailData()))) {
                    if (!parts[0].equals(String.valueOf(row.getOrDefault("来源面板", "")))
                            || !parts[1].equals(String.valueOf(row.getOrDefault("来源单号", "")))) continue;
                    int line = (int) Math.round(num(row.get("来源行号")));
                    if (line <= 0 || line > sourceRows.size()) continue;
                    Map<String, Object> sourceRow = sourceRows.get(line - 1);
                    double quantity = executedQuantity(row);
                    sourceRow.put("累计执行数量", num(sourceRow.get("累计执行数量")) + quantity);
                    if (QUALITY_INSPECTION_PANELS.contains(target.getPanelCode())) {
                        sourceRow.put("累计检验数量", num(sourceRow.get("累计检验数量")) + quantity);
                    }
                    if (Set.of("PURCHASE_IN", "FINISH_IN", "OUTSOURCE_IN").contains(target.getPanelCode())) {
                        sourceRow.put("累计入库数量", num(sourceRow.get("累计入库数量")) + quantity);
                    }
                }
            }
            source.setDetailData(toJson(sourceDetail));
            source.setUpdateTime(LocalDateTime.now());
            formMapper.updateById(source);
        }
    }

    // ---------- 审批流：提交审批 → 审批中 → 审批通过/审批驳回（全留痕，可扩展多级） ----------

    /**
     * 核销：把收款/付款单金额按业务日期分配到来往单（应收挂账 → 收款核销）。
     *
     * <p>三道以前缺失的闸门：
     * <ul>
     *   <li>只核销同一往来单位：候选池按 客户/供应商 过滤，避免 A 客户的收款核掉 B 客户的应收；</li>
     *   <li>核销结果回写单据：已核销金额 / 未核销金额 / 核销日期 / 单据状态（已核销 或 部分核销），
     *       以前接口返回「已核销」但单据状态仍是「已审核」，列表看不出来；</li>
     *   <li>可重复执行：服务层先回滚旧核销再重算，点两次不会把应收核两遍。</li>
     * </ul>
     */
    private Map<String, Object> writeOff(String panelCode, Map<String, Object> formData) {
        if (financePaymentService == null) throw new IllegalStateException("未启用财务核销服务");
        if (!Set.of("RECEIPT", "RECEIPT_PAYABLE").contains(panelCode)) {
            throw new IllegalStateException("仅收款单/付款单可核销");
        }
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData fd = formOf(panelCode, no);
        if (!Set.of("已审核", "部分核销").contains(String.valueOf(fd.getStatus()))) {
            throw new IllegalStateException("仅已审核的收款/付款单可核销，当前状态：" + fd.getStatus());
        }
        Map<String, Object> head = parseData(fd.getData());
        String partnerField = "RECEIPT".equals(panelCode) ? "客户" : "供应商";
        String partner = text(head.get(partnerField));
        if (partner.isEmpty()) {
            throw new IllegalStateException("核销前请先填写" + partnerField + "：没有往来单位就无从判断核销给谁");
        }
        double amount = num(head.get("金额"));
        if (amount <= 0) throw new IllegalStateException("收款/付款金额必须大于 0，当前：" + amount);

        String voucherType = "RECEIPT".equals(panelCode) ? "AR" : "AP";
        // 不限状态：这张收款单上一轮已经核掉的单据也要留在候选里，否则按旧明细重核会被判「不在范围」。
        // 是否可核由每行的未结额决定；自动分配本来就会跳过已结清的单据。
        List<FinanceVoucher> all = financeVoucherMapper == null
                ? List.of()
                : financeVoucherMapper.selectList(new LambdaQueryWrapper<FinanceVoucher>()
                        .eq(FinanceVoucher::getVoucherType, voucherType));
        List<FinanceVoucher> scoped = FinancePaymentService.filterByPartner(all, partner);
        boolean anyOpen = false;
        for (FinanceVoucher v : scoped) {
            if (num(v.getAmount()) - num(v.getSettledAmount()) > 0) {
                anyOpen = true;
                break;
            }
        }
        if (!anyOpen) {
            throw new IllegalStateException(partner + " 没有未结的"
                    + ("AR".equals(voucherType) ? "应收" : "应付") + "，无法核销");
        }

        // 手工核销：明细里给了「来源单号 + 本次核销金额」就按行核（可只核一部分、可指定到某张单），
        // 没给就沿用按业务日期自动分配。
        List<FinancePaymentService.PlanItem> planItems = settlePlanOf(formData);
        List<FinancePaymentService.Allocation> plan = planItems.isEmpty()
                ? financePaymentService.execute(fd.getFormNo(), voucherType, scoped,
                        java.math.BigDecimal.valueOf(amount), currentUserName())
                : financePaymentService.executePlan(fd.getFormNo(), voucherType, scoped,
                        planItems, java.math.BigDecimal.valueOf(amount), currentUserName());

        java.math.BigDecimal settled = financePaymentService.settledAmount(fd.getFormNo());
        double settledValue = settled == null ? 0 : settled.doubleValue();
        boolean fully = settledValue >= amount - 0.005;
        head.put("已核销金额", round2(settledValue));
        head.put("未核销金额", round2(Math.max(0, amount - settledValue)));
        head.put("核销日期", LocalDate.now().toString());
        fd.setData(toJson(head));
        // 把「这次核到哪几张单、各多少」写回核销明细：重新打开单据能看到分配结果，
        // 再点一次核销也是按同一份明细重算，而不是悄悄改成自动分配。
        Map<String, Object> detail = parseData(fd.getDetailData());
        List<Map<String, Object>> settleRows = new ArrayList<>();
        for (FinancePaymentService.Allocation allocation : plan) {
            FinanceVoucher v = allocation.voucher();
            double open = num(v.getAmount()) - num(v.getSettledAmount());
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("来源单号", v.getSourceNo());
            row.put("未结金额", round2(Math.max(0, open)));
            row.put("本次核销金额", round2(allocation.amount().doubleValue()));
            settleRows.add(row);
        }
        detail.put("settle", settleRows);
        fd.setDetailData(toJson(detail));
        fd.setStatus(fully ? "已核销" : "部分核销");
        fd.setUpdateTime(LocalDateTime.now());
        formMapper.updateById(fd);

        List<Map<String, Object>> details = new ArrayList<>();
        for (FinancePaymentService.Allocation allocation : plan) {
            FinanceVoucher v = allocation.voucher();
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("来源单号", v.getSourceNo());
            row.put("核销金额", round2(allocation.amount().doubleValue()));
            row.put("业务日期", v.getBizDate() == null ? "" : v.getBizDate().toString());
            details.add(row);
        }
        Map<String, Object> out = new HashMap<>();
        out.put("编号", fd.getFormNo());
        out.put("核销笔数", details.size());
        out.put("已核销金额", round2(settledValue));
        out.put("未核销金额", round2(Math.max(0, amount - settledValue)));
        out.put("单据状态", fd.getStatus());
        out.put("核销明细", details);
        return out;
    }

    /** 反核销：撤销该收款/付款单的全部核销记录，并把单据退回「已审核」。 */
    private Map<String, Object> reverseWriteOff(String panelCode, Map<String, Object> formData) {
        if (financePaymentService == null) throw new IllegalStateException("未启用财务核销服务");
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData fd = formOf(panelCode, no);
        int removed = financePaymentService.reverse(fd.getFormNo());
        if (removed == 0) {
            throw new IllegalStateException("该单据没有核销记录，无需反核销");
        }
        Map<String, Object> head = parseData(fd.getData());
        double amount = num(head.get("金额"));
        head.put("已核销金额", 0);
        head.put("未核销金额", round2(amount));
        head.put("核销日期", "");
        fd.setData(toJson(head));
        Map<String, Object> detail = parseData(fd.getDetailData());
        detail.remove("settle");
        fd.setDetailData(toJson(detail));
        fd.setStatus("已审核");
        fd.setUpdateTime(LocalDateTime.now());
        formMapper.updateById(fd);
        Map<String, Object> out = new HashMap<>();
        out.put("编号", fd.getFormNo());
        out.put("撤销笔数", removed);
        out.put("单据状态", fd.getStatus());
        out.put("已核销金额", 0);
        out.put("未核销金额", round2(amount));
        return out;
    }

    /** 应收挂账（AR）按「销货单号」建索引：发票要显示自己来源单的应收状态。 */
    private Map<String, FinanceVoucher> arVoucherBySaleInvNo() {
        Map<String, FinanceVoucher> out = new HashMap<>();
        if (financeVoucherMapper == null) return out;
        for (FinanceVoucher v : financeVoucherMapper.selectList(new LambdaQueryWrapper<FinanceVoucher>()
                .eq(FinanceVoucher::getVoucherType, "AR")
                .eq(FinanceVoucher::getSourcePanel, "SALE_INV"))) {
            out.put(text(v.getSourceNo()), v);
        }
        return out;
    }

    /**
     * 把应收状态填进发票行（应收金额 / 已核销金额 / 未核销金额 / 结算状态）。
     * 查不到应收（来源销货单还没审核）时不填，避免用 0 冒充「已核销」。
     */
    private void fillReceivable(Map<String, Object> row, Map<String, FinanceVoucher> arBySaleInv) {
        if (row == null || arBySaleInv == null || arBySaleInv.isEmpty()) return;
        String saleInvNo = text(row.get("销货单号"));
        if (saleInvNo.isEmpty()) return;
        FinanceVoucher v = arBySaleInv.get(saleInvNo);
        if (v == null) return;
        double amount = v.getAmount() == null ? 0 : v.getAmount().doubleValue();
        double settled = v.getSettledAmount() == null ? 0 : v.getSettledAmount().doubleValue();
        row.put("应收金额", round2(amount));
        row.put("已核销金额", round2(settled));
        row.put("未核销金额", round2(Math.max(0, amount - settled)));
        row.put("结算状态", v.getStatus());
    }

    /** 收款单/付款单：未核销金额按读时算（金额 - 已核销），老单据存着 0 会看起来像已结清。 */
    private void fillReceiptPending(Map<String, Object> row) {
        if (row == null) return;
        double amount = num(row.get("金额"));
        double settled = num(row.get("已核销金额"));
        row.put("未核销金额", round2(Math.max(0, amount - settled)));
    }

    /** 收款单/付款单：表单上列出这个客户当前未结的应收单号，人工核销时直接照抄。 */
    private void fillReceiptOpenDocs(String panelCode, Map<String, Object> data) {
        if (!isReceiptPanel(panelCode) || financeVoucherMapper == null) return;
        String voucherType = "RECEIPT".equals(panelCode) ? "AR" : "AP";
        String partner = text(data.get("客户") != null ? data.get("客户") : data.get("供应商"));
        if (partner.isEmpty()) return;
        List<FinanceVoucher> open = financeVoucherMapper.selectList(new LambdaQueryWrapper<FinanceVoucher>()
                .eq(FinanceVoucher::getVoucherType, voucherType)
                .ne(FinanceVoucher::getStatus, "已结算"));
        StringBuilder hint = new StringBuilder();
        for (FinanceVoucher v : FinancePaymentService.filterByPartner(open, partner)) {
            double amount = v.getAmount() == null ? 0 : v.getAmount().doubleValue();
            double settled = v.getSettledAmount() == null ? 0 : v.getSettledAmount().doubleValue();
            double left = round2(amount - settled);
            if (left <= 0) continue;
            if (hint.length() > 0) hint.append("；");
            hint.append(v.getSourceNo()).append(" ").append(money(left));
        }
        data.put("未结单据", hint.toString());
    }

    /** 读「核销明细」页签里的手工核销行；未传明细或空行返回空列表（走自动分配）。 */
    @SuppressWarnings("unchecked")
    private List<FinancePaymentService.PlanItem> settlePlanOf(Map<String, Object> formData) {
        // 只认本次请求带的明细（表单上看到的那份）；请求里没有明细=自动分配，
        // 不去读库里存的历史明细，否则「清空明细改自动分配」会永远做不到。
        Map<String, Object> detail = formData.get("detail") instanceof Map<?, ?> map ? (Map<String, Object>) map : Map.of();
        Object rows = detail.get("settle");
        List<FinancePaymentService.PlanItem> out = new ArrayList<>();
        if (!(rows instanceof List<?> list)) return out;
        for (Object row : list) {
            if (!(row instanceof Map<?, ?> map)) continue;
            String sourceNo = text(((Map<String, Object>) map).get("来源单号"));
            double amount = num(((Map<String, Object>) map).get("本次核销金额"));
            if (sourceNo.isEmpty() && amount == 0) continue;
            out.add(new FinancePaymentService.PlanItem(sourceNo, java.math.BigDecimal.valueOf(amount)));
        }
        return out;
    }

    private static boolean isReceiptPanel(String panelCode) {
        return "RECEIPT".equals(panelCode) || "RECEIPT_PAYABLE".equals(panelCode);
    }

    /**
     * 退货冲抵：销售退货单审掉的是应收侧的一份信用（AR_RETURN，报表上按负数冲减），
     * 这份信用要能拿去冲同一客户的未结应收，否则客户退了货却只能在对账单上看着它。
     * 采购退货单同理冲应付。
     */
    private Map<String, Object> setOffReturn(String panelCode, Map<String, Object> formData) {
        if (financePaymentService == null) throw new IllegalStateException("未启用财务核销服务");
        if (!Set.of("SALE_RETURN", "PURCHASE_RETURN").contains(panelCode)) {
            throw new IllegalStateException("仅销售退货单/采购退货单可冲抵");
        }
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData fd = formOf(panelCode, no);
        if (!"已审核".equals(fd.getStatus())) throw new IllegalStateException("仅已审核的退货单可冲抵");
        boolean sale = "SALE_RETURN".equals(panelCode);
        String returnType = sale ? "AR_RETURN" : "AP_RETURN";
        String targetType = sale ? "AR" : "AP";
        String partnerField = sale ? "客户" : "供应商";
        Map<String, Object> head = parseData(fd.getData());
        String partner = text(head.get(partnerField));
        if (partner.isEmpty()) {
            throw new IllegalStateException("冲抵前请先填写" + partnerField + "：没有往来单位就不知道该冲谁的账");
        }
        FinanceVoucher credit = financeVoucherMapper == null ? null
                : financeVoucherMapper.selectOne(new LambdaQueryWrapper<FinanceVoucher>()
                        .eq(FinanceVoucher::getVoucherType, returnType)
                        .eq(FinanceVoucher::getSourcePanel, panelCode)
                        .eq(FinanceVoucher::getSourceNo, fd.getFormNo()));
        if (credit == null) {
            throw new IllegalStateException("该退货单没有产生" + (sale ? "应收" : "应付") + "冲减记录，无法冲抵");
        }
        if (financePaymentService.creditRemaining(credit).signum() <= 0) {
            throw new IllegalStateException("该退货单的信用已冲完：" + fd.getFormNo());
        }
        List<FinanceVoucher> targets = financeVoucherMapper == null ? List.of()
                : financeVoucherMapper.selectList(new LambdaQueryWrapper<FinanceVoucher>()
                        .eq(FinanceVoucher::getVoucherType, targetType));
        List<FinanceVoucher> scoped = FinancePaymentService.filterByPartner(targets, partner);
        List<FinancePaymentService.Allocation> plan =
                financePaymentService.setOff(fd.getFormNo(), credit, scoped, currentUserName());
        if (plan.isEmpty()) {
            throw new IllegalStateException(partner + " 没有未结的" + (sale ? "应收" : "应付") + "可冲抵");
        }
        double applied = 0;
        List<Map<String, Object>> details = new ArrayList<>();
        for (FinancePaymentService.Allocation allocation : plan) {
            double amount = allocation.amount().doubleValue();
            applied += amount;
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("来源单号", allocation.voucher().getSourceNo());
            row.put("冲抵金额", round2(amount));
            details.add(row);
        }
        double creditAmount = credit.getAmount() == null ? 0 : credit.getAmount().doubleValue();
        // 冲抵按当前余额重算，已冲抵额就是本次分配合计（不是历史累加）
        head.put("已冲抵金额", round2(applied));
        head.put("未冲抵金额", round2(Math.max(0, creditAmount - applied)));
        head.put("冲抵日期", LocalDate.now().toString());
        fd.setData(toJson(head));
        fd.setUpdateTime(LocalDateTime.now());
        formMapper.updateById(fd);

        Map<String, Object> out = new HashMap<>();
        out.put("编号", fd.getFormNo());
        out.put("冲抵笔数", details.size());
        out.put("冲抵金额", round2(applied));
        out.put("冲抵明细", details);
        return out;
    }

    /** 反冲抵：撤销该退货单的全部冲抵记录，信用回到未冲抵。 */
    private Map<String, Object> reverseSetOffReturn(String panelCode, Map<String, Object> formData) {
        if (financePaymentService == null) throw new IllegalStateException("未启用财务核销服务");
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData fd = formOf(panelCode, no);
        boolean sale = "SALE_RETURN".equals(panelCode);
        FinanceVoucher credit = financeVoucherMapper == null ? null
                : financeVoucherMapper.selectOne(new LambdaQueryWrapper<FinanceVoucher>()
                        .eq(FinanceVoucher::getVoucherType, sale ? "AR_RETURN" : "AP_RETURN")
                        .eq(FinanceVoucher::getSourcePanel, panelCode)
                        .eq(FinanceVoucher::getSourceNo, fd.getFormNo()));
        int removed = financePaymentService.reverseSetOff(fd.getFormNo(), credit);
        if (removed == 0) throw new IllegalStateException("该退货单没有冲抵记录，无需反冲抵");
        Map<String, Object> head = parseData(fd.getData());
        head.put("已冲抵金额", 0);
        head.put("未冲抵金额", round2(credit != null && credit.getAmount() != null ? credit.getAmount().doubleValue() : 0));
        head.put("冲抵日期", "");
        fd.setData(toJson(head));
        fd.setUpdateTime(LocalDateTime.now());
        formMapper.updateById(fd);
        Map<String, Object> out = new HashMap<>();
        out.put("编号", fd.getFormNo());
        out.put("撤销笔数", removed);
        out.put("已冲抵金额", 0);
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
        validateSourceLinks(panelCode, fd.getFormNo(), parseData(fd.getDetailData()));
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
        validateSourceLinks(panelCode, fd.getFormNo(), parseData(fd.getDetailData()));
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
        // 审批通过同样生成会计凭证
        postAccounting(panelCode, fd.getFormNo(), head, parseData(fd.getDetailData()));
        recordApproval(panelCode, fd.getFormNo(), "APPROVE", "APPROVED", opinion);
        refreshSourceExecution(fd);
        Map<String, Object> out = new HashMap<>();
        out.put("编号", fd.getFormNo());
        out.put("单据状态", "已审核");
        return out;
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

    private String generateFormNo(String panelCode) {
        // 单据编号：前缀-yyyy-MM-dd+2位当日序号（如 MO-2026-08-1701 / SO-2026-08-1701）
        // 每天从 01 顺序排；精确查重确保单据号不允许重复（全局唯一）
        String biz;
        if ("SO_ORDER".equals(panelCode)) biz = "SO-";
        else if ("MANU_ORDER".equals(panelCode)) biz = "MO-";
        else if ("FINISH_IN".equals(panelCode)) biz = "FI-";       // 产成品入库单
        else if ("PURCHASE_IN".equals(panelCode)) biz = "RK-";     // 采购入库单（T+ 入库惯例）
        else if ("SALE_OUT".equals(panelCode)) biz = "CK-";        // 销售出库单（T+ 出库惯例）
        else if ("MATERIAL_OUT".equals(panelCode)) biz = "CL-";    // 材料出库单（2026-08-24 修复：原默认 MO- 前缀错误）
        else if ("PROCESS_REPORT".equals(panelCode)) biz = "GX-";  // 工序汇报单
        else if ("PU_IN".equals(panelCode)) biz = "PU-";           // 进货单
        else if ("PU_ORDER".equals(panelCode)) biz = "PO-";        // 采购订单（2026-08-24 修复：原默认 MO- 前缀错误）
        else if ("PU_REQ".equals(panelCode)) biz = "CG-";          // 请购单（T+ 采购惯例 CG）
        else if ("SALE_INV".equals(panelCode)) biz = "XS-";        // 销货单
        else if ("PICK_ORDER".equals(panelCode)) biz = "PH-";      // 配货单
        else if ("MATERIAL_REQ".equals(panelCode)) biz = "LL-";    // 领料申请单
        else if ("ARRIVAL_IN".equals(panelCode)) biz = "DH-";        // 到货单
        else if ("FINISH_INSPECT".equals(panelCode)) biz = "BJ-";   // 成品报检单
        else if ("INSPECTION".equals(panelCode)) biz = "JY-";       // 来料成品检验单
        else if ("FIRST_INSPECT".equals(panelCode)) biz = "SJ-";    // 首件报检单
        else if ("PROCESS_INSPECT_APPLY".equals(panelCode)) biz = "GB-"; // 工序报检单
        else if ("PROCESS_INSPECTION".equals(panelCode)) biz = "GCJ-";   // 生产过程检验单
        else if ("DISPATCH".equals(panelCode)) biz = "PG-";         // 工序派工单
        else if ("TRANSFER".equals(panelCode)) biz = "DB-";        // 调拨单（T+ 调拨惯例 DB）
        else if ("OUTSOURCE_ORDER".equals(panelCode)) biz = "WW-"; // 委外加工单（T+ 委外惯例 WW）
        else if ("OUTSOURCE_ISSUE".equals(panelCode)) biz = "WF-"; // 委外发料单
        else if ("OUTSOURCE_IN".equals(panelCode)) biz = "WR-";    // 委外入库单
        else if ("OUTSOURCE_FEE".equals(panelCode)) biz = "WY-";   // 委外加工费用单
        else if ("QUOTE_ORDER".equals(panelCode)) biz = "BJ-";     // 报价单（T+ 报价惯例 BJ）
        else if ("SALE_INVOICE".equals(panelCode)) biz = "FP-";    // 销售发票（T+ 发票惯例 FP）
        else if ("EXPENSE".equals(panelCode)) biz = "FY-";         // 费用单（T+ 费用惯例 FY）
        else if ("SALE_COST_ALLOC".equals(panelCode)) biz = "FT-"; // 销售费用分摊单（T+ 分摊惯例 FT）
        else if ("PU_INVOICE".equals(panelCode)) biz = "PI-";     // 采购发票（进项发票）
        else if ("PU_COST_ALLOC".equals(panelCode)) biz = "PC-";  // 采购费用分摊单
        else if ("STOCK_CHECK".equals(panelCode)) biz = "PD-";    // 库存盘点单（T+ 盘点惯例 PD）
        else if ("LOCATION_ADJUST".equals(panelCode)) biz = "HW-"; // 货位调整单（T+ 货位惯例 HW）
        else if ("SERIAL_NO".equals(panelCode)) biz = "XL-";     // 序列号登记（T+ 序列号惯例 XL）
        else if ("BOM".equals(panelCode)) biz = "BOM-";          // 物料清单（2026-08-25 多单据化：可新增多张 BOM）
        else if ("INV".equals(panelCode)) biz = "INV-"; // 存货类别单据
        else if ("RECEIPT".equals(panelCode)) biz = "SK-";          // 收款单
        else if ("RECEIPT_PAYABLE".equals(panelCode)) biz = "FK-";  // 付款单
        else if ("PURCHASE_RETURN".equals(panelCode)) biz = "CT-";  // 采购退货单
        else if ("SALE_RETURN".equals(panelCode)) biz = "XT-";      // 销售退货单
        else biz = "MO-";
        String base = biz + LocalDate.now().format(DateTimeFormatter.ofPattern("yyyy-MM-dd"));
        int seq = 1;
        String no;
        do {
            no = base + String.format("%02d", seq++);
        } while (formMapper.selectCount(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, panelCode)
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

    /** 启用审批流的面板（与前端 APPROVAL_PANELS 一致）：单据日期/单据编号不在新建时预填，提交审核时自动填写 */
    private static final Set<String> APPROVAL_PANELS = Set.of(
            // 销售出库单（SALE_OUT）刻意不在其中：库存单据「审核即过账」，不走审批流。
            // 它曾在这个集合里，导致配置里的「审核」被改写成提交审批，出库单永远过不了账。
            "SO_ORDER", "PURCHASE_IN", "FINISH_IN", "OTHER_IN", "MATERIAL_OUT", "OTHER_OUT",
            "MANU_ORDER", "PROCESS_REPORT", "INIT_AP", "INIT_AR", "INIT_BALANCE", "BOM", "ROUTE", "PU_REQ",
            "ARRIVAL_IN", "DISPATCH", "FINISH_INSPECT", "INSPECTION", "MATERIAL_REQ", "PICK_ORDER",
            "PU_IN", "PU_ORDER", "SALE_INV",
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
}
