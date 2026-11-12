package com.mes.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.mapper.FormDataMapper;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 工程变更：版本进位规则、执行前置条件、目标单据匹配。
 *
 * <p>变更单是 PDM 唯一的「改版留痕」入口，版本算错或目标找错都会让 BOM 版本失真。
 */
class EngChangeServiceTest {

    private final FormDataMapper formMapper = mock(FormDataMapper.class);
    private final ObjectMapper json = new ObjectMapper();
    private final EngChangeService service = new EngChangeService(formMapper, json);

    @Test
    void nextVersionBumpsMinorOrMajor() {
        assertEquals("V1.1", EngChangeService.nextVersion("V1.0", "小版本"));
        assertEquals("V1.3", EngChangeService.nextVersion("V1.2", "小版本"));
        assertEquals("V2.0", EngChangeService.nextVersion("V1.2", "大版本"));
        assertEquals("V1.1", EngChangeService.nextVersion("", "小版本"), "空版本按 V1.0 起步");
        assertEquals("V2.0", EngChangeService.nextVersion("V1.0", "大版本"));
        assertEquals("V1.1", EngChangeService.nextVersion("1.0", "小版本"), "不带 V 也能解析");
    }

    @Test
    void applyRequiresApprovedChange() {
        when(formMapper.selectOne(any())).thenReturn(doc("ENG_CHANGE", "EC-001", "草稿", Map.of(), List.of()));
        IllegalStateException error = assertThrows(IllegalStateException.class,
                () -> service.apply("EC-001"));
        assertTrue(error.getMessage().contains("审核"));
        verify(formMapper, never()).updateById(any(FormData.class));
    }

    @Test
    void applyBumpsRouteVersionAndStampsChangeNo() throws Exception {
        Map<String, Object> changeHead = Map.of("单据编号", "EC-001");
        List<Map<String, Object>> changeRows = List.of(Map.of(
                "对象类型", "工艺路线", "对象编码", "RT-CP001", "变更级别", "小版本", "变更说明", "换夹具"));
        when(formMapper.selectOne(any())).thenReturn(
                doc("ENG_CHANGE", "EC-001", "已审核", changeHead, changeRows));
        Map<String, Object> routeHead = Map.of("工艺路线编码", "RT-CP001", "版本号", "V1.0");
        FormData route = doc("ROUTE", "RT-001", "已审核", routeHead, List.of());
        when(formMapper.selectList(any())).thenReturn(List.of(route));

        Map<String, Object> out = service.apply("EC-001");

        assertEquals(1, out.get("执行条数"));
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> rows = (List<Map<String, Object>>) out.get("rows");
        assertEquals("V1.0", rows.get(0).get("原版本"));
        assertEquals("V1.1", rows.get(0).get("新版本"));
        Map<String, Object> saved = json.readValue(route.getData(), Map.class);
        assertEquals("V1.1", saved.get("版本号"));
        assertEquals("EC-001", saved.get("变更单号"));
        assertTrue(String.valueOf(saved.get("最后变更日期")).length() == 10);
    }

    @Test
    void bomCanBeMatchedByDetailCode() {
        Map<String, Object> bomHead = Map.of("产品编码", "CP002");
        List<Map<String, Object>> rows = List.of(Map.of("物料清单编码", "BOM-CP002-A"));
        FormData bom = doc("BOM", "BOM-001", "已审核", bomHead, rows);
        when(formMapper.selectList(any())).thenReturn(List.of(bom));
        assertEquals("BOM-001", service.findTarget("BOM", "BOM-CP002-A").getFormNo(),
                "BOM 的版本挂在明细行，对象编码按明细匹配");
    }

    @Test
    void missingTargetFailsLoudly() {
        when(formMapper.selectOne(any())).thenReturn(doc("ENG_CHANGE", "EC-002", "已审核",
                Map.of(), List.of(Map.of("对象类型", "物料清单", "对象编码", "BOM-NOT-EXIST", "变更级别", "小版本"))));
        when(formMapper.selectList(any())).thenReturn(List.of());
        IllegalStateException error = assertThrows(IllegalStateException.class,
                () -> service.apply("EC-002"));
        assertTrue(error.getMessage().contains("找不到物料清单"));
    }

    private FormData doc(String panel, String no, String status,
                         Map<String, Object> head, List<Map<String, Object>> items) {
        FormData fd = new FormData();
        fd.setPanelCode(panel);
        fd.setFormNo(no);
        fd.setStatus(status);
        try {
            fd.setData(json.writeValueAsString(head));
            fd.setDetailData(json.writeValueAsString(Map.of("items", items)));
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
        return fd;
    }
}
