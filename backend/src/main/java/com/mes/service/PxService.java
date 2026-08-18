package com.mes.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormApproval;
import com.mes.entity.FormData;
import com.mes.entity.PanelConfig;
import com.mes.mapper.FormApprovalMapper;
import com.mes.mapper.FormDataMapper;
import com.mes.mapper.PanelConfigMapper;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Set;
import java.util.List;
import java.util.Map;

@Service
public class PxService {

    private final PanelConfigMapper panelMapper;
    private final FormDataMapper formMapper;
    private final FormApprovalMapper approvalMapper;
    private final ObjectMapper json = new ObjectMapper();

    public PxService(PanelConfigMapper panelMapper, FormDataMapper formMapper, FormApprovalMapper approvalMapper) {
        this.panelMapper = panelMapper;
        this.formMapper = formMapper;
        this.approvalMapper = approvalMapper;
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
        out.put("panelName", panelNameOf(panelCode));
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
        out.put("panelName", panelNameOf(panelCode));
        return out;
    }

    private String panelNameOf(String panelCode) {
        Map<String, Object> cfg = loadConfig(panelCode);
        Object name = ((Map<String, Object>) cfg.get("metadata")).get("panelName");
        return name == null ? "表单" : String.valueOf(name);
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
        // 单据级行（一单一行，单号不重复）：明细挂在 detail 对象，key 与 detail.tabs 一致。
        // 对齐 docs/页面开发规范.md 数据契约；前端渲染器/选单弹窗按此结构消费。
        List<Map<String, Object>> flat = new ArrayList<>();
        for (FormData fd : all) {
            Map<String, Object> head = parseData(fd.getData());
            head.put("编号", fd.getFormNo());
            head.put("单据状态", fd.getStatus());
            head.put("创建时间", fd.getCreateTime());
            head.put("更新时间", fd.getUpdateTime());
            head.put("发起人编号", fd.getCreateBy());
            Map<String, Object> detail = parseData(fd.getDetailData());
            if (!detail.isEmpty()) head.put("detail", detail);
            flat.add(head);
        }
        // 条件过滤（仅标量字段参与比较，detail 对象跳过）
        List<Map<String, Object>> rows = new ArrayList<>();
        for (Map<String, Object> row : flat) {
            boolean hit = true;
            if (condition != null) {
                for (Map.Entry<String, Object> e : condition.entrySet()) {
                    Object v = e.getValue();
                    if (v == null || "".equals(String.valueOf(v))) continue;
                    Object rv = row.get(e.getKey());
                    if (rv == null || rv instanceof Map || rv instanceof List) continue;
                    if (!String.valueOf(rv).contains(String.valueOf(v))) { hit = false; break; }
                }
            }
            if (hit && keyword != null && !keyword.isBlank()) {
                hit = false;
                for (Object v : row.values()) {
                    if (v != null && !(v instanceof Map) && !(v instanceof List)
                            && String.valueOf(v).contains(keyword)) { hit = true; break; }
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
            case "保存":
            case "保存新增":
            case "保存为草稿":
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
            case "生成工序汇报单（自制汇报）":   // 推式生单：生产加工单 → 工序汇报单（拉取加工单工序明细）
                return createProcReportFromMo(panelCode, formData);
            // 审批流
            case "提交审批":
                return submitApproval(panelCode, formData);
            case "审批通过":
                return approveApproval(panelCode, formData);
            case "审批驳回":
                return rejectApproval(panelCode, formData);
            case "审批情况":
                return approvalHistory(panelCode, formData);
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
        // 生成材料明细：按产成品的存货 BOM（INV 类别单据物品 _bom）自动带出
        List<Map<String, Object>> materials = new ArrayList<>();
        Map<String, List<Map<String, Object>>> bomByItem = new HashMap<>();
        for (FormData idoc : formMapper.selectList(new LambdaQueryWrapper<FormData>().eq(FormData::getPanelCode, "INV"))) {
            Map<String, Object> idetail = parseData(idoc.getDetailData());
            Object iitems = idetail.get("items");
            if (iitems instanceof List) {
                for (Object o : (List<?>) iitems) {
                    if (!(o instanceof Map)) continue;
                    Map<String, Object> it = (Map<String, Object>) o;
                    Object bomObj = it.get("_bom");
                    if (bomObj == null || String.valueOf(bomObj).isBlank()) continue;
                    try {
                        List<Map<String, Object>> bom = json.readValue(String.valueOf(bomObj),
                                new TypeReference<List<Map<String, Object>>>() {});
                        bomByItem.put(String.valueOf(it.get("存货编码")), bom);
                    } catch (Exception ignore) {}
                }
            }
        }
        Set<String> matKeys = new HashSet<>();
        for (Map<String, Object> p : products) {
            String pcode = String.valueOf(p.getOrDefault("产品编码", ""));
            List<Map<String, Object>> bom = bomByItem.get(pcode);
            if (bom == null) continue;
            for (Map<String, Object> b : bom) {
                String mcode = String.valueOf(b.getOrDefault("材料编码", ""));
                if (mcode.isEmpty() || !matKeys.add(pcode + "|" + mcode)) continue;
                Map<String, Object> m = new HashMap<>();
                m.put("材料编码", mcode);
                m.put("材料名称", b.getOrDefault("材料名称", ""));
                m.put("规格型号", b.getOrDefault("规格型号", ""));
                m.put("计量单位", b.getOrDefault("计量单位", "kg"));
                m.put("定额需用数量", b.getOrDefault("定额需用数量", 0));
                m.put("损耗率%", b.getOrDefault("损耗率%", 0));
                m.put("子件BOM", pcode);
                m.put("预出仓库", "原料仓");
                m.put("材料倒冲方式", "按定额倒冲");
                m.put("领料工序", "下料");
                m.put("允许循环", false);
                m.put("行中止", false);
                m.put("定额生产数量", 1);
                m.put("需用数量", 0);
                m.put("损耗数量", 0);
                m.put("计划数量", 0);
                m.put("累计领用数量", 0);
                m.put("可用量", 0);
                m.put("可用量说明", "");
                m.put("现存量", 0);
                m.put("现存量说明", "");
                m.put("单重", 0);
                m.put("总重", 0);
                m.put("存货图片", "");
                materials.add(m);
            }
        }
        Map<String, Object> detail = new HashMap<>();
        detail.put("products", products);
        detail.put("materials", materials);
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
    /**
     * 推式生单：生产加工单（MANU_ORDER）→ 工序汇报单（PROCESS_REPORT）
     * 表头：加工单号/生产车间/产品/客户/销售订单号；明细 = 加工单工序明细（工序编码/车间/工作中心/设备/班组/工人/计划数量→报工数量/工价）
     */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createProcReportFromMo(String panelCode, Map<String, Object> formData) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData mo = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "MANU_ORDER")
                .eq(FormData::getFormNo, String.valueOf(no)));
        if (mo == null) throw new IllegalArgumentException("生产加工单不存在：" + no);
        String moStatus = mo.getStatus();
        if (!"已审核".equals(moStatus) && !"生产中".equals(moStatus)) {
            throw new IllegalStateException("仅已审核/生产中的生产加工单可生成工序汇报单");
        }

        Map<String, Object> head = parseData(mo.getData());
        Map<String, Object> detailMap = parseData(mo.getDetailData());
        List<Map<String, Object>> products = detailMap.get("products") instanceof List
                ? (List<Map<String, Object>>) detailMap.get("products") : new ArrayList<>();
        List<Map<String, Object>> procs = detailMap.get("processes") instanceof List
                ? (List<Map<String, Object>>) detailMap.get("processes") : new ArrayList<>();
        if (products.isEmpty()) throw new IllegalStateException("生产加工单无产成品明细：" + no);
        Map<String, Object> p0 = products.get(0);

        String newNo = generateFormNo("PROCESS_REPORT");
        Map<String, Object> prData = new HashMap<>();
        prData.put("单据日期", LocalDate.now().toString());
        prData.put("业务类型", "工序汇报");
        prData.put("加工单号", String.valueOf(no));
        prData.put("生产车间", head.getOrDefault("生产车间", ""));
        prData.put("产品编码", p0.getOrDefault("产品编码", ""));
        prData.put("产品名称", p0.getOrDefault("产品名称", ""));
        prData.put("规格型号", p0.getOrDefault("规格型号", ""));
        prData.put("销售订单号", head.getOrDefault("销售订单号", ""));
        prData.put("客户", head.getOrDefault("客户", ""));
        prData.put("匹配来源单号", String.valueOf(no));

        List<Map<String, Object>> items = new ArrayList<>();
        for (Map<String, Object> pr : procs) {
            Map<String, Object> it = new HashMap<>();
            it.put("加工单号", String.valueOf(no));
            it.put("产品编码", p0.getOrDefault("产品编码", ""));
            it.put("产品名称", p0.getOrDefault("产品名称", ""));
            it.put("规格型号", p0.getOrDefault("规格型号", ""));
            it.put("工艺类型", pr.getOrDefault("工艺类型", "自制"));
            it.put("工艺序号", pr.getOrDefault("工艺序号", 0));
            it.put("加工顺序", pr.getOrDefault("加工顺序", 0));
            it.put("工艺路线", "");
            it.put("工序编码", pr.getOrDefault("工序编码", ""));
            it.put("工序名称", pr.getOrDefault("工序名称", ""));
            it.put("工序备注", pr.getOrDefault("工序备注", ""));
            it.put("生产车间", pr.getOrDefault("生产车间", ""));
            it.put("工作中心", pr.getOrDefault("工作中心", ""));
            it.put("设备", pr.getOrDefault("设备", ""));
            it.put("班组名称", pr.getOrDefault("班组", ""));
            it.put("工人名称", pr.getOrDefault("工人", ""));
            it.put("工序单位", pr.getOrDefault("工序单位", "件"));
            it.put("报工数量", pr.getOrDefault("计划数量", 0));
            it.put("可报工数量", pr.getOrDefault("计划数量", 0));
            it.put("合格数量", 0);
            it.put("不合格数量", 0);
            it.put("工资类型", pr.getOrDefault("工资类型", "计件"));
            it.put("工价", pr.getOrDefault("工价", 0));
            it.put("计时/计件金额", 0);
            it.put("调整工资", 0);
            it.put("金额", 0);
            it.put("单位标准工时", pr.getOrDefault("单位标准工时", 0));
            it.put("实际工时", 0);
            it.put("计划时间", pr.getOrDefault("计划时间", ""));
            it.put("完成时间", pr.getOrDefault("完成时间", ""));
            it.put("手工完工", false);
            it.put("委外供应商", pr.getOrDefault("委外供应商", ""));
            it.put("委外单价", pr.getOrDefault("委外单价", 0));
            it.put("税率%", pr.getOrDefault("税率%", 13));
            it.put("委外含税单价", 0);
            it.put("委外金额", 0);
            it.put("委外税额", 0);
            it.put("委外含税金额", 0);
            it.put("累计汇报数量", 0);
            it.put("需求令号", pr.getOrDefault("需求令号", p0.getOrDefault("需求令号", "")));
            it.put("图号", p0.getOrDefault("图号", ""));
            it.put("备注", "");
            items.add(it);
        }
        if (items.isEmpty()) throw new IllegalStateException("生产加工单无工序明细：" + no);

        Map<String, Object> detail = new HashMap<>();
        detail.put("items", items);

        FormData pr = new FormData();
        pr.setPanelCode("PROCESS_REPORT");
        pr.setFormNo(newNo);
        pr.setData(toJson(prData));
        pr.setDetailData(toJson(detail));
        pr.setStatus("草稿");
        pr.setCreateBy("admin");
        pr.setCreateTime(LocalDateTime.now());
        formMapper.insert(pr);

        Map<String, Object> out = new HashMap<>();
        out.put("单据状态", "草稿");
        out.put("gotoPanel", "PROCESS_REPORT");
        out.put("编号", newNo);
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
            // 单据=草稿可保存；基础档案（存货/部门等）状态为 启用/停用 同样允许保存
            String st0 = fd.getStatus() == null ? "" : fd.getStatus();
            if (!"草稿".equals(st0) && !"启用".equals(st0) && !"停用".equals(st0)) {
                throw new IllegalStateException("仅草稿状态可保存");
            }
            fd.setData(toJson(formData));
            fd.setDetailData(toJson(detail));
            fd.setUpdateTime(LocalDateTime.now());
            formMapper.updateById(fd);
        } else {
            fd = new FormData();
            fd.setPanelCode(panelCode);
            // 存货新单：按类别前缀编号（产成品 CP-、原材料 YL-、辅助材料 FZ-、包装物 BZ-、半成品 BC-）
            fd.setFormNo("INV".equals(panelCode)
                    ? invNo(formData.get("类别") == null ? "" : String.valueOf(formData.get("类别")))
                    : generateFormNo(panelCode));
            fd.setData(toJson(formData));
            fd.setDetailData(toJson(detail));
            fd.setStatus("INV".equals(panelCode) ? "启用" : "草稿"); // 存货类别单据初始状态=启用
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
    /** 当前登录用户（JwtAuthFilter 写入 SecurityContext；未认证时兜底 system） */
    private String currentUserName() {
        var auth = SecurityContextHolder.getContext().getAuthentication();
        if (auth != null && auth.getName() != null && !auth.getName().isBlank()) return auth.getName();
        return "system";
    }

    private Map<String, Object> changeStatus(String panelCode, Map<String, Object> formData, String action) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData fd = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, panelCode)
                .eq(FormData::getFormNo, String.valueOf(no)));
        if (fd == null) throw new IllegalArgumentException("表单数据不存在：" + no);
        if ("审核".equals(action)) {
            if (!"草稿".equals(fd.getStatus())) throw new IllegalStateException("仅草稿状态可审核");
            // 人工审核：审核人 = 当前登录人（不再硬编码 admin）
            String operator = currentUserName();
            fd.setStatus("已审核");
            fd.setAuditBy(operator);
            fd.setAuditTime(LocalDateTime.now());
            // 审核人/审核时间/审核意见 写入表头 JSON（前端表尾/列表直接展示）
            Map<String, Object> head = parseData(fd.getData());
            head.put("审核人", operator);
            head.put("审核时间", fd.getAuditTime().format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss")));
            Object opinion = formData.get("审核意见");
            if (opinion != null && !String.valueOf(opinion).isBlank()) head.put("审核意见", String.valueOf(opinion));
            fd.setData(toJson(head));
        } else if ("弃审".equals(action)) {
            if (!"已审核".equals(fd.getStatus())) throw new IllegalStateException("仅已审核状态可弃审");
            Map<String, Object> head = parseData(fd.getData());
            head.remove("审核人");
            head.remove("审核时间");
            head.remove("审核意见");
            // 显式 set(null)：MyBatis-Plus 默认 NOT_NULL 策略不会把 null 字段写进 UPDATE，
            // 必须用 LambdaUpdateWrapper.set 强制清空 audit_by/audit_time
            formMapper.update(null, new LambdaUpdateWrapper<FormData>()
                    .eq(FormData::getId, fd.getId())
                    .set(FormData::getStatus, "草稿")
                    .set(FormData::getAuditBy, null)
                    .set(FormData::getAuditTime, null)
                    .set(FormData::getData, toJson(head))
                    .set(FormData::getUpdateTime, LocalDateTime.now()));
            return new HashMap<String, Object>() {{
                put("编号", fd.getFormNo());
                put("单据状态", "草稿");
            }};
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

    // ---------- 审批流：提交审批 → 审批中 → 审批通过/审批驳回（全留痕，可扩展多级） ----------

    private FormData formOf(String panelCode, Object no) {
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData fd = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, panelCode)
                .eq(FormData::getFormNo, String.valueOf(no)));
        if (fd == null) throw new IllegalArgumentException("表单数据不存在：" + no);
        return fd;
    }

