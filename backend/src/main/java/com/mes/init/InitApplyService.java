package com.mes.init;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.mapper.FormDataMapper;
import com.mes.service.StockTxnService;
import org.springframework.security.crypto.password.PasswordEncoder;

/**
 * 初始化落库（P1）：把上传解析出的行写进业务主数据。
 *
 * <p>已支持的模块：组织架构（sys_dept）、存货（INV 单文档 items）、工序（OP 单文档 operations）、
 * 工艺路线（ROUTE 每路线一张单）、BOM（每个产品一张单）。
 *
 * <p>幂等口径：一律按**自然键**判重（部门名 / 存货编码 / 工序编码 / 工艺路线编码 / 产品编码+子件编码），
 * 已存在则跳过并记为 `skipped`，重复上传同一份表不会产生重复行。
 *
 * <p>单文档面板（INV/OP）的行直接合并进那一张单据的 detail JSON；多单据面板（ROUTE/BOM）
 * 每个业务对象一张 form_data，单号直接用业务编码（GY-xxx / 产品编码），便于反查。
 */
@Service
public class InitApplyService {

    private static final Logger log = LoggerFactory.getLogger(InitApplyService.class);
    /** 单文档档案的单号（与运行库现有数据一致：INV=INV、OP=工序） */
    private static final String INV_DOC_NO = "INV";
    private static final String OP_DOC_NO = "工序";
    private static final String ARCHIVE_STATUS = "启用";
    private static final String DOC_STATUS = "已审核";

    private final JdbcTemplate jdbc;
    private final FormDataMapper formMapper;
    private final ObjectMapper json;
    private final PasswordEncoder passwordEncoder;
    private final StockTxnService stockTxnService;

    public InitApplyService(JdbcTemplate jdbc, FormDataMapper formMapper, ObjectMapper json,
                            PasswordEncoder passwordEncoder, StockTxnService stockTxnService) {
        this.jdbc = jdbc;
        this.formMapper = formMapper;
        this.json = json;
        this.passwordEncoder = passwordEncoder;
        this.stockTxnService = stockTxnService;
    }

    /** 初始化统一初始密码（用户口径 D5），登录后可自行修改。 */
    static final String INIT_PASSWORD = "123456";

