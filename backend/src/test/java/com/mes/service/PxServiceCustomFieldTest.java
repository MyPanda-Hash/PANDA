package com.mes.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.PanelConfig;
import com.mes.mapper.FormApprovalMapper;
import com.mes.mapper.FormDataMapper;
import com.mes.mapper.PanelConfigMapper;
import com.mes.panel.PanelActionRegistry;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.contains;
import static org.mockito.Mockito.atLeastOnce;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 面板自助加字段（px_custom_field）：登记元数据 → 读取出口合并进面板配置。
 *
 * 口径（借鉴 YINJIA-MES「字段管理」后按本项目的 JSON 存储方式落地）：
 * - 加字段不动表结构、不写 panel_config.config（Registry 启动时会覆盖），只登记元数据；
 * - 表头字段要同时补进 formPages.fieldNames 白名单，否则前端按白名单取字段看不到；
 * - 明细首块列来自 gridTabs[0].columns，不补列就不显示；
 * - 只能停用自定义字段，内置字段拒绝；停用不碰业务数据。
 */
@ExtendWith(MockitoExtension.class)
class PxServiceCustomFieldTest {

    @Mock private PanelConfigMapper panelMapper;
    @Mock private FormDataMapper formMapper;
    @Mock private FormApprovalMapper approvalMapper;
    @Mock private ReportQueryService reportQueryService;
    @Mock private JdbcTemplate jdbcTemplate;
    @Mock private RoleService roleService;

    private final ObjectMapper json = new ObjectMapper();
    /** 模拟 px_custom_field 表内容（加字段后写进来，供读取出口合并） */
    private final List<Map<String, Object>> stored = new ArrayList<>();
    private PxService service;

    @BeforeEach
    void setUp() {
        service = new PxService(panelMapper, formMapper, approvalMapper, reportQueryService,
                new PanelActionRegistry(List.of()), roleService, jdbcTemplate, null, null, null, null, null, null);
        // 默认以 admin 身份操作（权限另有专项用例），否则 requirePanelPermission 会先拦下来
        SecurityContextHolder.getContext().setAuthentication(
                new UsernamePasswordAuthenticationToken("tester", "x", List.of()));
        lenient().when(roleService.getPerms("tester")).thenReturn(Map.of("isAdmin", true, "panelPerms", Map.of()));
        lenient().when(panelMapper.selectOne(any())).thenReturn(panel("SO_ORDER", salesConfig()));
        // 读自定义字段：返回内存表
        lenient().when(jdbcTemplate.queryForList(contains("FROM px_custom_field"), any(Object[].class)))
                .thenAnswer(inv -> new ArrayList<>(stored));
        // 写自定义字段：落到内存表（vars 从索引 1 开始：0 = SQL）
        lenient().when(jdbcTemplate.update(contains("INSERT INTO px_custom_field"), any(Object[].class)))
                .thenAnswer(inv -> {
                    Object[] a = inv.getArguments();
                    Map<String, Object> row = new LinkedHashMap<>();
                    row.put("scope", a[2]);
                    row.put("tab_key", a[3]);
                    row.put("data_name", a[4]);
                    row.put("data_type", a[5]);
                    row.put("options", a[6]);
                    row.put("field_width", a[7]);
                    row.put("is_required", a[8]);
                    row.put("in_grid", a[9]);
                    stored.add(row);
                    return 1;
                });
        lenient().when(jdbcTemplate.update(contains("DELETE FROM px_custom_field"), any(Object[].class)))
                .thenAnswer(inv -> {
                    Object[] a = inv.getArguments();
                    return stored.removeIf(r -> String.valueOf(r.get("data_name")).equals(String.valueOf(a[4]))) ? 1 : 0;
                });
        lenient().when(jdbcTemplate.update(contains("INSERT INTO px_field_log"), any(Object[].class))).thenReturn(1);
    }

    @AfterEach
    void tearDown() {
        SecurityContextHolder.clearContext();
    }

    @Test
    void detailFieldIsMergedIntoFieldsAndFirstGridColumns() {
        Map<String, Object> out = service.addCustomField("SO_ORDER", body("detail", "items", "客户要求", "文本"));

        assertEquals("客户要求", out.get("dataName"));
        assertEquals("detail", out.get("scope"));

        Map<String, Object> cfg = service.getPanelConfig("SO_ORDER");
        List<Map<String, Object>> fields = fieldsOf(cfg, "items");
        assertTrue(fields.stream().anyMatch(f -> "客户要求".equals(f.get("dataName"))));
        assertTrue(fields.stream().anyMatch(f -> "客户要求".equals(f.get("dataName")) && Boolean.TRUE.equals(f.get("custom"))));
        // 第一个明细页签的列来自 gridTabs[0].columns，必须补列才显示
        assertTrue(firstColumns(cfg).contains("客户要求"));
    }

