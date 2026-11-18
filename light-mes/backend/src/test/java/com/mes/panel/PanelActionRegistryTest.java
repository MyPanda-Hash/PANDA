package com.mes.panel;

import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class PanelActionRegistryTest {

    @Test
    void dispatchesDomainActionWithItsContext() {
        PanelActionHandler handler = handler("PLM_ITEM", "签出", Map.of("状态", "已签出"));
        PanelActionRegistry registry = new PanelActionRegistry(List.of(handler));

        Map<String, Object> result = registry.dispatch(new PanelActionContext(
                "PLM_ITEM", "签出", Map.of("编号", "ITEM-001"), Map.of(), "admin"))
                .orElseThrow();

        assertEquals("已签出", result.get("状态"));
    }

    @Test
    void returnsEmptyWhenNoDomainHandlerMatches() {
        PanelActionRegistry registry = new PanelActionRegistry(List.of());

        assertTrue(registry.dispatch(new PanelActionContext(
                "PLM_ITEM", "发布", Map.of(), Map.of(), "admin")).isEmpty());
    }

    @Test
    void rejectsAmbiguousDomainHandlers() {
        PanelActionHandler first = handler("PLM_ITEM", "发布", Map.of());
        PanelActionHandler second = handler("PLM_ITEM", "发布", Map.of());
        PanelActionRegistry registry = new PanelActionRegistry(List.of(first, second));

        assertThrows(IllegalStateException.class, () -> registry.dispatch(new PanelActionContext(
                "PLM_ITEM", "发布", Map.of(), Map.of(), "admin")));
    }

    private PanelActionHandler handler(String panelCode, String action, Map<String, Object> result) {
        return new PanelActionHandler() {
            @Override
            public boolean supports(String candidatePanel, String candidateAction) {
                return panelCode.equals(candidatePanel) && action.equals(candidateAction);
            }

            @Override
            public Map<String, Object> handle(PanelActionContext context) {
                return result;
            }
        };
    }
}
