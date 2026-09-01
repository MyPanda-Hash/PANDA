package com.mes.panel;

import java.util.List;
import java.util.Map;

/** Stable application contract consumed by the configuration-driven panel controller. */
public interface PanelRuntimeService {

    Map<String, Object> getPanelConfig(String panelCode);

    /** 按登录用户叠加表格列定制（px_column_pref：顺序/显隐/别名）的面板配置；userName 为空时不叠加 */
    default Map<String, Object> getPanelConfig(String panelCode, String userName) {
        return getPanelConfig(panelCode);
    }

    /** 用户级表格列定制保存（columns 空数组=恢复默认）；默认不支持 */
    default void saveColumnPrefs(String panelCode, String userName, List<Map<String, Object>> columns) {
        throw new UnsupportedOperationException("saveColumnPrefs is not supported by this runtime");
    }

    Map<String, Object> getPermMatrix(String panelCode);

    /** 按登录用户权限过滤的权限矩阵（userName 为空或 admin 时不过滤，保持全真） */
    default Map<String, Object> getPermMatrix(String panelCode, String userName) {
        return getPermMatrix(panelCode);
    }

    Map<String, Object> getNewFormPermMatrix(String panelCode, String operationName);

    Map<String, Object> getFormDescriptor(String panelCode, String code);

    Map<String, Object> queryFormDataList(String panelCode, String keyword,
                                          Map<String, Object> condition, int pageNo, int pageSize);

    List<Map<String, Object>> getApprovalHistory(String panelCode, String formNo);

    Map<String, Object> callButton(String panelCode, String buttonName,
                                   Map<String, Object> formData, Map<String, Object> buttonParam);

    void deleteForms(String panelCode, List<String> rowCodes);
}
