package com.mes.controller;

import com.mes.dto.ApiResult;
import com.mes.ocr.OcrScanResponse;
import com.mes.service.OcrScanService;
import org.springframework.http.MediaType;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestPart;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

@RestController
@RequestMapping("/api/ocr")
public class OcrController {

    private final OcrScanService service;

    public OcrController(OcrScanService service) {
        this.service = service;
    }

    @PostMapping(value = "/scan-form", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    public ApiResult<OcrScanResponse> scanForm(
            @RequestPart("image") MultipartFile image,
            @RequestParam("panelCode") String panelCode,
            Authentication authentication) {
        String userName = authentication == null ? "" : authentication.getName();
        return ApiResult.ok(service.scan(panelCode, image, userName));
    }
}
