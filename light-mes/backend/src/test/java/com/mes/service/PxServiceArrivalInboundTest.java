package com.mes.service;

import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 到货单的来源类型（采购 / 委外）决定它能生成哪种入库单，防止生错单。
 * 老数据没有这个字段时按「采购」处理。
 */
class PxServiceArrivalInboundTest {

    @Test
    void purchaseArrivalOnlyMakesPurchaseReceipt() {
        Map<String, Object> head = Map.of("来源类型", "采购");
        assertDoesNotThrow(() -> PxService.requireArrivalInboundTarget(head, "PURCHASE_IN", "生成采购入库单"));
        IllegalStateException e = assertThrows(IllegalStateException.class,
                () -> PxService.requireArrivalInboundTarget(head, "OUTSOURCE_IN", "生成委外入库单"));
        assertTrue(e.getMessage().contains("采购到货"), e.getMessage());
    }

    @Test
    void outsourceArrivalOnlyMakesOutsourceReceipt() {
        Map<String, Object> head = Map.of("来源类型", "委外");
        assertDoesNotThrow(() -> PxService.requireArrivalInboundTarget(head, "OUTSOURCE_IN", "生成委外入库单"));
        IllegalStateException e = assertThrows(IllegalStateException.class,
                () -> PxService.requireArrivalInboundTarget(head, "PURCHASE_IN", "生成采购入库单"));
        assertTrue(e.getMessage().contains("委外回厂"), e.getMessage());
    }

    @Test
    void legacyArrivalWithoutFlagIsTreatedAsPurchase() {
        assertDoesNotThrow(() -> PxService.requireArrivalInboundTarget(Map.of(), "PURCHASE_IN", "生成采购入库单"));
        assertThrows(IllegalStateException.class,
                () -> PxService.requireArrivalInboundTarget(Map.of(), "OUTSOURCE_IN", "生成委外入库单"));
    }

    @Test
    void inspectionFollowsArrivalSourceType() {
        List<Map<String, Object>> rows = List.of(Map.of("来源面板", "ARRIVAL_IN", "来源单号", "DH-001"));
        // 委外回厂的检验单只能转委外入库
        assertDoesNotThrow(() -> PxService.requireInspectionInboundTarget(
                "INSPECTION", "OUTSOURCE_IN", Map.of("来源类型", "委外"), rows));
        IllegalStateException e = assertThrows(IllegalStateException.class,
                () -> PxService.requireInspectionInboundTarget(
                        "INSPECTION", "PURCHASE_IN", Map.of("来源类型", "委外"), rows));
        assertTrue(e.getMessage().contains("委外"), e.getMessage());
        // 采购到货的检验单只能转采购入库
        assertDoesNotThrow(() -> PxService.requireInspectionInboundTarget(
                "INSPECTION", "PURCHASE_IN", Map.of("来源类型", "采购"), rows));
        assertThrows(IllegalStateException.class, () -> PxService.requireInspectionInboundTarget(
                "INSPECTION", "OUTSOURCE_IN", Map.of("来源类型", "采购"), rows));
    }
}
