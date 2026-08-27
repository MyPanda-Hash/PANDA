package com.mes.panel;

import java.util.List;
import java.util.Map;

/** Stable application contract consumed by the configuration-driven panel controller. */
public interface PanelRuntimeService {

    Map<String, Object> getPanelConfig(String panelCode);

    Map<String, Object> getPermMatrix(String panelCode);

    Map<String, Object> getNewFormPermMatrix(String panelCode, String operationName);

    Map<String, Object> getFormDescriptor(String panelCode, String code);

    Map<String, Object> queryFormDataList(String panelCode, String keyword,
                                          Map<String, Object> condition, int pageNo, int pageSize);

    List<Map<String, Object>> getApprovalHistory(String panelCode, String formNo);

    Map<String, Object> callButton(String panelCode, String buttonName,
                                   Map<String, Object> formData, Map<String, Object> buttonParam);

    void deleteForms(String panelCode, List<String> rowCodes);
}
