package com.mes.service;

import org.junit.jupiter.api.Test;

import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

/**
 * 手工填写单据号：勾了「是否手工修改单据编码」就按填的用，不套编码规则，但必须唯一。
 */
class ManualDocNoRulesTest {

    private static boolean existsSomething(String no) {
        return "更".equals(no);
    }

    @Test
    void 没勾手工修改时用系统生成的单号() {
        String no = ManualDocNoRules.resolve("MANU_ORDER",
                Map.of("是否手工修改单据编码", "否"), "我填的单号", "MO-20261111-001", s -> false);
        assertEquals("MO-20261111-001", no);
    }

    @Test
    void 勾了手工修改就用填的号且不套编码规则() {
        String no = ManualDocNoRules.resolve("MANU_ORDER",
                Map.of("是否手工修改单据编码", "是"), " 试产单A ", "MO-20261111-001", s -> false);
        assertEquals("试产单A", no);
    }

    @Test
    void 勾了但没填就退回系统生成() {
        String no = ManualDocNoRules.resolve("MANU_ORDER",
                Map.of("是否手工修改单据编码", "是"), "", "MO-20261111-001", s -> false);
        assertEquals("MO-20261111-001", no);
    }

    @Test
    void 填的号已存在要报错不能覆盖() {
        IllegalStateException e = assertThrows(IllegalStateException.class,
                () -> ManualDocNoRules.resolve("MANU_ORDER",
                        Map.of("是否手工修改单据编码", "是"), "更", "MO-20261111-001", s -> true));
        assertEquals("生产加工单号已存在：更", e.getMessage());
    }

    @Test
    void 布尔型开关也认() {
        String no = ManualDocNoRules.resolve("MANU_ORDER",
                Map.of("是否手工修改单据编码", true), "手写-01", "MO-20261111-001", s -> false);
        assertEquals("手写-01", no);
    }

    @Test
    void 其他面板不受影响() {
        String no = ManualDocNoRules.resolve("SO_ORDER",
                Map.of("是否手工修改单据编码", "是"), "随便填", "SO-20261111-001", s -> false);
        assertEquals("SO-20261111-001", no);
    }
}