    private String opinionOf(Map<String, Object> formData) {
        Object v = formData == null ? null : formData.get("审批意见");
        return v == null ? "" : String.valueOf(v).trim();
    }

    private void recordApproval(String panelCode, String formNo, String action, String result, String opinion) {
        FormApproval rec = new FormApproval();
        rec.setPanelCode(panelCode);
        rec.setFormNo(formNo);
        rec.setAction(action);
        rec.setResult(result);
        rec.setNodeNo(1);
        rec.setOperator(currentUserName());
        rec.setOpinion(opinion.isEmpty() ? null : opinion);
        rec.setCreateTime(LocalDateTime.now());
        approvalMapper.insert(rec);
    }

    /** 提交审批：仅草稿 → 审批中 */
    private Map<String, Object> submitApproval(String panelCode, Map<String, Object> formData) {
        FormData fd = formOf(panelCode, formData.get("编号"));
        if (!"草稿".equals(fd.getStatus())) throw new IllegalStateException("仅草稿状态可提交审批");
        String operator = currentUserName();
        String now = LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss"));
        fd.setStatus("审批中");
        fd.setUpdateTime(LocalDateTime.now());
        Map<String, Object> head = parseData(fd.getData());
        head.put("审批状态", "审批中");
        head.put("提交人", operator);
        head.put("提交时间", now);
        fd.setData(toJson(head));
        formMapper.updateById(fd);
        recordApproval(panelCode, fd.getFormNo(), "SUBMIT", "PENDING", opinionOf(formData));
        Map<String, Object> out = new HashMap<>();
        out.put("编号", fd.getFormNo());
        out.put("单据状态", "审批中");
        return out;
    }

