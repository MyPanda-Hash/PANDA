package com.mes.controller;

import com.mes.service.PanelxService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;

/**
 * PanelX 后端代理接口。
 * 注意：与 /api/px/*（本地引擎）不同，这里直接返回平台 {state,msg,data} 封装，
 * 不包 ApiResult —— 前端引擎 platformCall 按该封装判断 state，与 SDK 直连保持一致。
 */
@RestController
@RequestMapping("/api/panelx")
public class PanelxController {

    private final PanelxService service;

    public PanelxController(PanelxService service) {
        this.service = service;
    }

    @GetMapping("/getPanelConfig")
    public Map<String, Object> getPanelConfig(@RequestParam String panelCode) {
        return service.getPanelConfig(panelCode);
    }

    @GetMapping("/getPermMatrix")
    public Map<String, Object> getPermMatrix(@RequestParam String panelCode) {
        return service.getPermMatrix(panelCode);
    }

    @GetMapping("/getNewFormPermMatrix")
    public Map<String, Object> getNewFormPermMatrix(@RequestParam String panelCode,
                                                    @RequestParam(required = false) String operationName) {
        return service.getNewFormPermMatrix(panelCode, operationName);
    }

    @GetMapping("/getFormDescriptor")
    public Map<String, Object> getFormDescriptor(@RequestParam String panelCode,
                                                 @RequestParam String code) {
        return service.getFormDescriptor(panelCode, code);
    }

    @PostMapping("/queryFormDataList")
    public Map<String, Object> queryFormDataList(@RequestBody Map<String, Object> body) {
        return service.queryFormDataList(body);
    }

    @PostMapping("/callButton")
    public Map<String, Object> callButton(@RequestBody Map<String, Object> body) {
        String panelCode = String.valueOf(body.getOrDefault("panelCode", ""));
        String buttonName = String.valueOf(body.getOrDefault("buttonName", ""));
        @SuppressWarnings("unchecked")
        Map<String, Object> formData = (Map<String, Object>) body.getOrDefault("formData", Map.of());
        @SuppressWarnings("unchecked")
        Map<String, Object> buttonParam = (Map<String, Object>) body.getOrDefault("buttonParam", Map.of());
        return service.callButton(panelCode, buttonName, formData, buttonParam);
    }

    @PostMapping("/deleteForms")
    public Map<String, Object> deleteForms(@RequestBody Map<String, Object> body) {
        String panelCode = String.valueOf(body.getOrDefault("panelCode", ""));
        @SuppressWarnings("unchecked")
        List<String> rowCodes = (List<String>) body.getOrDefault("rowCodes", List.of());
        return service.deleteForms(panelCode, rowCodes);
    }
}