    @Test
    void headerFieldIsMergedIntoFieldsAndFieldNamesWhitelist() {
        service.addCustomField("SO_ORDER", body("header", null, "客户单号", "文本"));

        Map<String, Object> cfg = service.getPanelConfig("SO_ORDER");
        assertTrue(headerFieldNames(cfg).contains("客户单号"));
        // 表头字段受 formPages.fieldNames 白名单约束：不在里面就显示不出来
        assertTrue(String.valueOf(firstFormPage(cfg).get("fieldNames")).contains("客户单号"));
    }

    @Test
    void dropdownFieldCarriesOptionsAndRequiredFlag() {
        Map<String, Object> body = body("detail", "items", "急件", "下拉框");
        body.put("options", "是，否, 待定");
        body.put("isRequired", true);
        body.put("fieldWidth", 120);
        service.addCustomField("SO_ORDER", body);

        Map<String, Object> cfg = service.getPanelConfig("SO_ORDER");
        Map<String, Object> field = fieldsOf(cfg, "items").stream()
                .filter(f -> "急件".equals(f.get("dataName"))).findFirst().orElseThrow();
        assertEquals("下拉框", field.get("dataType"));
        assertEquals(List.of("是", "否", "待定"), field.get("options"));
        assertEquals(Boolean.TRUE, field.get("isRequired"));
        assertEquals(120, field.get("fieldWidth"));
    }

    @Test
    void rejectsNameWithDotOrSpace() {
        assertThrows(IllegalArgumentException.class,
                () -> service.addCustomField("SO_ORDER", body("detail", "items", "存货名称.品牌", "文本")));
        assertThrows(IllegalArgumentException.class,
                () -> service.addCustomField("SO_ORDER", body("detail", "items", "客户 要求", "文本")));
        assertThrows(IllegalArgumentException.class,
                () -> service.addCustomField("SO_ORDER", body("detail", "items", "", "文本")));
        assertTrue(stored.isEmpty());
    }

    @Test
    void rejectsDuplicateNameAgainstBuiltInField() {
        assertThrows(IllegalArgumentException.class,
                () -> service.addCustomField("SO_ORDER", body("detail", "items", "数量", "小数")));
        // 同位置同名字段加第二次也要拒绝
        service.addCustomField("SO_ORDER", body("detail", "items", "客户要求", "文本"));
        assertThrows(IllegalArgumentException.class,
                () -> service.addCustomField("SO_ORDER", body("detail", "items", "客户要求", "文本")));
        // 换位置（表头）允许同名
        service.addCustomField("SO_ORDER", body("header", null, "客户要求", "文本"));
        assertEquals(2, stored.size());
    }

    @Test
    void rejectsUnsupportedTypeAndMissingOptions() {
        assertThrows(IllegalArgumentException.class,
                () -> service.addCustomField("SO_ORDER", body("detail", "items", "甲方", "参照")));
        assertThrows(IllegalArgumentException.class,
                () -> service.addCustomField("SO_ORDER", body("detail", "items", "急件", "下拉框")));
        // 列宽越界
        Map<String, Object> wide = body("detail", "items", "客户要求", "文本");
        wide.put("fieldWidth", 999);
        assertThrows(IllegalArgumentException.class, () -> service.addCustomField("SO_ORDER", wide));
        // 明细页签必须存在
        assertThrows(IllegalArgumentException.class,
                () -> service.addCustomField("SO_ORDER", body("detail", "nope", "客户要求", "文本")));
    }

    @Test
    void retireRemovesMetadataButOnlyForCustomFields() {
        service.addCustomField("SO_ORDER", body("detail", "items", "客户要求", "文本"));
        service.retireCustomField("SO_ORDER", Map.of("scope", "detail", "tabKey", "items", "dataName", "客户要求"));

        Map<String, Object> cfg = service.getPanelConfig("SO_ORDER");
        assertFalse(fieldsOf(cfg, "items").stream().anyMatch(f -> "客户要求".equals(f.get("dataName"))));
        assertFalse(firstColumns(cfg).contains("客户要求"));
        // 内置字段不能「停用」——它根本不在自定义字段表里
        assertThrows(IllegalArgumentException.class,
                () -> service.retireCustomField("SO_ORDER", Map.of("scope", "detail", "tabKey", "items", "dataName", "数量")));
    }

    @Test
    void addingFieldIsAudited() {
        service.addCustomField("SO_ORDER", body("detail", "items", "客户要求", "文本"));
        verify(jdbcTemplate, atLeastOnce()).update(contains("INSERT INTO px_field_log"), any(Object[].class));
    }

    @Test
    void requiresEditPermission() {
        SecurityContextHolder.getContext().setAuthentication(
                new UsernamePasswordAuthenticationToken("lisi", "x", List.of()));
        when(roleService.getPerms("lisi")).thenReturn(Map.of(
                "isAdmin", false,
                "panelPerms", Map.of("SO_ORDER", List.of("view", "query"))));
        assertThrows(AccessDeniedException.class,
                () -> service.addCustomField("SO_ORDER", body("detail", "items", "客户要求", "文本")));
        assertTrue(stored.isEmpty());
    }

