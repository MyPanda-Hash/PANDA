package com.mes.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.entity.PanelConfig;
import com.mes.mapper.FormDataMapper;
import com.mes.mapper.PanelConfigMapper;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

@Service
public class PxService {

    private final PanelConfigMapper panelMapper;
    private final FormDataMapper formMapper;
    private final ObjectMapper json = new ObjectMapper();

    public PxService(PanelConfigMapper panelMapper, FormDataMapper formMapper) {
        this.panelMapper = panelMapper;
        this.formMapper = formMapper;
    }

    // ---------- 配置 ----------

    @SuppressWarnings("unchecked")
    private Map<String, Object> loadConfig(String panelCode) {
        PanelConfig pc = panelMapper.selectOne(new LambdaQueryWrapper<PanelConfig>()
                .eq(PanelConfig::getPanelCode, panelCode));
        if (pc == null) throw new IllegalArgumentException("面板不存在：" + panelCode);
        try {
            return json.readValue(pc.getConfig(), new TypeReference<Map<String, Object>>() {});
        } catch (Exception e) {
            throw new IllegalStateException("面板配置解析失败：" + e.getMessage());
        }
    }

    public Map<String, Object> getPanelConfig(String panelCode) {
        return loadConfig(panelCode);
    }

    // ---------- 权限矩阵 ----------

    @SuppressWarnings("unchecked")
    public Map<String, Object> getPermMatrix(String panelCode) {
        Map<String, Object> cfg = loadConfig(panelCode);
        Map<String, Object> metadata = (Map<String, Object>) cfg.get("metadata");
        List<Map<String, Object>> buttons = (List<Map<String, Object>>) metadata.get("panelButtons");
        List<Map<String, Object>> actions = new ArrayList<>();
        for (Map<String, Object> b : buttons) {
            Map<String, Object> a = new HashMap<>();
            a.put("name", String.valueOf(b.get("buttonName")));
            a.put("visible", true);
            a.put("operatable", true);
            actions.add(a);
        }
        Map<String, Object> privilege = new HashMap<>();
        privilege.put("actionPrivileges", actions);
        privilege.put("fieldPrivileges", new ArrayList<>());
        privilege.put("groupPrivileges", new ArrayList<>());
        Map<String, Object> out = new HashMap<>();
        out.put("privilege", privilege);
        return out;
    }

    // ---------- 字段 meta ----------

    @SuppressWarnings("unchecked")
    private Map<String, Object> fieldsOf(String panelCode) {
        Map<String, Object> cfg = loadConfig(panelCode);
        Map<String, Object> schema = (Map<String, Object>) cfg.get("dataSchema");
        List<Map<String, Object>> fields = (List<Map<String, Object>>) schema.get("fields");
        Map<String, Object> out = new HashMap<>();
        out.put("fields", fields);
        out.put("detail", cfg.get("detail"));
        out.put("stateFieldName", ((Map<String, Object>) ((Map<String, Object>) cfg.get("metadata")).get("panelState")).get("dataName"));
        return out;
    }

