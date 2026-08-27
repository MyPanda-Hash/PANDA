package com.mes.panel;

import java.util.Map;

/** Input shared by domain-specific panel action handlers. */
public record PanelActionContext(
        String panelCode,
        String action,
        Map<String, Object> formData,
        Map<String, Object> parameters,
        String userName) {
}
