package com.mes.dc;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.time.LocalDateTime;
import java.util.LinkedHashMap;
import java.util.Map;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

/**
 * 数采平台的判定口径：在线窗口、越限、串口帧取重、保留位。
 */
class DcRulesTest {

    private static Map<String, Object> device(Integer heartbeatSeconds, LocalDateTime lastSeen, boolean enabled) {
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("heartbeat_seconds", heartbeatSeconds);
        row.put("last_seen_at", lastSeen);
        row.put("enabled", enabled);
        return row;
    }

    @Test
    @DisplayName("离线窗口 = 心跳 × 3，且不低于 180 秒")
    void offline_window_has_floor() {
        assertEquals(180, DcService.offlineWindow(device(60, null, true)));
        assertEquals(180, DcService.offlineWindow(device(5, null, true)));
        assertEquals(1800, DcService.offlineWindow(device(600, null, true)));
        assertEquals(180, DcService.offlineWindow(device(null, null, true)));
        assertEquals(180, DcService.offlineWindow(device(0, null, true)));
    }

    @Test
    @DisplayName("在窗口内算在线，超出算离线；停用设备一律离线")
    void online_follows_last_seen() {
        assertTrue(DcService.isOnline(device(60, LocalDateTime.now().minusSeconds(100), true)));
        assertFalse(DcService.isOnline(device(60, LocalDateTime.now().minusSeconds(200), true)));
        assertFalse(DcService.isOnline(device(60, LocalDateTime.now(), false)));
        assertFalse(DcService.isOnline(device(60, null, true)));
    }

    @Test
    @DisplayName("越限只在有数值且有限值一侧时判定")
    void over_limit_rules() {
        assertTrue(DcService.isOver(10, 20, 5));
        assertTrue(DcService.isOver(10, 20, 25));
        assertFalse(DcService.isOver(10, 20, 15));
        assertFalse(DcService.isOver(null, null, 5));
        assertFalse(DcService.isOver(10, 20, "文本值"));
        assertFalse(DcService.isOver(10, 20, null));
    }

    @Test
    @DisplayName("串口帧取重：现场格式、带前缀、整数、无解")
    void weight_parse() {
        assertEquals(0.39, DcController.parseWeightText("+000.39000.00kg"), 1e-9);
        assertEquals(0.52, DcController.parseWeightText("ST,GS,+ 0.520kg"), 1e-9);
        assertEquals(0.52, DcController.parseWeightText("wn+00.520kg"), 1e-9);
        assertEquals(12.0, DcController.parseWeightText("+12kg"), 1e-9);
        assertNull(DcController.parseWeightText("no digits here"));
        assertNull(DcController.parseWeightText(""));
    }

    @Test
    @DisplayName("在线率保留一位小数")
    void round_one_decimal() {
        assertEquals(66.7, DcService.round1(66.666));
        assertEquals(100.0, DcService.round1(100.0));
        assertEquals(0.0, DcService.round1(0.0));
    }
}
