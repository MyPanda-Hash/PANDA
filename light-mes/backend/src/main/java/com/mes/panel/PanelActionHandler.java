package com.mes.panel;

import java.util.Map;

/**
 * Extension point for domain actions that are not part of the generic panel lifecycle.
 * A PLM module can implement this interface for actions such as check-out, release or ECO execution.
 */
public interface PanelActionHandler {

    boolean supports(String panelCode, String action);

    Map<String, Object> handle(PanelActionContext context);
}