    /** 落库结果：逐行明细 + 计数。 */
    public Map<String, Object> apply(InitModule module, List<Map<String, String>> rows, String operator) {
        List<Map<String, Object>> items = switch (module) {
            case DEPT -> applyDept(rows, operator);
            case PARTNER -> applyPartner(rows, operator);
            case WH -> applyWh(rows, operator);
            case ITEM -> applyItem(rows, operator);
            case PROCESS -> applyProcess(rows, operator);
            case ROUTING -> applyRouting(rows, operator);
            case BOM -> applyBom(rows, operator);
            case EMPLOYEE -> applyEmployee(rows, operator);
            case OPENING_STOCK -> throw new IllegalStateException(
                    "期初库存要核对后确认才能落库，请走「核对并写入期初」");
        };
        int inserted = 0;
        int skipped = 0;
        int failed = 0;
        for (Map<String, Object> item : items) {
            switch (String.valueOf(item.get("action"))) {
                case "insert" -> inserted++;
                case "skip" -> skipped++;
                default -> failed++;
            }
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("module", module.code());
        out.put("moduleLabel", module.label());
        out.put("inserted", inserted);
        out.put("skipped", skipped);
        out.put("failed", failed);
        out.put("items", items);
        return out;
    }

    // ───────────────────────── 组织架构 ─────────────────────────

    /** 部门：按「部门名称」判重；上级按模板里的编码在本批内解析，找不到就挂根。 */
    List<Map<String, Object>> applyDept(List<Map<String, String>> rows, String operator) {
        Map<String, Long> idByCode = new LinkedHashMap<>();
        List<Map<String, Object>> items = new ArrayList<>();

        // 第一遍：建部门（记录编码 → id）
        for (Map<String, String> row : rows) {
            String code = value(row, "部门编码");
            String name = value(row, "部门名称");
            if (name.isEmpty()) {
                items.add(item("dept", code, "skip", "部门名称为空，跳过"));
                continue;
            }
            Long existing = jdbc.query("select id from sys_dept where dept_name = ? limit 1",
                    rs -> rs.next() ? rs.getLong(1) : null, name);
            if (existing != null) {
                idByCode.put(code, existing);
                items.add(item("dept", name, "skip", "部门已存在（id=" + existing + "）"));
                continue;
            }
            Long id = jdbc.queryForObject(
                    "insert into sys_dept (parent_id, dept_name, dept_code, sort, create_time)"
                            + " values (0, ?, ?, ?, now()) returning id",
                    Long.class, name, code.isEmpty() ? null : code, intOf(row, "排序", 0));
            idByCode.put(code, id);
            items.add(item("dept", name, "insert", "新建部门 id=" + id));
        }

        // 第二遍：挂上级（父编码可能排在后行，所以单独一遍）
        for (Map<String, String> row : rows) {
            String code = value(row, "部门编码");
            String parentCode = value(row, "上级部门编码");
            if (parentCode.isEmpty() || code.isEmpty()) continue;
            Long self = idByCode.get(code);
            Long parent = idByCode.get(parentCode);
            if (self == null || parent == null || self.equals(parent)) continue;
            jdbc.update("update sys_dept set parent_id = ? where id = ?", parent, self);
        }
        return items;
    }

    // ───────────────────────── 往来单位（客户 / 供应商） ─────────────────────────

    /** 往来单位：一张单一个单位，按「往来单位编码」判重；性质决定是客户还是供应商。 */
    List<Map<String, Object>> applyPartner(List<Map<String, String>> rows, String operator) {
        List<Map<String, Object>> result = new ArrayList<>();
        for (Map<String, String> row : rows) {
            String code = value(row, "往来单位编码");
            String name = value(row, "往来单位名称");
            if (code.isEmpty() || name.isEmpty()) {
                result.add(item("partner", code, "skip", "往来单位编码或名称为空"));
                continue;
            }
            if (partnerDocByCode(code) != null) {
                result.add(item("partner", code, "skip", "往来单位已存在，跳过"));
                continue;
            }
            String nature = value(row, "性质");
            if (nature.isEmpty()) nature = "客户";
            Map<String, Object> head = new LinkedHashMap<>();
            head.put("往来单位编码", code);
            head.put("往来单位名称", name);
            head.put("性质", nature);
            head.put("结算客户", nature.contains("客户"));
            head.put("客户价格等级", "");
            head.put("分管部门", value(row, "分管部门"));
            head.put("分管人员", value(row, "分管人员"));
            head.put("职位", value(row, "职位"));
            head.put("建档日期", LocalDateTime.now().toLocalDate().toString());
            head.put("停用", "是".equals(value(row, "停用")) || "true".equalsIgnoreCase(value(row, "停用")));
            insertDoc("PARTNER", code, head, Map.of(), operator);
            result.add(item("partner", code, "insert", "新增往来单位（" + nature + "）"));
        }
        return result;
    }

    private FormData partnerDocByCode(String code) {
        for (FormData doc : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "PARTNER"))) {
            if (code.equals(String.valueOf(parse(doc.getData()).getOrDefault("往来单位编码", "")).trim())) {
                return doc;
            }
        }
        return null;
    }

    // ───────────────────────── 仓库 ─────────────────────────

    /** 仓库：单文档 WH.stores，按仓库编码判重。 */
    List<Map<String, Object>> applyWh(List<Map<String, String>> rows, String operator) {
        FormData doc = archiveDoc("WH", "仓库");
        Map<String, Object> detail = parse(doc.getDetailData());
        List<Map<String, Object>> stores = rowsOf(detail, "stores");
        Set<String> existing = new LinkedHashSet<>();
        for (Map<String, Object> row : stores) existing.add(String.valueOf(row.get("仓库编码")).trim());

        List<Map<String, Object>> result = new ArrayList<>();
        int seq = stores.size();
        for (Map<String, String> row : rows) {
            String code = value(row, "仓库编码");
            if (code.isEmpty()) {
                result.add(item("wh", "", "skip", "仓库编码为空"));
                continue;
            }
            if (!existing.add(code)) {
                result.add(item("wh", code, "skip", "仓库已存在，跳过"));
                continue;
            }
            Map<String, Object> r = new LinkedHashMap<>();
            r.put("编号", "WH-" + String.format("%03d", ++seq));
            r.put("仓库编码", code);
            r.put("仓库名称", value(row, "仓库名称"));
            r.put("仓库类别", value(row, "仓库类别"));
            r.put("仓库地址", value(row, "仓库地址"));
            r.put("负责人", value(row, "负责人"));
            r.put("停用", false);
            r.put("允许零库存出库", "是".equals(value(row, "允许零库存出库"))
                    || "true".equalsIgnoreCase(value(row, "允许零库存出库")));
            r.put("备注", "");
            stores.add(r);
            result.add(item("wh", code, "insert", "新增仓库"));
        }
        if (result.stream().anyMatch(r -> "insert".equals(r.get("action")))) {
            detail.put("stores", stores);
            saveDetail(doc, detail, operator);
        }
        return result;
    }

    // ───────────────────────── 存货 ─────────────────────────

    /** 存货：单文档 INV.items，按存货编码判重合并。 */
    List<Map<String, Object>> applyItem(List<Map<String, String>> rows, String operator) {
        FormData doc = archiveDoc("INV", INV_DOC_NO);
        Map<String, Object> detail = parse(doc.getDetailData());
        List<Map<String, Object>> items = rowsOf(detail, "items");
        Set<String> existing = new LinkedHashSet<>();
        for (Map<String, Object> row : items) existing.add(String.valueOf(row.get("存货编码")).trim());

        List<Map<String, Object>> result = new ArrayList<>();
        int seq = items.size();
        for (Map<String, String> row : rows) {
            String code = value(row, "存货编码");
            if (code.isEmpty()) {
                result.add(item("item", "", "skip", "存货编码为空"));
                continue;
            }
            if (!existing.add(code)) {
                result.add(item("item", code, "skip", "存货已存在，跳过"));
                continue;
            }
            Map<String, Object> r = new LinkedHashMap<>();
            r.put("存货编码", code);
            r.put("存货名称", value(row, "存货名称"));
            r.put("规格型号", value(row, "规格型号"));
            r.put("计量单位", value(row, "计量单位"));
            r.put("计价方式", value(row, "计价方式").isEmpty() ? "全月平均" : value(row, "计价方式"));
            r.put("所属类别", value(row, "所属类别"));
            r.put("参考成本", number(value(row, "参考成本"), 0));
            r.put("最新成本", number(value(row, "参考成本"), 0));
            r.put("工艺路线", value(row, "工艺路线"));
            r.put("品牌", "");
            r.put("属性", "");
            r.put("序列号管理", false);
            r.put("停用", false);
            r.put("建档日期", LocalDateTime.now().toLocalDate().toString());
            r.put("备注", "");
            items.add(r);
            seq++;
            result.add(item("item", code, "insert", "新增存货"));
        }
        if (result.stream().anyMatch(r -> "insert".equals(r.get("action")))) {
            detail.put("items", items);
            saveDetail(doc, detail, operator);
        }
        return result;
    }

    // ───────────────────────── 员工 + 账号 + 角色 ─────────────────────────

    /**
     * 员工：EMP 档案 + `sys_user` 账号（密码统一 123456）+ 按职务自动建角色并授权。
     *
     * <p>管理员保护（D3）：`admin` 账号一律跳过；不会创建 is_admin=1 的角色。
     * 部门按模板里的「所属部门编码」解析（sys_dept.dept_code，V5 新增）；
     * 找不到时回退按部门名称匹配，再找不到则账号不带部门并给出提示。
     */
    List<Map<String, Object>> applyEmployee(List<Map<String, String>> rows, String operator) {
        FormData doc = archiveDoc("EMP", "员工档案");
        Map<String, Object> detail = parse(doc.getDetailData());
        List<Map<String, Object>> employees = rowsOf(detail, "employees");
        Set<String> existingEmp = new LinkedHashSet<>();
        for (Map<String, Object> row : employees) existingEmp.add(String.valueOf(row.get("员工编码")).trim());

        List<Map<String, Object>> result = new ArrayList<>();
        boolean changed = false;
        for (Map<String, String> row : rows) {
            String code = value(row, "员工编码");
            String name = value(row, "姓名");
            String account = value(row, "登录账号");
            if (account.isEmpty()) account = code;
            if ("admin".equalsIgnoreCase(account)) {
                result.add(item("employee", code, "skip", "账号 admin 由系统保留，初始化跳过"));
                continue;
            }
            if (code.isEmpty() || name.isEmpty()) {
                result.add(item("employee", code, "skip", "员工编码或姓名为空"));
                continue;
            }
            if (existingEmp.add(code)) {
                Map<String, Object> emp = new LinkedHashMap<>();
                emp.put("员工编码", code);
                emp.put("员工名称", name);
                emp.put("所属部门", deptNameOf(value(row, "所属部门编码")));
                emp.put("职务", value(row, "职务"));
                emp.put("手机", value(row, "手机"));
                emp.put("业务员", false);
                emp.put("停用", false);
                emp.put("备注", value(row, "岗位职责"));
                employees.add(emp);
                changed = true;
                result.add(item("employee", code, "insert", "新增员工档案"));
            } else {
                result.add(item("employee", code, "skip", "员工档案已存在"));
            }

            // 账号：已存在就跳过（不覆盖既有账号的密码与角色，避免第二次导入把人改了）
            Integer exists = jdbc.queryForObject(
                    "select count(*) from sys_user where user_name = ?", Integer.class, account);
            if (exists != null && exists > 0) {
                result.add(item("user", account, "skip", "账号已存在，跳过（不改密码与角色）"));
                continue;
            }
            InitRoleTemplates.Role roleKind = InitRoleTemplates.classify(value(row, "职务"), value(row, "岗位职责"));
            boolean supervisor = InitRoleTemplates.isSupervisor(value(row, "职务"));
            Long roleId = ensureRole(roleKind, supervisor, operator);
            Long deptId = deptIdOf(value(row, "所属部门编码"));
            jdbc.update("""
                    insert into sys_user (user_name, password, real_name, phone, factory_code,
                                          enabled, role_id, dept_id, create_time)
                    values (?,?,?,?,?,1,?,?, now())
                    """, account, passwordEncoder.encode(INIT_PASSWORD), name,
                    value(row, "手机"), "F01", roleId, deptId);
            result.add(item("user", account, "insert",
                    "建账号（初始密码 " + INIT_PASSWORD + "），角色=" + InitRoleTemplates.roleName(roleKind, supervisor)));
        }
        if (changed) {
            detail.put("employees", employees);
            saveDetail(doc, detail, operator);
        }
        return result;
    }

    /**
     * 建角色（按编码判重）并补齐面板权限。
     *
     * <p>sys_role_panel 上没有 (role_id, panel_code) 唯一约束，不能用 ON CONFLICT；
     * 这里按「已存在即跳过」逐条判断，重跑或上次半途失败都能补齐，不会重复授权。
     */
    private Long ensureRole(InitRoleTemplates.Role role, boolean supervisor, String operator) {
        String code = InitRoleTemplates.roleCode(role, supervisor);
        String name = InitRoleTemplates.roleName(role, supervisor);
        Long id = jdbc.query("select id from sys_role where role_code = ? limit 1",
                rs -> rs.next() ? rs.getLong(1) : null, code);
        boolean created = false;
        if (id == null) {
            id = jdbc.queryForObject(
                    "insert into sys_role (role_code, role_name, is_admin, remark, create_time)"
                            + " values (?,?,0,?,now()) returning id",
                    Long.class, code, name, "初始化按岗位模板创建");
            created = true;
        }
        Map<String, List<String>> grants = InitRoleTemplates.grants(role, supervisor);
        int granted = 0;
        for (Map.Entry<String, List<String>> entry : grants.entrySet()) {
            List<String> perms = entry.getValue();
            Integer exists = jdbc.queryForObject(
                    "select count(*) from sys_role_panel where role_id = ? and panel_code = ?",
                    Integer.class, id, entry.getKey());
            if (exists != null && exists > 0) continue;
            jdbc.update("""
                    insert into sys_role_panel (role_id, panel_code, perms, can_approve)
                    values (?,?,?,?)
                    """, id, entry.getKey(), String.join(",", perms), perms.contains("audit") ? 1 : 0);
            granted++;
        }
        if (created || granted > 0) {
            log.info("初始化{}角色 {}（{}）：新增授权 {} 个面板 by {}",
                    created ? "创建" : "补齐", name, code, granted, operator);
        }
        return id;
    }

    private Long deptIdOf(String deptCode) {
        if (deptCode == null || deptCode.isBlank()) return null;
        Long id = jdbc.query("select id from sys_dept where dept_code = ? limit 1",
                rs -> rs.next() ? rs.getLong(1) : null, deptCode.trim());
        if (id != null) return id;
        return jdbc.query("select id from sys_dept where dept_name = ? limit 1",
                rs -> rs.next() ? rs.getLong(1) : null, deptCode.trim());
    }

    private String deptNameOf(String deptCode) {
        if (deptCode == null || deptCode.isBlank()) return "";
        Long id = deptIdOf(deptCode);
        if (id == null) return deptCode;
        return jdbc.query("select dept_name from sys_dept where id = ?",
                rs -> rs.next() ? rs.getString(1) : "", id);
    }

    // ───────────────────────── 工序 ─────────────────────────

    /** 工序：单文档 OP.operations，按工序编码判重。 */
    List<Map<String, Object>> applyProcess(List<Map<String, String>> rows, String operator) {
        FormData doc = archiveDoc("OP", OP_DOC_NO);
        Map<String, Object> detail = parse(doc.getDetailData());
        List<Map<String, Object>> operations = rowsOf(detail, "operations");
        Set<String> existing = new LinkedHashSet<>();
        for (Map<String, Object> row : operations) existing.add(String.valueOf(row.get("工序编码")).trim());

        List<Map<String, Object>> result = new ArrayList<>();
        int seq = operations.size();
        for (Map<String, String> row : rows) {
            String code = value(row, "工序编码");
            if (code.isEmpty()) {
                result.add(item("process", "", "skip", "工序编码为空"));
                continue;
            }
            if (!existing.add(code)) {
                result.add(item("process", code, "skip", "工序已存在，跳过"));
                continue;
            }
            Map<String, Object> r = new LinkedHashMap<>();
            r.put("编号", "OP-" + String.format("%03d", ++seq));
            r.put("工序编码", code);
            r.put("工序名称", value(row, "工序名称"));
            r.put("默认车间", value(row, "默认车间"));
            r.put("加工方式", value(row, "加工方式").isEmpty() ? "自制" : value(row, "加工方式"));
            r.put("标准合格率%", number(value(row, "标准合格率%"), 100));
            r.put("关键工序", false);
            r.put("按辅单位计价", false);
            r.put("辅单位", "件");
            r.put("换算率", 1);
            r.put("默认工资类型", "计件");
            r.put("计件依据", value(row, "计件依据").isEmpty() ? "合格数量" : value(row, "计件依据"));
            r.put("是否停用", false);
            r.put("备注", "");
            operations.add(r);
            result.add(item("process", code, "insert", "新增工序"));
        }
        if (result.stream().anyMatch(r -> "insert".equals(r.get("action")))) {
            detail.put("operations", operations);
            saveDetail(doc, detail, operator);
        }
        return result;
    }

    // ───────────────────────── 工艺路线 ─────────────────────────

    /** 工艺路线：一个路线编码一张单，明细挂在 processes；按路线编码判重。 */
    List<Map<String, Object>> applyRouting(List<Map<String, String>> rows, String operator) {
        Map<String, List<Map<String, String>>> grouped = new LinkedHashMap<>();
        for (Map<String, String> row : rows) {
            String code = value(row, "工艺路线编码");
            if (code.isEmpty()) continue;
            grouped.computeIfAbsent(code, k -> new ArrayList<>()).add(row);
        }
        List<Map<String, Object>> result = new ArrayList<>();
        for (Map.Entry<String, List<Map<String, String>>> entry : grouped.entrySet()) {
            String code = entry.getKey();
            FormData existing = formDoc("ROUTE", code);
            if (existing == null) existing = routeDocByCode(code);
            if (existing != null) {
                result.add(item("routing", code, "skip", "工艺路线已存在（" + existing.getFormNo() + "），跳过"));
                continue;
            }
            String name = value(entry.getValue().get(0), "工艺路线名称");
            Map<String, Object> head = new LinkedHashMap<>();
            head.put("备注", "工艺路线");
            head.put("工艺路线编码", code);
            head.put("工艺路线名称", name);
            head.put("停用", false);
            List<Map<String, Object>> processes = new ArrayList<>();
            int order = 0;
            for (Map<String, String> row : entry.getValue()) {
                Map<String, Object> p = new LinkedHashMap<>();
                order++;
                int seq = (int) number(value(row, "工序顺序"), order * 10);
                p.put("工序顺序", seq);
                p.put("加工顺序", seq);
                p.put("工序编码", value(row, "工序编码"));
                p.put("工序名称", processNameOf(value(row, "工序编码")));
                p.put("加工方式", "自制");
                p.put("生产车间", "");
                p.put("工资类型", value(row, "工资类型").isEmpty() ? "计件" : value(row, "工资类型"));
                p.put("计件依据", "合格数量");
                p.put("委外供应商", value(row, "委外供应商"));
                p.put("按辅单位计价", false);
                p.put("辅单位", "件");
                p.put("换算率", 1);
                p.put("默认报工数量", 0);
                p.put("关键工序", false);
                processes.add(p);
            }
            insertDoc("ROUTE", code, head, Map.of("processes", processes), operator);
            result.add(item("routing", code, "insert", "新增工艺路线，工序 " + processes.size() + " 道"));
        }
        return result;
    }

    // ───────────────────────── BOM ─────────────────────────

    /** BOM：一个产品一张单，明细挂在 children；按「产品编码 + 子件编码」判重。 */
    List<Map<String, Object>> applyBom(List<Map<String, String>> rows, String operator) {
        Map<String, List<Map<String, String>>> grouped = new LinkedHashMap<>();
        for (Map<String, String> row : rows) {
            String product = value(row, "产品编码");
            if (product.isEmpty()) continue;
            grouped.computeIfAbsent(product, k -> new ArrayList<>()).add(row);
        }
        List<Map<String, Object>> result = new ArrayList<>();
        for (Map.Entry<String, List<Map<String, String>>> entry : grouped.entrySet()) {
            String product = entry.getKey();
            FormData doc = formDoc("BOM", product);
            Map<String, Object> detail = doc == null ? new LinkedHashMap<>() : parse(doc.getDetailData());
            List<Map<String, Object>> children = rowsOf(detail, "children");
            Set<String> existing = new LinkedHashSet<>();
            for (Map<String, Object> c : children) existing.add(String.valueOf(c.get("子件编码")).trim());

            int added = 0;
            for (Map<String, String> row : entry.getValue()) {
                String child = value(row, "子件编码");
                if (child.isEmpty() || !existing.add(child)) {
                    result.add(item("bom", product + "/" + child, "skip",
                            child.isEmpty() ? "子件编码为空" : "该父件下子件已存在，跳过"));
                    continue;
                }
                Map<String, Object> c = new LinkedHashMap<>();
                String route = routeOfProduct(product);
                c.put("物料清单编码", product);
                c.put("父件编码", product);
                c.put("父件名称", productNameOf(product));
                c.put("虚拟件", false);
                c.put("版本号", "V1.0");
                c.put("计量单位", "件");
                c.put("生产数量", number(value(row, "生产数量"), 1));
                c.put("生产车间", "");
                c.put("预入仓库", value(row, "预入仓库"));
                c.put("默认BOM", true);
                c.put("子件编码", child);
                c.put("子件名称", itemNameOf(child));
                c.put("规格型号", itemSpecOf(child));
                c.put("子件计量单位", itemUnitOf(child));
                c.put("定额数量", number(value(row, "定额数量"), 0));
                c.put("损耗率%", number(value(row, "损耗率%"), 0));
                c.put("领料工序", "");
                c.put("领料工序编码", "");
                children.add(c);
                added++;
            }
            if (added > 0) {
                if (doc == null) {
                    Map<String, Object> head = new LinkedHashMap<>();
                    head.put("备注", "物料清单");
                    head.put("产品编码", product);
                    head.put("产品名称", productNameOf(product));
                    head.put("工艺路线编码", routeOfProduct(product));
                    head.put("工艺路线名称", routeNameOf(routeOfProduct(product)));
                    head.put("版本号", "V1.0");
                    insertDoc("BOM", product, head, Map.of("children", children), operator);
                    result.add(item("bom", product, "insert", "新建 BOM，" + added + " 行子件"));
                } else {
                    detail.put("children", children);
                    saveDetail(doc, detail, operator);
                    result.add(item("bom", product, "insert", "追加 " + added + " 行子件"));
                }
            }
        }
        return result;
    }

    // ───────────────────────── 期初库存（核对后确认） ─────────────────────────

    /**
     * 期初库存确认落库：写证据表 → 生成 INIT_BALANCE 期初单 → 审核（落库存事务）。
     *
     * <p>证据表（D7）：每行同时记录**上传原值**（`qtyInput`/`priceInput`，前端上传时带回）与
     * **人工确认值**（`数量`/`主单价`），并标记是否被改过，事后可逐行对账。
     *
     * @param rows 每行含 仓库/存货编码/存货名称/规格型号/计量单位/数量/主单价，
     *             以及可选的 qtyInput / priceInput（上传原值）
     */
    Map<String, Object> applyOpening(List<Map<String, String>> rows, String batchNo,
                                     String fileName, String operator) {
        if (rows == null || rows.isEmpty()) throw new IllegalArgumentException("没有可写入的期初库存行");
        String initNo = "INIT-" + LocalDateTime.now().format(java.time.format.DateTimeFormatter
                .ofPattern("yyyyMMdd-HHmmss"));
        List<Map<String, Object>> items = new ArrayList<>();
        double totalAmount = 0;
        for (Map<String, String> row : rows) {
            String warehouse = value(row, "仓库");
            String code = value(row, "存货编码");
            if (warehouse.isEmpty() || code.isEmpty()) continue;
            double qty = number(value(row, "数量"), 0);
            if (qty == 0) continue;
            double price = number(value(row, "主单价"), 0);
            double amount = Math.round(qty * price * 100) / 100.0;
            totalAmount += amount;
            items.add(new LinkedHashMap<>(Map.of(
                    "存货编码", code,
                    "存货名称", value(row, "存货名称"),
                    "规格型号", value(row, "规格型号"),
                    "计量单位", value(row, "计量单位"),
                    "数量", qty,
                    "主单价", price,
                    "金额", amount,
                    "仓库", warehouse)));
        }
        if (items.isEmpty()) throw new IllegalArgumentException("期初库存没有有效行（数量为 0 或缺少仓库/存货编码）");

        Map<String, Object> head = new LinkedHashMap<>();
        head.put("期初余额单号", initNo);
        head.put("单据日期", LocalDateTime.now().toLocalDate().toString());
        head.put("仓库", value(rows.get(0), "仓库"));
        head.put("备注", "系统初始化导入（批次 " + batchNo + "）");
        Map<String, Object> detail = new LinkedHashMap<>();
        detail.put("items", items);
        insertDoc("INIT_BALANCE", initNo, head, detail, operator);

        // 审核：期初必须真正进库存，否则开始用系统时余额是空的
        if (stockTxnService != null) {
            stockTxnService.post("INIT_BALANCE", initNo, head, detail);
        }

        for (Map<String, String> row : rows) {
            String code = value(row, "存货编码");
            if (code.isEmpty()) continue;
            double qty = number(value(row, "数量"), 0);
            if (qty == 0) continue;
            double price = number(value(row, "主单价"), 0);
            double qtyInput = number(value(row, "qtyInput"), qty);
            double priceInput = number(value(row, "priceInput"), price);
            boolean changed = Math.abs(qty - qtyInput) > 1e-9 || Math.abs(price - priceInput) > 1e-9
                    || !value(row, "仓库").equals(value(row, "warehouseInput").isEmpty()
                    ? value(row, "仓库") : value(row, "warehouseInput"));
            jdbc.update("""
                    insert into init_balance_evidence (batch_no, source_file, source_row,
                        item_code, item_name, spec, unit,
                        warehouse_input, warehouse_confirmed, qty_input, qty_confirmed,
                        price_input, price_confirmed, amount_confirmed, changed,
                        init_balance_no, operator, confirmed_at)
                    values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,now())
                    """, batchNo, fileName, lineOf(row),
                    code, value(row, "存货名称"), value(row, "规格型号"), value(row, "计量单位"),
                    value(row, "warehouseInput").isEmpty() ? value(row, "仓库") : value(row, "warehouseInput"),
                    value(row, "仓库"), qtyInput, qty, priceInput, price,
                    Math.round(qty * price * 100) / 100.0, changed, initNo, operator);
        }

        Map<String, Object> out = new LinkedHashMap<>();
        out.put("initBalanceNo", initNo);
        out.put("rowCount", items.size());
        out.put("amountTotal", Math.round(totalAmount * 100) / 100.0);
        out.put("message", "期初库存已写入并审核，库存事务已生成");
        return out;
    }

    private static int lineOf(Map<String, String> row) {
        String raw = value(row, "__row");
        try {
            return Integer.parseInt(raw);
        } catch (NumberFormatException e) {
            return 0;
        }
    }

    // ───────────────────────── 共用 ─────────────────────────

    private FormData archiveDoc(String panelCode, String formNo) {
        FormData doc = formDoc(panelCode, formNo);
        if (doc != null) return doc;
        FormData created = new FormData();
        created.setPanelCode(panelCode);
        created.setFormNo(formNo);
        created.setData("{}");
        created.setDetailData("{}");
        created.setStatus(ARCHIVE_STATUS);
        created.setCreateTime(LocalDateTime.now());
        created.setUpdateTime(LocalDateTime.now());
        formMapper.insert(created);
        return formDoc(panelCode, formNo);
    }

    private FormData formDoc(String panelCode, String formNo) {
        return formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, panelCode)
                .eq(FormData::getFormNo, formNo));
    }

    /** 老数据可能用 ROUT-xxxx 当单号、业务编码在表头里，这里按表头再找一次。 */
    private FormData routeDocByCode(String code) {
        for (FormData doc : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "ROUTE"))) {
            if (code.equals(String.valueOf(parse(doc.getData()).getOrDefault("工艺路线编码", "")).trim())) {
                return doc;
            }
        }
        return null;
    }

    private void saveDetail(FormData doc, Map<String, Object> detail, String operator) {
        doc.setDetailData(toJson(detail));
        doc.setUpdateTime(LocalDateTime.now());
        formMapper.updateById(doc);
    }

    private void insertDoc(String panelCode, String formNo, Map<String, Object> head,
                           Map<String, Object> detail, String operator) {
        FormData doc = new FormData();
        doc.setPanelCode(panelCode);
        doc.setFormNo(formNo);
        doc.setData(toJson(head));
        doc.setDetailData(toJson(detail));
        doc.setStatus(DOC_STATUS);
        doc.setCreateBy(operator);
        doc.setCreateTime(LocalDateTime.now());
        doc.setUpdateTime(LocalDateTime.now());
        doc.setAuditBy(operator);
        doc.setAuditTime(LocalDateTime.now());
        formMapper.insert(doc);
        log.info("初始化落库：{} {} 由 {}", panelCode, formNo, operator);
    }

    private String processNameOf(String code) {
        FormData op = formDoc("OP", OP_DOC_NO);
        if (op == null) return "";
        for (Map<String, Object> row : rowsOf(parse(op.getDetailData()), "operations")) {
            if (code.equals(String.valueOf(row.get("工序编码")).trim())) {
                return String.valueOf(row.getOrDefault("工序名称", ""));
            }
        }
        return "";
    }

    private String productNameOf(String code) {
        return itemNameOf(code);
    }

    private String itemNameOf(String code) {
        return itemField(code, "存货名称");
    }

    private String itemSpecOf(String code) {
        return itemField(code, "规格型号");
    }

    private String itemUnitOf(String code) {
        return itemField(code, "计量单位");
    }

    private String itemField(String code, String field) {
        FormData inv = formDoc("INV", INV_DOC_NO);
        if (inv == null) return "";
        for (Map<String, Object> row : rowsOf(parse(inv.getDetailData()), "items")) {
            if (code.equals(String.valueOf(row.get("存货编码")).trim())) {
                return String.valueOf(row.getOrDefault(field, ""));
            }
        }
        return "";
    }

    private String routeOfProduct(String product) {
        FormData inv = formDoc("INV", INV_DOC_NO);
        if (inv == null) return "";
        for (Map<String, Object> row : rowsOf(parse(inv.getDetailData()), "items")) {
            if (product.equals(String.valueOf(row.get("存货编码")).trim())) {
                return String.valueOf(row.getOrDefault("工艺路线", ""));
            }
        }
        return "";
    }

    private String routeNameOf(String code) {
        if (code.isEmpty()) return "";
        for (FormData doc : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "ROUTE"))) {
            Map<String, Object> head = parse(doc.getData());
            if (code.equals(String.valueOf(head.getOrDefault("工艺路线编码", "")).trim())) {
                return String.valueOf(head.getOrDefault("工艺路线名称", ""));
            }
        }
        return "";
    }

    private Map<String, Object> parse(String raw) {
        if (raw == null || raw.isBlank()) return new LinkedHashMap<>();
        try {
            return json.readValue(raw, new TypeReference<Map<String, Object>>() { });
        } catch (Exception e) {
            return new LinkedHashMap<>();
        }
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> rowsOf(Map<String, Object> detail, String key) {
        Object value = detail.get(key);
        if (value instanceof List<?> list) {
            List<Map<String, Object>> out = new ArrayList<>();
            for (Object row : list) {
                if (row instanceof Map<?, ?> map) out.add((Map<String, Object>) map);
            }
            return out;
        }
        return new ArrayList<>();
    }

    private String toJson(Object value) {
        try {
            return json.writeValueAsString(value);
        } catch (Exception e) {
            throw new IllegalStateException("初始化落库序列化失败", e);
        }
    }

    private static Map<String, Object> item(String target, String key, String action, String message) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("target", target);
        out.put("key", key);
        out.put("action", action);
        out.put("message", message);
        return out;
    }

    private static String value(Map<String, String> row, String key) {
        String v = row == null ? null : row.get(key);
        return v == null ? "" : v.trim();
    }

    private static double number(String raw, double fallback) {
        String v = raw == null ? "" : raw.trim();
        if (v.isEmpty()) return fallback;
        try {
            return Double.parseDouble(v);
        } catch (NumberFormatException e) {
            return fallback;
        }
    }

    private static int intOf(Map<String, String> row, String key, int fallback) {
        return (int) number(value(row, key), fallback);
    }
}