    @Test
    void adminPermissionIsNotFiltered() {
        SecurityContextHolder.getContext().setAuthentication(
                new UsernamePasswordAuthenticationToken("admin", "x", List.of()));
        when(roleService.getPerms("admin")).thenReturn(Map.of("isAdmin", true, "panelPerms", Map.of()));
        service.addCustomField("SO_ORDER", body("detail", "items", "客户要求", "文本"));
        assertEquals(1, stored.size());
    }
    // ---------- 夹具 ----------

    private static Map<String, Object> body(String scope, String tabKey, String dataName, String dataType) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("scope", scope);
        if (tabKey != null) body.put("tabKey", tabKey);
        body.put("dataName", dataName);
        body.put("dataType", dataType);
        return body;
    }

    private PanelConfig panel(String code, Map<String, Object> config) {
        PanelConfig pc = new PanelConfig();
        pc.setPanelCode(code);
        pc.setPanelName("销售订单");
        try {
            pc.setConfig(json.writeValueAsString(config));
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
        return pc;
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> fieldsOf(Map<String, Object> cfg, String tabKey) {
        for (Object tab : (List<Object>) ((Map<String, Object>) cfg.get("detail")).get("tabs")) {
            Map<String, Object> t = (Map<String, Object>) tab;
            if (tabKey.equals(t.get("key"))) return (List<Map<String, Object>>) t.get("fields");
        }
        throw new IllegalStateException("页签不存在：" + tabKey);
    }

    @SuppressWarnings("unchecked")
    private static List<Object> firstColumns(Map<String, Object> cfg) {
        Map<String, Object> metadata = (Map<String, Object>) cfg.get("metadata");
        Map<String, Object> pageDto = (Map<String, Object>) metadata.get("panelPageDto");
        List<Object> pages = (List<Object>) pageDto.get("tablePages");
        Map<String, Object> page = (Map<String, Object>) pages.get(0);
        List<Object> tabs = (List<Object>) page.get("gridTabs");
        return (List<Object>) ((Map<String, Object>) tabs.get(0)).get("columns");
    }

    @SuppressWarnings("unchecked")
    private static List<String> headerFieldNames(Map<String, Object> cfg) {
        List<String> names = new ArrayList<>();
        for (Object f : (List<Object>) ((Map<String, Object>) cfg.get("dataSchema")).get("fields")) {
            names.add(String.valueOf(((Map<String, Object>) f).get("dataName")));
        }
        return names;
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> firstFormPage(Map<String, Object> cfg) {
        Map<String, Object> metadata = (Map<String, Object>) cfg.get("metadata");
        Map<String, Object> pageDto = (Map<String, Object>) metadata.get("panelPageDto");
        List<Object> pages = (List<Object>) pageDto.get("formPages");
        return (Map<String, Object>) pages.get(0);
    }

    private static Map<String, Object> salesConfig() {
        Map<String, Object> config = new LinkedHashMap<>();
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("panelCode", "SO_ORDER");
        metadata.put("panelName", "销售订单");
        metadata.put("panelCategory", "单据");

        Map<String, Object> gridTab = new LinkedHashMap<>();
        gridTab.put("label", "明细");
        gridTab.put("rowSource", "items");
        List<Object> columns = new ArrayList<>(List.of("存货编码", "存货名称", "数量"));
        gridTab.put("columns", columns);

        Map<String, Object> page = new LinkedHashMap<>();
        page.put("tableName", "销售订单列表");
        page.put("gridTabs", new ArrayList<>(List.of(gridTab)));

        Map<String, Object> formPage = new LinkedHashMap<>();
        formPage.put("formName", "销售订单");
        formPage.put("fieldNames", "单据编号,客户");

        Map<String, Object> pageDto = new LinkedHashMap<>();
        pageDto.put("tablePages", new ArrayList<>(List.of(page)));
        pageDto.put("formPages", new ArrayList<>(List.of(formPage)));
        metadata.put("panelPageDto", pageDto);
        config.put("metadata", metadata);

        Map<String, Object> quantity = new LinkedHashMap<>();
        quantity.put("dataName", "数量");
        quantity.put("dataType", "小数");
        Map<String, Object> detailTab = new LinkedHashMap<>();
        detailTab.put("key", "items");
        detailTab.put("label", "明细");
        detailTab.put("fields", new ArrayList<>(List.of(quantity)));
        Map<String, Object> detail = new LinkedHashMap<>();
        detail.put("tabs", new ArrayList<>(List.of(detailTab)));
        config.put("detail", detail);

        Map<String, Object> docNo = new LinkedHashMap<>();
        docNo.put("dataName", "单据编号");
        docNo.put("dataType", "文本");
        Map<String, Object> customer = new LinkedHashMap<>();
        customer.put("dataName", "客户");
        customer.put("dataType", "文本");
        Map<String, Object> schema = new LinkedHashMap<>();
        schema.put("type", "object");
        schema.put("fields", new ArrayList<>(List.of(docNo, customer)));
        config.put("dataSchema", schema);
        return config;
    }
}
