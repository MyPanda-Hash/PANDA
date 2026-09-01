package com.mes.ocr;

import java.util.List;
import java.util.Map;

public record OcrScanResponse(
        String requestId,
        Map<String, Object> header,
        Map<String, List<Map<String, Object>>> detail,
        List<Map<String, Object>> matches,
        List<String> warnings) {
}