    /** 审批通过：仅审批中 → 已审核（审核人=当前登录人，写审核人/时间/意见） */
    private Map<String, Object> approveApproval(String panelCode, Map<String, Object> formData) {
        FormData fd = formOf(panelCode, formData.get("编号"));
        if (!"审批中".equals(fd.getStatus())) throw new IllegalStateException("仅审批中状态可审批通过");
        String operator = currentUserName();
        String opinion = opinionOf(formData);
        fd.setStatus("已审核");
        fd.setAuditBy(operator);
        fd.setAuditTime(LocalDateTime.now());
        Map<String, Object> head = parseData(fd.getData());
        head.put("审批状态", "已通过");
        head.put("审核人", operator);
        head.put("审核时间", fd.getAuditTime().format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss")));
        if (!opinion.isEmpty()) head.put("审核意见", opinion);
        fd.setData(toJson(head));
        formMapper.updateById(fd);
        recordApproval(panelCode, fd.getFormNo(), "APPROVE", "APPROVED", opinion);
        Map<String, Object> out = new HashMap<>();
        out.put("编号", fd.getFormNo());
        out.put("单据状态", "已审核");
        return out;
    }

    /** 审批驳回：仅审批中 → 草稿（意见必填，驳回后修改可重新提交） */
    private Map<String, Object> rejectApproval(String panelCode, Map<String, Object> formData) {
        FormData fd = formOf(panelCode, formData.get("编号"));
        if (!"审批中".equals(fd.getStatus())) throw new IllegalStateException("仅审批中状态可审批驳回");
        String opinion = opinionOf(formData);
        if (opinion.isEmpty()) throw new IllegalStateException("审批驳回必须填写审批意见");
        fd.setStatus("草稿");
        fd.setUpdateTime(LocalDateTime.now());
        Map<String, Object> head = parseData(fd.getData());
        head.put("审批状态", "已驳回");
        head.remove("提交人");
        head.remove("提交时间");
        fd.setData(toJson(head));
        formMapper.updateById(fd);
        recordApproval(panelCode, fd.getFormNo(), "REJECT", "REJECTED", opinion);
        Map<String, Object> out = new HashMap<>();
        out.put("编号", fd.getFormNo());
        out.put("单据状态", "草稿");
        return out;
    }

    /** 审批情况：返回该单据全部审批记录（按时间升序） */
    private Map<String, Object> approvalHistory(String panelCode, Map<String, Object> formData) {
        FormData fd = formOf(panelCode, formData.get("编号"));
        Map<String, Object> out = new HashMap<>();
        out.put("编号", fd.getFormNo());
        out.put("list", getApprovalHistory(panelCode, fd.getFormNo()));
        return out;
    }

    public List<Map<String, Object>> getApprovalHistory(String panelCode, String formNo) {
        List<FormApproval> recs = approvalMapper.selectList(new LambdaQueryWrapper<FormApproval>()
                .eq(FormApproval::getPanelCode, panelCode)
                .eq(FormApproval::getFormNo, formNo)
                .orderByAsc(FormApproval::getId));
        List<Map<String, Object>> out = new ArrayList<>();
        for (FormApproval r : recs) {
            Map<String, Object> m = new HashMap<>();
            m.put("action", r.getAction());
            m.put("result", r.getResult());
            m.put("operator", r.getOperator());
            m.put("opinion", r.getOpinion());
            m.put("nodeNo", r.getNodeNo());
            m.put("createTime", r.getCreateTime() == null ? ""
                    : r.getCreateTime().format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss")));
            out.add(m);
        }
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
        // 单据=草稿可删；基础档案（存货/部门等）状态为 启用/停用 同样允许删除
        String st1 = fd.getStatus() == null ? "" : fd.getStatus();
        if (!"草稿".equals(st1) && !"启用".equals(st1) && !"停用".equals(st1)) {
            throw new IllegalStateException("仅草稿状态可删除");
        }
        formMapper.deleteById(fd.getId());
    }

    // ---------- 工具 ----------

    // 存货类别 → 单据编号前缀（5 类别缩写）
    private static final Map<String, String> INV_PRE = new HashMap<>();
    static {
        INV_PRE.put("产成品", "CP");
        INV_PRE.put("原材料", "YL");
        INV_PRE.put("辅助材料", "FZ");
        INV_PRE.put("包装物", "BZ");
        INV_PRE.put("半成品", "BC");
    }

    /** 存货新类别单据编号：类别前缀-3位序号（如 CP-002 / BC-001） */
    private String invNo(String cat) {
        String pre = INV_PRE.getOrDefault(cat == null ? "" : cat, "INV");
        long count = formMapper.selectCount(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "INV")
                .likeRight(FormData::getFormNo, pre + "-"));
        return pre + "-" + String.format("%03d", count + 1);
    }

    private String generateFormNo(String panelCode) {
        // 单据编号：前缀-yyyy-MM-dd+2位当日序号（如 MO-2026-08-1701 / SO-2026-08-1701）
        // 每天从 01 顺序排；精确查重确保单据号不允许重复（全局唯一）
        String biz;
        if ("SO_ORDER".equals(panelCode)) biz = "SO-";
        else if ("MANU_ORDER".equals(panelCode)) biz = "MO-";
        else if ("FINISH_IN".equals(panelCode)) biz = "FI-";       // 产成品入库单
        else if ("PURCHASE_IN".equals(panelCode)) biz = "RK-";     // 采购入库单（T+ 入库惯例）
        else if ("SALE_OUT".equals(panelCode)) biz = "CK-";        // 销售出库单（T+ 出库惯例）
        else if ("PROCESS_REPORT".equals(panelCode)) biz = "GX-";  // 工序汇报单
        else if ("INV".equals(panelCode)) biz = "INV-"; // 存货类别单据
        else biz = "MO-";
        String base = biz + LocalDate.now().format(DateTimeFormatter.ofPattern("yyyy-MM-dd"));
        int seq = 1;
        String no;
        do {
            no = base + String.format("%02d", seq++);
        } while (formMapper.selectCount(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, panelCode)
                .eq(FormData::getFormNo, no)) > 0);
        return no;
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
