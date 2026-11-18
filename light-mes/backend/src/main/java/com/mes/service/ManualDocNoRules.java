package com.mes.service;

import java.util.Map;
import java.util.function.Predicate;

/**
 * 手工单据号：勾了「是否手工修改单据编码」后，单号按用户填的用，不套编码规则，但必须唯一。
 *
 * <p>目前只对生产加工单开放（加工单既可以由销售订单排产生成，也可以预生产手工建单，
 * 手工建单常带内部编号习惯）。默认仍走 {@code generateFormNo}，行为不变。
 *
 * <p>规则集中在这里，不碰数据库：是否已存在由调用方传进来的判定函数回答，便于单测。
 */
public final class ManualDocNoRules {

    /** 当前开放手工编号的面板 */
    private static final String PANEL_MANUFACTURE_ORDER = "MANU_ORDER";

    private ManualDocNoRules() {
    }

    /**
     * 求本次新建/保存单据的最终单号。
     *
     * @param panelCode 面板编码
     * @param formData  表头数据（读「是否手工修改单据编码」和已填的单号）
     * @param typed     用户在「加工单号」里填的值
     * @param generated 系统生成的候选单号
     * @param exists    单号是否已被占用
     * @return 最终单号；勾了手工修改且填了值且不重复时返回填的值
     */
    public static String resolve(String panelCode, Map<String, Object> formData, String typed,
                                 String generated, Predicate<String> exists) {
        String manual = typed == null ? "" : typed.trim();
        if (!PANEL_MANUFACTURE_ORDER.equals(panelCode) || manual.isEmpty() || !manualEdit(formData)) {
            return generated;
        }
        if (exists != null && exists.test(manual)) {
            throw new IllegalStateException("生产加工单号已存在：" + manual);
        }
        return manual;
    }

    /** 「是否手工修改单据编码」是否打开（兼容 是/否 文本与布尔两种存法）。 */
    public static boolean manualEdit(Map<String, Object> formData) {
        if (formData == null) return false;
        Object raw = formData.get("是否手工修改单据编码");
        if (raw instanceof Boolean flag) return flag;
        String text = raw == null ? "" : String.valueOf(raw).trim();
        return "是".equals(text) || "true".equalsIgnoreCase(text) || "1".equals(text);
    }
}