    private List<Map<String, Object>> buildMeta(String panelCode) {
        List<Map<String, Object>> meta = new ArrayList<>();
        List<Map<String, Object>> fields = (List<Map<String, Object>>) fieldsOf(panelCode).get("fields");
        for (Map<String, Object> f : fields) {
            Map<String, Object> m = new HashMap<>();
            m.put("code", String.valueOf(f.get("dataName")));
            m.put("name", String.valueOf(f.get("dataName")));
            m.put("dataType", f.getOrDefault("dataType", "文本"));
            m.put("isNotNull", Boolean.TRUE.equals(f.get("isRequired")));
            m.put("defaultValue", f.getOrDefault("defaultValue", ""));
            if (f.containsKey("options")) m.put("options", f.get("options"));
            meta.add(m);
        }
        return meta;
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> fieldPrivileges() {
        List<Map<String, Object>> fps = new ArrayList<>();
        Map<String, Object> privilege = new HashMap<>();
        privilege.put("fieldPrivileges", fps);
        return privilege;
    }

    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> actionPrivileges(String panelCode, String formPage) {
        Map<String, Object> cfg = loadConfig(panelCode);
        Map<String, Object> metadata = (Map<String, Object>) cfg.get("metadata");
        Map<String, Object> pageDto = (Map<String, Object>) metadata.get("panelPageDto");
        List<Map<String, Object>> pages = (List<Map<String, Object>>) pageDto.get("formPages".equals(formPage) ? "formPages" : "tablePages");
        String key = "formPages".equals(formPage) ? "bottomOperationBarBtn" : "topBarBtn";
        List<Map<String, Object>> btns = (List<Map<String, Object>>) pages.get(0).get(key);
        List<Map<String, Object>> actions = new ArrayList<>();
        for (Map<String, Object> b : btns) {
            Map<String, Object> a = new HashMap<>();
            a.put("name", String.valueOf(b.get("buttonName")));
            a.put("visible", true);
            a.put("operatable", true);
            actions.add(a);
        }
        return actions;
    }

    @SuppressWarnings("unchecked")
    private Object buttonGroupsOf(String panelCode) {
        Map<String, Object> cfg = loadConfig(panelCode);
        Map<String, Object> metadata = (Map<String, Object>) cfg.get("metadata");
        return metadata.get("buttonGroups");
    }

    // ---------- 表单描述器 / 新建元数据 ----------

    @SuppressWarnings("unchecked")
    public Map<String, Object> getNewFormPermMatrix(String panelCode, String operationName) {
        Map<String, Object> stateField = fieldsOf(panelCode);
        String stateName = (String) stateField.get("stateFieldName");
        Map<String, Object> data = new HashMap<>();
        data.put(stateName, "草稿");
        for (Map<String, Object> f : (List<Map<String, Object>>) stateField.get("fields")) {
            Object dv = f.get("defaultValue");
            if (dv != null && !"".equals(String.valueOf(dv))) {
                data.putIfAbsent(String.valueOf(f.get("dataName")), dv);
            }
        }
        // T+ 单据：单据日期默认系统登录日期
        if (!data.containsKey("单据日期")) data.put("单据日期", LocalDate.now().toString());
        // T+ 锭号 = 单据编号（自动预览单号）
        if (!data.containsKey("锭号")) data.put("锭号", generateFormNo(panelCode));
        Map<String, Object> privilege = new HashMap<>();
        privilege.put("actionPrivileges", actionPrivileges(panelCode, "formPages"));
        privilege.put("fieldPrivileges", new ArrayList<>());
        privilege.put("groupPrivileges", new ArrayList<>());
        Map<String, Object> out = new HashMap<>();
        out.put("data", data);
        out.put("meta", buildMeta(panelCode));
        out.put("privilege", privilege);
        out.put("detail", stateField.get("detail"));
        out.put("buttonGroups", buttonGroupsOf(panelCode));
        return out;
    }

    @SuppressWarnings("unchecked")
    public Map<String, Object> getFormDescriptor(String panelCode, String code) {
        FormData fd = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, panelCode)
                .eq(FormData::getFormNo, code));
        if (fd == null) throw new IllegalArgumentException("表单数据不存在：" + code);
        Map<String, Object> data = parseData(fd.getData());
        data.put("编号", fd.getFormNo());
        data.put("单据状态", fd.getStatus());
        data.put("创建时间", fd.getCreateTime());
        data.put("更新时间", fd.getUpdateTime());
        data.put("发起人编号", fd.getCreateBy());
        Map<String, Object> out = new HashMap<>();
        out.put("data", data);
        out.put("meta", buildMeta(panelCode));
        Map<String, Object> privilege = new HashMap<>();
        privilege.put("actionPrivileges", actionPrivileges(panelCode, "formPages"));
        privilege.put("fieldPrivileges", new ArrayList<>());
        privilege.put("groupPrivileges", new ArrayList<>());
        out.put("privilege", privilege);
        out.put("detail", fieldsOf(panelCode).get("detail"));
        out.put("detailData", parseData(fd.getDetailData()));
        out.put("buttonGroups", buttonGroupsOf(panelCode));
        return out;
    }

    // ---------- 列表 ----------

    @SuppressWarnings("unchecked")
    public Map<String, Object> queryFormDataList(String panelCode, String keyword,
                                                 Map<String, Object> condition, int pageNo, int pageSize) {
        LambdaQueryWrapper<FormData> qw = new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, panelCode)
                .orderByDesc(FormData::getCreateTime)
                .orderByDesc(FormData::getId);
        List<FormData> all = formMapper.selectList(qw);
        // T+ 形态：列表按产成品明细行展平
        List<Map<String, Object>> flat = new ArrayList<>();
        for (FormData fd : all) {
            Map<String, Object> head = parseData(fd.getData());
            head.put("编号", fd.getFormNo());
            head.put("单据状态", fd.getStatus());
            head.put("创建时间", fd.getCreateTime());
            head.put("更新时间", fd.getUpdateTime());
            head.put("发起人编号", fd.getCreateBy());
            Map<String, Object> detail = parseData(fd.getDetailData());
            List<Map<String, Object>> products = (List<Map<String, Object>>) detail.get("products");
            if (products == null || products.isEmpty()) {
                flat.add(head);
                continue;
            }
            for (Map<String, Object> p : products) {
                Map<String, Object> row = new HashMap<>(head);
                row.putAll(p);
                flat.add(row);
            }
        }
        // 条件过滤
        List<Map<String, Object>> rows = new ArrayList<>();
        for (Map<String, Object> row : flat) {
            boolean hit = true;
            if (condition != null) {
                for (Map.Entry<String, Object> e : condition.entrySet()) {
                    Object v = e.getValue();
                    if (v == null || "".equals(String.valueOf(v))) continue;
                    Object rv = row.get(e.getKey());
                    if (rv == null || !String.valueOf(rv).contains(String.valueOf(v))) { hit = false; break; }
                }
            }
            if (hit && keyword != null && !keyword.isBlank()) {
                hit = false;
                for (Object v : row.values()) {
                    if (v != null && String.valueOf(v).contains(keyword)) { hit = true; break; }
                }
            }
            if (hit) rows.add(row);
        }
        int from = Math.min((pageNo - 1) * pageSize, rows.size());
        int to = Math.min(from + pageSize, rows.size());
        Map<String, Object> out = new HashMap<>();
        out.put("totalSize", rows.size());
        out.put("list", rows.subList(from, to));
        return out;
    }

    // ---------- 按钮（业务流转） ----------

    @SuppressWarnings("unchecked")
    @Transactional
    public Map<String, Object> callButton(String panelCode, String buttonName,
                                          Map<String, Object> formData, Map<String, Object> buttonParam) {
        switch (buttonName) {
            case "刷新":
                return new HashMap<>();
            case "新增流程":
                return create(panelCode, formData, null);
            case "提交":
                return save(panelCode, formData, null);
            case "删除": {
                Object no = formData.get("编号");
                if (no == null) throw new IllegalArgumentException("缺少表单编号");
                delete(panelCode, String.valueOf(no));
                return new HashMap<>();
            }
            case "审核":
                return changeStatus(panelCode, formData, "审核");
            case "弃审":
                return changeStatus(panelCode, formData, "弃审");
            case "关闭":
                return changeStatus(panelCode, formData, "关闭");
            case "手工完工":
                return changeStatus(panelCode, formData, "手工完工");
            case "中止":
                return changeStatus(panelCode, formData, "中止");
            case "整单中止":
                return changeStatus(panelCode, formData, "中止");
            case "取消中止":
                return changeStatus(panelCode, formData, "取消中止");
            case "生成生产加工单":   // 推式生单：销售订单 → 生产加工单（对齐真实 T+「生单-生成生产加工单」）
                return createMoFromSo(panelCode, formData);
            default:
                throw new IllegalStateException("未定义按钮规则：" + buttonName + "（可在 PxService 扩展）");
        }
    }

    /**
     * 推式生单：销售订单（SO_ORDER）→ 生成生产加工单（MANU_ORDER）草稿
     * 字段对应：单据日期→合同号、单据编号→销售订单号、客户/客户编码→客户/客户编码、
     *           结算客户→生产订单客户、业务员→负责人、预计交货日期→预完工日；
     *           明细行→产成品明细（存货编码→产品编码、存货名称→产品名称、销售单位→生产单位）
     */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createMoFromSo(String panelCode, Map<String, Object> formData) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData so = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "SO_ORDER")
                .eq(FormData::getFormNo, String.valueOf(no)));
        if (so == null) throw new IllegalArgumentException("销售订单不存在：" + no);
        if (!"已审核".equals(so.getStatus())) throw new IllegalStateException("仅已审核销售订单可生成生产加工单");

        Map<String, Object> head = parseData(so.getData());
        Map<String, Object> detailMap = parseData(so.getDetailData());
        Object itemsObj = detailMap.get("items");
        List<Map<String, Object>> items = itemsObj instanceof List ? (List<Map<String, Object>>) itemsObj : new ArrayList<>();

        String newNo = generateFormNo("MANU_ORDER");
        Map<String, Object> moData = new HashMap<>();
        moData.put("合同号", head.getOrDefault("单据日期", ""));
        moData.put("锭号", newNo);
        moData.put("批号", "正常");
        moData.put("生产车间", "熔铸车间");
        moData.put("预开工日", LocalDate.now().toString());
        moData.put("预完工日", head.getOrDefault("预计交货日期", ""));
        moData.put("销售订单号", head.getOrDefault("单据编号", ""));
        moData.put("客户编码", head.getOrDefault("客户编码", ""));
        moData.put("客户", head.getOrDefault("客户", ""));
        moData.put("测试程序", "光谱分析");
        moData.put("生产订单客户", head.getOrDefault("结算客户", ""));
        moData.put("机构", "总部");
        moData.put("重量", 0);
        moData.put("开工日期", "");
        moData.put("完工日期", "");
        moData.put("启用派工", false);
        moData.put("自动转移", false);
        moData.put("产品自动添加到材料", false);
        moData.put("是否手工修改单据编码", false);
        moData.put("外部单据号", "");
        moData.put("负责人", head.getOrDefault("业务员", ""));
        moData.put("启用领料申请", false);
        moData.put("对方仓库", "");

        List<Map<String, Object>> products = new ArrayList<>();
        for (Map<String, Object> it : items) {
            Map<String, Object> p = new HashMap<>();
            p.put("生产类型", "自制");
            p.put("产品编码", it.getOrDefault("存货编码", ""));
            p.put("存货图片", "");
            p.put("产品名称", it.getOrDefault("存货名称", ""));
            p.put("规格型号", it.getOrDefault("规格型号", ""));
            p.put("型号", "");
            p.put("适用BOM", "BOM-001");
            p.put("BOM展开方式", "单阶");
            p.put("生产单位", it.getOrDefault("销售单位", "件"));
            p.put("数量", it.getOrDefault("数量", 0));
            p.put("齐套数量(主)", 0);
            p.put("累计汇报套数(工序单位)", 0);
            p.put("可用量", 0);
            p.put("可用量说明", "");
            p.put("现存量", it.getOrDefault("现存量", 0));
            p.put("现存量说明", "");
            p.put("产品字符公用自定义项1", "");
            p.put("图号", "");
            p.put("单重", 0);
            p.put("总重", 0);
            p.put("需求令号", head.getOrDefault("单据编号", ""));
            products.add(p);
        }
        Map<String, Object> detail = new HashMap<>();
        detail.put("products", products);
        detail.put("materials", new ArrayList<>());
        detail.put("processes", new ArrayList<>());

        FormData mo = new FormData();
        mo.setPanelCode("MANU_ORDER");
        mo.setFormNo(newNo);
        mo.setData(toJson(moData));
        mo.setDetailData(toJson(detail));
        mo.setStatus("草稿");
        mo.setCreateBy("admin");
        mo.setCreateTime(LocalDateTime.now());
        mo.setUpdateTime(LocalDateTime.now());
        formMapper.insert(mo);

        Map<String, Object> out = new HashMap<>();
        out.put("编号", newNo);
        out.put("单据状态", "草稿");
        out.put("gotoPanel", "MANU_ORDER");
        return out;
    }

    private Map<String, Object> create(String panelCode, Map<String, Object> formData, String userName) {
        return save(panelCode, formData, userName);
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> save(String panelCode, Map<String, Object> formData, String userName) {
        Object detail = formData.remove("detail");
        FormData fd;
        Object no = formData.get("编号");
        String stateField = (String) fieldsOf(panelCode).get("stateFieldName");
        formData.remove("编号");
        formData.remove("创建时间");
        formData.remove("更新时间");
        formData.remove("发起人编号");
        if (no != null && !"".equals(String.valueOf(no))) {
            fd = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                    .eq(FormData::getPanelCode, panelCode)
                    .eq(FormData::getFormNo, String.valueOf(no)));
            if (fd == null) throw new IllegalArgumentException("表单数据不存在：" + no);
            if (!"草稿".equals(fd.getStatus())) throw new IllegalStateException("仅草稿状态可保存");
            fd.setData(toJson(formData));
            fd.setDetailData(toJson(detail));
            fd.setUpdateTime(LocalDateTime.now());
            formMapper.updateById(fd);
        } else {
            fd = new FormData();
            fd.setPanelCode(panelCode);
            fd.setFormNo(generateFormNo(panelCode));
            fd.setData(toJson(formData));
            fd.setDetailData(toJson(detail));
            fd.setStatus("草稿");
            fd.setCreateBy(userName == null ? "admin" : userName);
            fd.setCreateTime(LocalDateTime.now());
            fd.setUpdateTime(LocalDateTime.now());
            formMapper.insert(fd);
        }
        Map<String, Object> out = new HashMap<>();
        out.put("编号", fd.getFormNo());
        out.put("单据状态", fd.getStatus());
        out.put(stateField, fd.getStatus());
        return out;
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> changeStatus(String panelCode, Map<String, Object> formData, String action) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData fd = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, panelCode)
                .eq(FormData::getFormNo, String.valueOf(no)));
        if (fd == null) throw new IllegalArgumentException("表单数据不存在：" + no);
        if ("审核".equals(action)) {
            if (!"草稿".equals(fd.getStatus())) throw new IllegalStateException("仅草稿状态可审核");
            fd.setStatus("已审核");
            fd.setAuditBy("admin");
            fd.setAuditTime(LocalDateTime.now());
        } else if ("弃审".equals(action)) {
            if (!"已审核".equals(fd.getStatus())) throw new IllegalStateException("仅已审核状态可弃审");
            fd.setStatus("草稿");
            fd.setAuditBy(null);
            fd.setAuditTime(null);
        } else if ("关闭".equals(action)) {
            if (!"已审核".equals(fd.getStatus()) && !"生产中".equals(fd.getStatus())) {
                throw new IllegalStateException("仅已审核/生产中状态可关闭");
            }
            fd.setStatus("已关闭");
            fd.setCloseTime(LocalDateTime.now());
        } else if ("手工完工".equals(action)) {
            if (!"已审核".equals(fd.getStatus()) && !"生产中".equals(fd.getStatus())) {
                throw new IllegalStateException("仅已审核/生产中状态可手工完工");
            }
            fd.setStatus("已完工");
        } else if ("中止".equals(action)) {
            if (!"已审核".equals(fd.getStatus()) && !"生产中".equals(fd.getStatus()) && !"已完工".equals(fd.getStatus())) {
                throw new IllegalStateException("仅已审核/生产中/已完工状态可中止");
            }
            fd.setStatus("已中止");
        } else if ("取消中止".equals(action)) {
            if (!"已中止".equals(fd.getStatus())) {
                throw new IllegalStateException("仅已中止状态可取消中止");
            }
            fd.setStatus("已审核");
        }
        fd.setUpdateTime(LocalDateTime.now());
        formMapper.updateById(fd);
        Map<String, Object> out = new HashMap<>();
        out.put("编号", fd.getFormNo());
        out.put("单据状态", fd.getStatus());
        return out;
    }

    @Transactional
    public void deleteForms(String panelCode, List<String> rowCodes) {
        for (String code : rowCodes) delete(panelCode, code);
    }

    private void delete(String panelCode, String code) {
        FormData fd = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, panelCode)
                .eq(FormData::getFormNo, code));
        if (fd == null) throw new IllegalArgumentException("表单数据不存在：" + code);
        if (!"草稿".equals(fd.getStatus())) throw new IllegalStateException("仅草稿状态可删除");
        formMapper.deleteById(fd.getId());
    }

    // ---------- 工具 ----------

    private String generateFormNo(String panelCode) {
        // 每张单据独立编号前缀：生产加工单 MO-…、销售订单 SO-…（对齐 T+ 单据独立存在）
        String biz = "SO_ORDER".equals(panelCode) ? "SO-" : "MO-";
        String prefix = biz + LocalDate.now().format(DateTimeFormatter.ofPattern("yyyy-MM-dd")) + "-";
        long count = formMapper.selectCount(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, panelCode)
                .likeRight(FormData::getFormNo, prefix));
        return prefix + String.format("%04d", count + 1);
    }

    private Map<String, Object> parseData(String s) {
        if (s == null || s.isBlank()) return new HashMap<>();
        try {
            return json.readValue(s, new TypeReference<Map<String, Object>>() {});
        } catch (Exception e) {
            return new HashMap<>();
        }
    }

    private String toJson(Object o) {
        try {
            return json.writeValueAsString(o == null ? new ArrayList<>() : o);
        } catch (Exception e) {
            return "[]";
        }
    }
}
