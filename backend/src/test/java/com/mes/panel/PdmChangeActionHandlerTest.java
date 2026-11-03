package com.mes.panel;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.mapper.FormDataMapper;
import com.mes.service.PdmRevisionService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * BOM 变更动作：PanelActionHandler 首个实现的端到端校验。
 */
@ExtendWith(MockitoExtension.class)
class PdmChangeActionHandlerTest {

    @Mock private FormDataMapper formMapper;

    private final ObjectMapper json = new ObjectMapper();
    private PdmChangeActionHandler handler;

    @BeforeEach
    void setUp() {
        handler = new PdmChangeActionHandler(formMapper, new PdmRevisionService());
    }

    @Test
    void supportsOnlyRevisionPanelsAndChangeAction() {
        assertTrue(handler.supports("BOM", "变更"));
        assertTrue(handler.supports("ROUTE", "变更"));
        assertFalse(handler.supports("BOM", "审核"));
        assertFalse(handler.supports("SO_ORDER", "变更"));
    }

    @Test
    void createsNextRevisionAndSupersedesOld() throws Exception {
        FormData source = document("BOM", "BOM-001", "已审核", 1, null, true);
        when(formMapper.selectOne(any())).thenReturn(source);
        when(formMapper.selectList(any())).thenReturn(List.of(source));

        Map<String, Object> result = handler.handle(
                new PanelActionContext("BOM", "变更", Map.of("编号", "BOM-001"), Map.of(), "admin"));

        assertEquals("BOM-001-V2", result.get("编号"));
        assertEquals(2, result.get("版本"));
        assertEquals("BOM-001", result.get("父单号"));
        assertEquals("ECN-BOM-001-V2", result.get("变更单号"));
        assertEquals("草稿", result.get("单据状态"));
        assertTrue((Boolean) result.get("原单已取代"));

        ArgumentCaptor<FormData> inserted = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).insert(inserted.capture());
        FormData draft = inserted.getValue();
        assertEquals("BOM-001-V2", draft.getFormNo());
        assertEquals(2, draft.getVersion());
        assertEquals("BOM-001", draft.getParentNo());
        assertTrue(draft.getCurrentVersion());
        assertEquals("草稿", draft.getStatus());

        // 旧版本被标记已取代
        verify(formMapper).update(any(), any());
    }

    @Test
    void rejectsDraftRevision() throws Exception {
        when(formMapper.selectOne(any())).thenReturn(document("BOM", "BOM-002", "草稿", 1, null, true));

        IllegalStateException e = assertThrows(IllegalStateException.class,
                () -> handler.handle(new PanelActionContext("BOM", "变更",
                        Map.of("编号", "BOM-002"), Map.of(), "admin")));
        assertTrue(e.getMessage().contains("已审核"));
        verify(formMapper, never()).insert(any(FormData.class));
    }

    @Test
    void rejectsSupersededRevision() throws Exception {
        when(formMapper.selectOne(any())).thenReturn(document("BOM", "BOM-003", "已审核", 1, null, false));

        IllegalStateException e = assertThrows(IllegalStateException.class,
                () -> handler.handle(new PanelActionContext("BOM", "变更",
                        Map.of("编号", "BOM-003"), Map.of(), "admin")));
        assertTrue(e.getMessage().contains("取代"));
        verify(formMapper, never()).insert(any(FormData.class));
    }

    @Test
    void rejectsMissingDocument() {
        when(formMapper.selectOne(any())).thenReturn(null);

        assertThrows(IllegalArgumentException.class,
                () -> handler.handle(new PanelActionContext("BOM", "变更",
                        Map.of("编号", "NOT-EXIST"), Map.of(), "admin")));
    }

    @Test
    void rejectsMissingNumber() {
        assertThrows(IllegalArgumentException.class,
                () -> handler.handle(new PanelActionContext("BOM", "变更", Map.of(), Map.of(), "admin")));
    }

    @Test
    void revisionContinuesFromExistingChain() throws Exception {
        FormData v2 = document("BOM", "BOM-001-V2", "已审核", 2, "BOM-001", true);
        when(formMapper.selectOne(any())).thenReturn(v2);
        when(formMapper.selectList(any())).thenReturn(List.of(
                document("BOM", "BOM-001", "已审核", 1, null, false), v2));

        Map<String, Object> result = handler.handle(
                new PanelActionContext("BOM", "变更", Map.of("编号", "BOM-001-V2"), Map.of(), "admin"));

        assertEquals("BOM-001-V3", result.get("编号"));
        assertEquals(3, result.get("版本"));
        assertEquals("BOM-001", result.get("父单号"), "孙版本仍归属链首");
    }

    private FormData document(String panel, String no, String status, int version,
                              String parentNo, boolean current) throws Exception {
        FormData fd = new FormData();
        fd.setId(1L);
        fd.setPanelCode(panel);
        fd.setFormNo(no);
        fd.setStatus(status);
        fd.setVersion(version);
        fd.setParentNo(parentNo);
        fd.setCurrentVersion(current);
        fd.setData(json.writeValueAsString(Map.of("单据编号", no)));
        fd.setDetailData(json.writeValueAsString(Map.of("children", List.of())));
        return fd;
    }
}
