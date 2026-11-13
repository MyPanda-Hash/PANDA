package com.mes.init;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 岗位 → 面板权限模板（决策：给名单就自动配好账号权限）。
 *
 * <p>按「职务 / 岗位职责」里的关键词归类，给出该岗位应得的默认权限。原则：
 * <ul>
 *   <li>默认只给本职能面板的 查看/查询/新增/修改；</li>
 *   <li><b>删除、价格、调整</b>只给主管与财务——给多了是数据事故，不是效率；</li>
 *   <li>审批（audit）只给含「主管/经理/主任/审核/审批」的岗位；</li>
 *   <li>初始化**永不生成管理员**（管理员保护在 InitService 拦截，这里也不产出 is_admin）。</li>
 * </ul>
 */
public final class InitRoleTemplates {

    /** 一次权限授予：面板编码 + 权限动作（12 动作里的子集）。 */
    public record Grant(String panelCode, List<String> perms) {
    }

    /** 岗位归类：职务或职责命中关键词即采用该模板。 */
    public enum Role {
        QC("质检", List.of("质检员", "检验", "品检", "品质", "qc")),
        WMS("仓管", List.of("仓管", "库管", "仓库", "仓储", "物流", "配货")),
        PURCHASE("采购", List.of("采购", "供应", "外协")),
        SALES("销售", List.of("销售", "业务", "客户经理", "接单")),
        PLANNING("计划", List.of("计划", "排产", "调度", "生产计划")),
        PRODUCTION("生产", List.of("车间", "生产", "工艺", "设备", "班组长", "工人", "操作")),
        FINANCE("财务", List.of("财务", "会计", "出纳", "成本", "审计")),
        GENERAL("通用", List.of());

        final String label;
        final List<String> keywords;

        Role(String label, List<String> keywords) {
            this.label = label;
            this.keywords = keywords;
        }

        public String label() {
            return label;
        }
    }

    private InitRoleTemplates() {
    }

    /** 按职务/职责归类；都不命中给「通用」（只读几个主流程面板）。 */
    public static Role classify(String title, String duties) {
        String text = ((title == null ? "" : title) + " " + (duties == null ? "" : duties)).toLowerCase();
        for (Role role : Role.values()) {
            if (role == Role.GENERAL) continue;
            for (String keyword : role.keywords) {
                if (text.contains(keyword.toLowerCase())) return role;
            }
        }
        return Role.GENERAL;
    }

    /** 是否管理类岗位（给审批权）：主管/经理/主任/科长/负责人/审核/审批。 */
    public static boolean isSupervisor(String title) {
        String t = title == null ? "" : title;
        for (String keyword : List.of("主管", "经理", "主任", "科长", "负责人", "审核", "审批", "厂长", "总监")) {
            if (t.contains(keyword)) return true;
        }
        return false;
    }

    /** 该岗位的默认授权。 */
    public static Map<String, List<String>> grants(Role role, boolean supervisor) {
        List<String> rc = supervisor
                ? List.of("view", "query", "add", "edit", "print", "audit")
                : List.of("view", "query", "add", "edit", "print");
        Map<String, List<String>> out = new LinkedHashMap<>();
        switch (role) {
            case QC -> {
                for (String p : List.of("INSPECTION", "FIRST_INSPECT", "FINISH_INSPECT",
                        "OUTSOURCE_INSPECTION", "PROCESS_INSPECTION", "ARRIVAL_IN", "QC_ITEM")) {
                    out.put(p, rc);
                }
            }
            case WMS -> {
                for (String p : List.of("STOCK_STATUS", "STOCK_LEDGER", "MATERIAL_OUT", "OTHER_IN",
                        "OTHER_OUT", "TRANSFER", "STOCK_CHECK", "LOCATION_ADJUST", "PICK_ORDER")) {
                    out.put(p, supervisor
                            ? List.of("view", "query", "add", "edit", "print", "adjust", "audit")
                            : List.of("view", "query", "add", "edit", "print"));
                }
            }
            case PURCHASE -> {
                for (String p : List.of("PU_REQ", "PU_ORDER", "ARRIVAL_IN", "PURCHASE_IN",
                        "PURCHASE_RETURN", "PU_INVOICE", "PARTNER")) {
                    out.put(p, supervisor
                            ? List.of("view", "query", "add", "edit", "print", "audit", "price")
                            : List.of("view", "query", "add", "edit", "print"));
                }
            }
            case SALES -> {
                for (String p : List.of("QUOTE_ORDER", "SO_ORDER", "SALE_OUT", "SALE_INV",
                        "SALE_INVOICE", "SALE_RETURN", "PARTNER")) {
                    out.put(p, supervisor
                            ? List.of("view", "query", "add", "edit", "print", "audit", "price")
                            : List.of("view", "query", "add", "edit", "print"));
                }
            }
            case PLANNING -> {
                for (String p : List.of("SO_ORDER", "MANU_ORDER", "MPS_PLAN", "PU_REQ_ANALYSIS",
                        "DISPATCH", "BOM", "ROUTE")) {
                    out.put(p, supervisor
                            ? List.of("view", "query", "add", "edit", "print", "audit")
                            : List.of("view", "query", "add", "edit", "print"));
                }
            }
            case PRODUCTION -> {
                for (String p : List.of("MANU_ORDER", "DISPATCH", "PROCESS_REPORT", "MATERIAL_REQ",
                        "MATERIAL_OUT", "FINISH_IN", "FIRST_INSPECT", "BOM", "ROUTE", "OP")) {
                    out.put(p, supervisor
                            ? List.of("view", "query", "add", "edit", "print", "audit")
                            : List.of("view", "query", "add", "edit"));
                }
            }
            case FINANCE -> {
                for (String p : List.of("RECEIPT", "RECEIPT_PAYABLE", "EXPENSE", "SALE_INVOICE",
                        "PU_INVOICE", "ACCOUNTING_ENTRY", "ACCOUNT_BALANCE", "SETTLE_CUSTOMER",
                        "SETTLE_SUPPLIER")) {
                    out.put(p, supervisor
                            ? List.of("view", "query", "add", "edit", "print", "export", "audit")
                            : List.of("view", "query", "add", "edit", "print", "export"));
                }
            }
            case GENERAL -> {
                for (String p : List.of("SO_ORDER", "MANU_ORDER", "STOCK_STATUS")) {
                    out.put(p, List.of("view", "query"));
                }
            }
        }
        return out;
    }

    /** 角色编码：由岗位标签 + 是否主管生成稳定编码（重复初始化不会建重名角色）。 */
    public static String roleCode(Role role, boolean supervisor) {
        return "INIT_R_" + role.name() + (supervisor ? "_LEAD" : "");
    }

    /** 角色名称：岗位标签 + 主管后缀。 */
    public static String roleName(Role role, boolean supervisor) {
        return role.label() + (supervisor ? "主管" : "");
    }
}