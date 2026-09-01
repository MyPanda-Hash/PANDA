package com.mes.controller;

import com.mes.dto.ApiResult;
import com.mes.i18n.TranslationService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;

/**
 * 界面语言与词条字典（2026-09-01 阶段 A）：前端 tt() 显示层翻译的数据源。
 * zh 源语言直接短路返回空字典（无需翻译）。
 */
@RestController
@RequestMapping("/api/locale")
public class LocaleController {

    private final TranslationService translationService;

    public LocaleController(TranslationService translationService) {
        this.translationService = translationService;
    }

    @GetMapping("/list")
    public ApiResult<List<Map<String, String>>> list() {
        return ApiResult.ok(translationService.listLocales());
    }

    @PostMapping("/dict")
    public ApiResult<Map<String, String>> dict(@RequestBody Map<String, Object> body) {
        String locale = body.get("locale") == null ? "" : String.valueOf(body.get("locale"));
        @SuppressWarnings("unchecked")
        List<String> keys = (List<String>) body.getOrDefault("keys", List.of());
        return ApiResult.ok(translationService.dict(locale, keys));
    }
}
