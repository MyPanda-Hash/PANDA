package com.mes.openapi;

import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

import org.springframework.stereotype.Service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.mes.entity.FormApproval;
import com.mes.entity.FormData;
import com.mes.entity.PanelConfig;
import com.mes.entity.SysUser;
import com.mes.mapper.FormApprovalMapper;
import com.mes.mapper.FormDataMapper;
import com.mes.mapper.PanelConfigMapper;
import com.mes.mapper.SysUserMapper;
import com.mes.service.RoleService;

/**
 * 待审批单据的只读查询，供 AI 助手轮询。
 *
 * <p><b>最小暴露面</b>：只返回「谁该审哪张单」这一层元数据——面板编码/名称、单据编号、
 * 提交人、提交时间。单据正文（客户、金额、明细）一律不出这个接口，避免整库业务数据
 * 顺着一把令牌流出去。
 *
 * <p><b>精确到人</b>：审批人身份直接复用 MES 自身判定——{@link RoleService#getPerms(String)}
 * 里的 {@code approvePanels}，和门户待办的算法完全一致，不另立一套规则。
 */
@Service
public class OpenApprovalService {

    /** 只读投影白名单：这个接口能返回的字段就这些，新增字段必须显式加进来。 */
    static final List<String> PENDING_FIELDS = List.of(
            "panelCode", "panelName", "formNo", "submitter", "submittedAt");

    private static final int DEFAULT_LIMIT = 50;
    private static final int MAX_LIMIT = 200;
    private static final int APPROVAL_SCAN_LIMIT = 2000;
    private static final DateTimeFormatter TIME_FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    private final RoleService roleService;
    private final SysUserMapper userMapper;
    private final FormDataMapper formMapper;
    private final FormApprovalMapper approvalMapper;
    private final PanelConfigMapper panelMapper;

    public OpenApprovalService(RoleService roleService, SysUserMapper userMapper,
                               FormDataMapper formMapper, FormApprovalMapper approvalMapper,
                               PanelConfigMapper panelMapper) {
        this.roleService = roleService;
        this.userMapper = userMapper;
        this.formMapper = formMapper;
        this.approvalMapper = approvalMapper;
        this.panelMapper = panelMapper;
    }

    /** 有审批权限的账号及其面板。只返回账号名与面板编码，不含任何单据。 */
    public List<Map<String, Object>> listApprovers() {
        List<Map<String, Object>> out = new ArrayList<>();
        for (SysUser user : userMapper.selectList(new LambdaQueryWrapper<SysUser>()
                .eq(SysUser::getEnabled, 1))) {
            List<String> panels = approvePanels(user.getUserName());
            if (panels.isEmpty()) continue;
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("username", user.getUserName());
            item.put("displayName", user.getRealName());
            item.put("panels", panels);
            out.add(item);
        }
        return out;
    }

    /** 指定审批人待审的单据；approver 必须是显式传入，不支持"全库扫描"。 */
    public List<Map<String, Object>> pendingFor(String approver, Integer limit) {
        if (approver == null || approver.isBlank()) {
            throw new OpenApiException(400, "必须指定 approver");
        }
        int size = limit == null ? DEFAULT_LIMIT : Math.max(1, Math.min(limit, MAX_LIMIT));
        List<String> panels = approvePanels(approver);
        if (panels.isEmpty()) return List.of();

        List<FormData> forms = formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getStatus, "审批中")
                .in(FormData::getPanelCode, panels)
                .orderByDesc(FormData::getUpdateTime)
                .orderByDesc(FormData::getId)
                .last("LIMIT " + size));
        if (forms.isEmpty()) return List.of();

        Map<String, FormApproval> submissions = latestSubmissions(panels);
        Map<String, String> panelNames = panelNames(panels);

        List<Map<String, Object>> out = new ArrayList<>(forms.size());
        for (FormData form : forms) {
            String key = key(form.getPanelCode(), form.getFormNo());
            FormApproval submission = submissions.get(key);
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("panelCode", form.getPanelCode());
            item.put("panelName", panelNames.getOrDefault(form.getPanelCode(), form.getPanelCode()));
            item.put("formNo", form.getFormNo());
            item.put("submitter", submission != null ? submission.getOperator()
                    : (form.getCreateBy() == null ? "" : form.getCreateBy()));
            var time = submission != null ? submission.getCreateTime()
                    : (form.getUpdateTime() != null ? form.getUpdateTime() : form.getCreateTime());
            item.put("submittedAt", time == null ? "" : time.format(TIME_FMT));
            out.add(item);
        }
        return out;
    }

    private List<String> approvePanels(String approver) {
        Object value = roleService.getPerms(approver).get("approvePanels");
        if (!(value instanceof List<?> list)) return List.of();
        List<String> panels = new ArrayList<>();
        for (Object item : list) {
            String code = String.valueOf(item).trim();
            if (!code.isEmpty() && !panels.contains(code)) panels.add(code);
        }
        return panels;
    }

    private Map<String, FormApproval> latestSubmissions(List<String> panels) {
        Map<String, FormApproval> map = new HashMap<>();
        for (FormApproval record : approvalMapper.selectList(new LambdaQueryWrapper<FormApproval>()
                .eq(FormApproval::getAction, "SUBMIT")
                .eq(FormApproval::getResult, "PENDING")
                .in(FormApproval::getPanelCode, panels)
                .orderByDesc(FormApproval::getId)
                .last("LIMIT " + APPROVAL_SCAN_LIMIT))) {
            map.putIfAbsent(key(record.getPanelCode(), record.getFormNo()), record);
        }
        return map;
    }

    private Map<String, String> panelNames(List<String> panels) {
        Map<String, String> names = new HashMap<>();
        Set<String> codes = new HashSet<>(panels);
        for (PanelConfig panel : panelMapper.selectList(new LambdaQueryWrapper<PanelConfig>()
                .in(PanelConfig::getPanelCode, codes))) {
            names.put(panel.getPanelCode(),
                    panel.getPanelName() == null || panel.getPanelName().isBlank()
                            ? panel.getPanelCode() : panel.getPanelName());
        }
        return names;
    }

    private static String key(String panelCode, String formNo) {
        return panelCode + "#" + formNo;
    }
}
