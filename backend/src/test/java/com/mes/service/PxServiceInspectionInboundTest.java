package com.mes.service;

import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 检验单的入库方向：来料与委外回厂都从到货单来，合格可转采购入库或委外入库；
 * 产成品入库不在这张单的职能里（成品走成品报检单）。
 */
class PxServiceInspectionInboundTest {

    private List<Map<String, Object>> arrivalRows() {
        return List.of(Map.of("来源面板", "ARRIVAL_IN", "来源单号", "DH-001", "存货编码", "CL001"));
    }

    @Test
    void arrivalInspectionFollowsSourceType() {
        // 来源类型跟随到货单：采购到货只能转采购入库，委外回厂只能转委外入库（防止生错入库单）
        assertDoesNotThrow(() -> PxService.requireInspectionInboundTarget(
                "INSPECTION", "PURCHASE_IN", Map.of("业务类型", "来料检验", "来源类型", "采购"), arrivalRows()));
        assertDoesNotThrow(() -> PxService.requireInspectionInboundTarget(
                "INSPECTION", "OUTSOURCE_IN", Map.of("业务类型", "来料检验", "来源类型", "委外"), arrivalRows()));
        assertThrows(IllegalStateException.class, () -> PxService.requireInspectionInboundTarget(
                "INSPECTION", "OUTSOURCE_IN", Map.of("业务类型", "来料检验", "来源类型", "采购"), arrivalRows()));
    }

    @Test
    void inspectionNeverProducesFinishedGoodsReceipt() {
        IllegalStateException e = assertThrows(IllegalStateException.class,
                () -> PxService.requireInspectionInboundTarget(
                        "INSPECTION", "FINISH_IN", Map.of("业务类型", "来料检验"), arrivalRows()));
        assertTrue(e.getMessage().contains("成品报检单"), "提示要指向成品报检单");
    }

    @Test
    void finishedInspectionCannotBecomePurchaseOrOutsourceReceipt() {
        List<Map<String, Object>> rows = List.of(Map.of("来源面板", "FINISH_INSPECT", "来源单号", "BJ-001"));
        assertThrows(IllegalStateException.class, () -> PxService.requireInspectionInboundTarget(
                "INSPECTION", "PURCHASE_IN", Map.of("业务类型", "成品检验"), rows));
    }

    @Test
    void otherPanelsAreNotGatedHere() {
        assertDoesNotThrow(() -> PxService.requireInspectionInboundTarget(
                "PROCESS_INSPECTION", "FINISH_IN", Map.of(), List.of()));
    }
}
