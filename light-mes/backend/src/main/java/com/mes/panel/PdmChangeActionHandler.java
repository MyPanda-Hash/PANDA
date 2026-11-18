package com.mes.panel;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.mes.entity.FormData;
import com.mes.mapper.FormDataMapper;
import com.mes.service.PdmRevisionService;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * PDM 变更动作：BOM / 工艺路线的「变更」按钮。
 *
 * 这是 PanelActionHandler 扩展点的首个实现——此前该接口 0 个实现类。
 * 语义：已审核且当前生效的修订，复制出一份新草稿修订，父单号指向链首，
 * 旧版本标记为已取代，形成可追溯的修订链。
 */
@Component
public class PdmChangeActionHandler implements PanelActionHandler {

    /** 支持变更的修订类面板。 */
    private static final List<String> REVISION_PANELS = List.of("BOM", "ROUTE");

    private final FormDataMapper formMapper;
    private final PdmRevisionService revisionService;

    public PdmChangeActionHandler(FormDataMapper formMapper, PdmRevisionService revisionService) {
        this.formMapper = formMapper;
        this.revisionService = revisionService;
    }

    @Override
    public boolean supports(String panelCode, String action) {
        return "变更".equals(action) && REVISION_PANELS.contains(panelCode);
    }

    @Override
    public Map<String, Object> handle(PanelActionContext context) {
        Object no = context.formData() == null ? null : context.formData().get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        String formNo = String.valueOf(no);

        FormData source = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, context.panelCode())
                .eq(FormData::getFormNo, formNo));
        if (source == null) throw new IllegalArgumentException("单据不存在：" + formNo);

        boolean current = source.getCurrentVersion() == null || source.getCurrentVersion();
        revisionService.assertCanRevise(source.getStatus(), formNo, current);

        // 计算新修订号：同链最大版本 + 1
        String root = source.getParentNo() == null || source.getParentNo().isBlank()
                ? formNo : source.getParentNo();
        int maxVersion = 1;
        List<FormData> chain = formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, context.panelCode())
                .and(w -> w.eq(FormData::getParentNo, root).or().eq(FormData::getFormNo, root)));
        for (FormData row : chain) {
            if (row.getVersion() != null && row.getVersion() > maxVersion) maxVersion = row.getVersion();
        }
        int nextVersion = maxVersion + 1;
        String newNo = root + "-V" + nextVersion;

        // 旧版本标记为已取代（保留历史，不删除）。
        // 用 UpdateWrapper 写列名而非 LambdaUpdateWrapper：后者依赖 MyBatis-Plus 的
        // lambda 元数据缓存，在纯单元测试环境不可用。
        formMapper.update(null, new com.baomidou.mybatisplus.core.conditions.update.UpdateWrapper<FormData>()
                .eq("id", source.getId())
                .set("current_version", false)
                .set("update_time", LocalDateTime.now()));

        // 生成新修订草稿：内容复制自旧版，父单号指向链首
        FormData draft = new FormData();
        draft.setPanelCode(source.getPanelCode());
        draft.setFormNo(newNo);
        draft.setData(source.getData());
        draft.setDetailData(source.getDetailData());
        draft.setStatus("草稿");
        draft.setVersion(nextVersion);
        draft.setParentNo(root);
        draft.setCurrentVersion(true);
        draft.setCreateBy(context.userName());
        draft.setCreateTime(LocalDateTime.now());
        draft.setUpdateTime(LocalDateTime.now());
        formMapper.insert(draft);

        Map<String, Object> out = new HashMap<>();
        out.put("编号", newNo);
        out.put("单据状态", "草稿");
        out.put("版本", nextVersion);
        out.put("父单号", root);
        out.put("变更单号", revisionService.changeNoOf(root, nextVersion));
        out.put("原单号", formNo);
        out.put("原单已取代", true);
        out.put("gotoPanel", context.panelCode());
        return out;
    }
}
