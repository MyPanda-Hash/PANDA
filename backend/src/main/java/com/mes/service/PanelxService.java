package com.mes.service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.config.PanelxProperties;
import org.springframework.stereotype.Service;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * PanelX 后端代理：把本地面板码映射为平台面板码后，
 * 以服务端身份调用平台接口，原样返回平台 {state,msg,data} 封装。
 * 前端引擎（VITE_PANELX_PROXY=true）通过 /api/panelx/* 消费，
 * 与浏览器 SDK 直连模式共用同一套适配逻辑。
 */
@Service
public class PanelxService {

    private final PanelxGateway gateway;
    private final PanelxProperties props;
    private final ObjectMapper mapper;

    public PanelxService(PanelxGateway gateway, PanelxProperties props, ObjectMapper mapper) {
        this.gateway = gateway;
        this.props = props;
        this.mapper = mapper;
    }

    public Map<String, Object> getPanelConfig(String panelCode) {
        Map<String, Object> q = new LinkedHashMap<>();
        q.put("busDomainCode", props.getBusDomainCode());
        q.put("panelCode", gateway.resolveCode(panelCode));
        Map<String, Object> res = gateway.get("wp-core/api/cdp/getPanelConfig", q);
        // 平台把面板配置以 JSON 字符串放在 data 里（SDK 会解析），这里还原为对象
        Object data = res.get("data");
        if (data instanceof String s && !s.isBlank()) {
            try {
                res.put("data", mapper.readValue(s, new TypeReference<Map<String, Object>>() {}));
            } catch (Exception ignore) {
                // 解析失败保留原始字符串
            }
        }
        return res;
    }

    public Map<String, Object> getPermMatrix(String panelCode) {
        Map<String, Object> q = new LinkedHashMap<>();
        q.put("panelCode", gateway.resolveCode(panelCode));
        return gateway.get("wp-core/api/permMatrix", q);
    }

    public Map<String, Object> getNewFormPermMatrix(String panelCode, String operationName) {
        Map<String, Object> q = new LinkedHashMap<>();
        q.put("panelCode", gateway.resolveCode(panelCode));
        if (operationName != null && !operationName.isBlank()) {
            q.put("operationName", operationName);
        }
        return gateway.get("wp-core/api/newFormPermMatrix", q);
    }

    public Map<String, Object> getFormDescriptor(String panelCode, String code) {
        Map<String, Object> q = new LinkedHashMap<>();
        q.put("panelCode", gateway.resolveCode(panelCode));
        q.put("code", code);
        return gateway.get("wp-core/api/formDescriptor", q);
    }

    public Map<String, Object> queryFormDataList(Map<String, Object> params) {
        Map<String, Object> body = new LinkedHashMap<>(params);
        body.put("panelCode", gateway.resolveCode(String.valueOf(body.getOrDefault("panelCode", ""))));
        return gateway.post("wp-core/api/queryFormDataList", body);
    }

    public Map<String, Object> callButton(String panelCode, String buttonName,
                                          Map<String, Object> formData, Map<String, Object> buttonParam) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("panelCode", gateway.resolveCode(panelCode));
        body.put("buttonName", buttonName);
        body.put("formData", formData == null ? Map.of() : formData);
        body.put("buttonParam", buttonParam == null ? Map.of() : buttonParam);
        return gateway.post("wp-core/api/callButton2", body);
    }

    public Map<String, Object> deleteForms(String panelCode, List<String> rowCodes) {
        // 平台删除 = callButton2 的「删除」按钮：buttonParam.rowCodes
        Map<String, Object> buttonParam = new LinkedHashMap<>();
        buttonParam.put("rowCodes", rowCodes == null ? List.of() : rowCodes);
        return callButton(panelCode, "删除", Map.of(), buttonParam);
    }
}