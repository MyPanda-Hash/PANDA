package com.mes.init;

import java.util.List;

/**
 * 初始化模块：七个可分批上传的模块（2026-11-13 用户口径 D10）。
 *
 * <p>每个模块自带模板列（首行表头）与落库目标。模板由服务端用 POI 生成，
 * 客户填完上传，因此「列名」是前后端唯一的契约——改这里就等于改模板，
 * 不要在两处各写一份列名。
 *
 * <p>顺序即落库顺序：部门 → 员工 → 存货 → 工序 → 路线 → BOM → 期初；
 * 角色与账号由员工模块里的「岗位职责 / 登录账号」带出，在员工之后落库。
 */
public enum InitModule {

    DEPT("dept", "组织架构", "部门编码", "部门名称", "上级部门编码", "排序"),
    EMPLOYEE("employee", "员工信息", "员工编码", "姓名", "所属部门编码", "职务",
            "手机", "岗位职责", "登录账号"),
    ITEM("item", "存货", "存货编码", "存货名称", "规格型号", "计量单位",
            "计价方式", "参考成本", "所属类别", "工艺路线"),
    PROCESS("process", "工序", "工序编码", "工序名称", "默认车间",
            "标准合格率%", "加工方式", "计件依据"),
    ROUTING("routing", "工艺路线", "工艺路线编码", "工艺路线名称", "工序顺序",
            "工序编码", "工资类型", "委外供应商"),
    BOM("bom", "BOM 表", "产品编码", "子件编码", "定额数量", "生产数量",
            "损耗率%", "预入仓库"),
    OPENING_STOCK("openingStock", "期初库存", "仓库", "存货编码", "存货名称",
            "规格型号", "计量单位", "数量", "主单价");

    private final String code;
    private final String label;
    private final List<String> headers;

    InitModule(String code, String label, String... headers) {
        this.code = code;
        this.label = label;
        this.headers = List.of(headers);
    }

    public String code() {
        return code;
    }

    public String label() {
        return label;
    }

    public List<String> headers() {
        return headers;
    }

    /** 模板文件名：客户下载后改个名字也能认出来。 */
    public String fileName() {
        return switch (this) {
            case DEPT -> "组织架构.xlsx";
            case EMPLOYEE -> "员工信息.xlsx";
            case ITEM -> "存货.xlsx";
            case PROCESS -> "工序.xlsx";
            case ROUTING -> "工艺路线.xlsx";
            case BOM -> "BOM表.xlsx";
            case OPENING_STOCK -> "期初库存.xlsx";
        };
    }

    /** 该模块的前置模块：前置未完成时界面置灰，服务端也拒绝落库。 */
    public List<InitModule> requires() {
        return switch (this) {
            case DEPT, PROCESS -> List.of();
            case EMPLOYEE, ITEM -> List.of(DEPT);
            case ROUTING -> List.of(PROCESS);
            case BOM -> List.of(ITEM, ROUTING);
            case OPENING_STOCK -> List.of(ITEM);
        };
    }

    /** 走「核对 + 证据表」的模块（期初库存）：不能直接 apply，必须走 confirm。 */
    public boolean needsConfirmation() {
        return this == OPENING_STOCK;
    }

    public static InitModule of(String raw) {
        String key = raw == null ? "" : raw.trim();
        for (InitModule m : values()) {
            if (m.code.equalsIgnoreCase(key) || m.name().equalsIgnoreCase(key) || m.label.equals(key)) return m;
        }
        throw new IllegalArgumentException("未知初始化模块：" + raw);
    }
}