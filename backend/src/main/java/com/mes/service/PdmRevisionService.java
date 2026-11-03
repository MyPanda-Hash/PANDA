package com.mes.service;

import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Objects;
import java.util.Set;

/**
 * PDM 修订与变更：已发布修订不可原地覆盖，修改产生新修订并保留审计链。
 *
 * 核心约束（对应 docs/backend/后端逻辑设计.md §11）：
 * - 产品身份与修订版分离，业务引用指向明确修订版
 * - 已发布修订不可原地覆盖；修改产生新修订
 * - 变更单记录变更前后的差异，可追溯
 * - 同一链只允许一个生效版本
 */
@Service
public class PdmRevisionService {

    /** 一条修订：版本号、父单号（链首）、是否生效。 */
    public record Revision(String formNo, int version, String parentNo,
                           boolean current, LocalDateTime createdAt) {
        /** 兼容简化构造：不带时间。 */
        public Revision(String formNo, int version, String parentNo, boolean current) {
            this(formNo, version, parentNo, current, LocalDateTime.now());
        }
    }

    /** 变更前/后的一行：物料、名称、数量、损耗率。 */
    public record ChangeRow(String itemCode, String itemName, String qty, String scrapRate) {}

    /** 一条差异：物料 + 变更前后 + 变更类型。 */
    public record Diff(String itemCode, String beforeQty, String afterQty, String changeType) {}

    public PdmRevisionService() {
    }

    /**
     * 生成下一个修订。
     * 父单号始终指向链首（根单），便于按根单聚合整条修订链。
     */
    public Revision nextRevision(Revision parent) {
        if (parent == null) {
            return new Revision(null, 1, null, true, LocalDateTime.now());
        }
        String root = parent.parentNo() == null || parent.parentNo().isBlank()
                ? parent.formNo() : parent.parentNo();
        return new Revision(parent.formNo(), parent.version() + 1, root,
                true, LocalDateTime.now());
    }

    /** 旧版本标记为已取代：不再生效，但保留历史。 */
    public Revision markSuperseded(Revision old) {
        return new Revision(old.formNo(), old.version(), old.parentNo(), false, old.createdAt());
    }

    /**
     * 变更前置校验：只有已审核且当前生效的修订才能被变更。
     * 草稿不能变更（应先审核），已取代版本不能变更（应在最新版上改）。
     */
    public void assertCanRevise(String status, String formNo) {
        assertCanRevise(status, formNo, true);
    }

    public void assertCanRevise(String status, String formNo, boolean current) {
        if (!"已审核".equals(status)) {
            throw new IllegalStateException("仅已审核的修订可变更：" + formNo + "（当前 " + status + "）");
        }
        if (!current) {
            throw new IllegalStateException("该修订已被新版本取代，请在最新版本上变更：" + formNo);
        }
    }

    /** 比对单行差异：增 / 删 / 改 / 不变。 */
    public Diff diff(String itemCode, ChangeRow before, ChangeRow after) {
        if (before == null && after == null) {
            return new Diff(itemCode, null, null, "不变");
        }
        if (before == null) {
            return new Diff(itemCode, null, after.qty(), "增");
        }
        if (after == null) {
            return new Diff(itemCode, before.qty(), null, "删");
        }
        boolean same = Objects.equals(nz(before.qty()), nz(after.qty()))
                && Objects.equals(nz(before.scrapRate()), nz(after.scrapRate()));
        return new Diff(itemCode, before.qty(), after.qty(), same ? "不变" : "改");
    }

    /** 影响分析：哪些下游单据引用了该修订。去重且保持顺序。 */
    public List<String> impactOf(String formNo, List<String> references) {
        List<String> out = new ArrayList<>();
        if (formNo == null || formNo.isBlank() || references == null) return out;
        Set<String> seen = new LinkedHashSet<>();
        for (String ref : references) {
            if (ref == null || !ref.equals(formNo)) continue;
            seen.add(ref);
        }
        out.addAll(seen);
        return out;
    }

    /** 变更单号：ECN-根单号-V版本号。 */
    public String changeNoOf(String rootFormNo, int version) {
        if (rootFormNo == null || rootFormNo.isBlank()) return null;
        return "ECN-" + rootFormNo + "-V" + version;
    }

    private static String nz(String value) {
        return value == null ? "" : value.trim();
    }
}
