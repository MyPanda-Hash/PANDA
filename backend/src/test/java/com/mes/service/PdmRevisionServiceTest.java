package com.mes.service;

import org.junit.jupiter.api.Test;

import java.time.LocalDateTime;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * PDM 修订与变更：已发布修订不可原地覆盖，修改产生新修订并保留审计链。
 * 覆盖：修订号递增、父单号链、生效版本标记、变更前后快照、影响分析。
 */
class PdmRevisionServiceTest {

    private final PdmRevisionService service = new PdmRevisionService();

    // ---------- 修订链 ----------

    @Test
    void firstRevisionStartsAtOne() {
        PdmRevisionService.Revision r = service.nextRevision(null);

        assertEquals(1, r.version());
        assertNull(r.parentNo());
        assertTrue(r.current());
    }

    @Test
    void childRevisionIncrementsFromParent() {
        PdmRevisionService.Revision parent = new PdmRevisionService.Revision("BOM-001", 1, null, true);

        PdmRevisionService.Revision child = service.nextRevision(parent);

        assertEquals(2, child.version());
        assertEquals("BOM-001", child.parentNo(), "父单号应指向被变更原单");
        assertTrue(child.current());
    }

    @Test
    void parentBecomesSupersededWhenChildCreated() {
        PdmRevisionService.Revision parent = new PdmRevisionService.Revision("BOM-001", 1, null, true);

        PdmRevisionService.Revision superseded = service.markSuperseded(parent);

        assertFalse(superseded.current(), "旧版本不再生效");
        assertEquals("BOM-001", superseded.formNo());
        assertEquals(1, superseded.version());
    }

    @Test
    void headerRevisionStaysOnChainRoot() {
        PdmRevisionService.Revision v1 = new PdmRevisionService.Revision("BOM-001", 1, null, true);
        PdmRevisionService.Revision v2 = service.nextRevision(v1);
        PdmRevisionService.Revision v3 = service.nextRevision(v2);

        assertEquals("BOM-001", v3.parentNo(), "孙版本仍指向链首，便于按根单聚合");
        assertEquals(3, v3.version());
    }

    // ---------- 变更校验 ----------

    @Test
    void cannotReviseDraft() {
        IllegalStateException e = assertThrows(IllegalStateException.class,
                () -> service.assertCanRevise("草稿", "BOM-001"));
        assertTrue(e.getMessage().contains("已审核"));
    }

    @Test
    void canReviseApproved() {
        service.assertCanRevise("已审核", "BOM-001");
    }

    @Test
    void cannotReviseSuperseded() {
        IllegalStateException e = assertThrows(IllegalStateException.class,
                () -> service.assertCanRevise("已审核", "BOM-001", false));
        assertTrue(e.getMessage().contains("取代"));
    }

    @Test
    void canReviseWhenCurrent() {
        service.assertCanRevise("已审核", "BOM-001", true);
    }

    // ---------- 变更前后快照 ----------

    @Test
    void changeSnapshotComparesQuantities() {
        PdmRevisionService.ChangeRow before = new PdmRevisionService.ChangeRow(
                "CL001", "铝棒", "2", "0");
        PdmRevisionService.ChangeRow after = new PdmRevisionService.ChangeRow(
                "CL001", "铝棒", "2.5", "0");

        PdmRevisionService.Diff diff = service.diff("CL001", before, after);

        assertEquals("CL001", diff.itemCode());
        assertEquals("2", diff.beforeQty());
        assertEquals("2.5", diff.afterQty());
        assertEquals("改", diff.changeType());
    }

    @Test
    void addedRowIsMarked() {
        PdmRevisionService.ChangeRow after = new PdmRevisionService.ChangeRow("CL002", "铝锭", "1", "0");

        PdmRevisionService.Diff diff = service.diff("CL002", null, after);

        assertEquals("增", diff.changeType());
        assertNull(diff.beforeQty());
        assertEquals("1", diff.afterQty());
    }

    @Test
    void removedRowIsMarked() {
        PdmRevisionService.ChangeRow before = new PdmRevisionService.ChangeRow("CL003", "切削液", "0.5", "0");

        PdmRevisionService.Diff diff = service.diff("CL003", before, null);

        assertEquals("删", diff.changeType());
        assertEquals("0.5", diff.beforeQty());
        assertNull(diff.afterQty());
    }

    @Test
    void identicalRowsAreUnchanged() {
        PdmRevisionService.ChangeRow a = new PdmRevisionService.ChangeRow("CL001", "铝棒", "2", "5");
        PdmRevisionService.ChangeRow b = new PdmRevisionService.ChangeRow("CL001", "铝棒", "2", "5");

        PdmRevisionService.Diff diff = service.diff("CL001", a, b);

        assertEquals("不变", diff.changeType());
    }

    // ---------- 影响分析 ----------

    @Test
    void impactListsAffectedDocuments() {
        // 引用去重：同一单号被多次引用只算一行
        List<String> affected = service.impactOf("BOM-001", List.of("BOM-001", "BOM-001", "BOM-002"));

        assertEquals(1, affected.size());
        assertEquals("BOM-001", affected.get(0));
    }

    @Test
    void impactIsEmptyWhenNoReference() {
        assertTrue(service.impactOf("BOM-999", List.of("BOM-001", "BOM-002")).isEmpty());
    }

    @Test
    void impactIgnoresNullOrEmpty() {
        assertTrue(service.impactOf(null, List.of("BOM-001")).isEmpty());
        assertTrue(service.impactOf("BOM-001", List.of()).isEmpty());
    }

    // ---------- 变更单号 ----------

    @Test
    void changeNoDerivedFromRootAndVersion() {
        assertEquals("ECN-BOM-001-V2", service.changeNoOf("BOM-001", 2));
    }

    @Test
    void changeNoHandlesBlankRoot() {
        assertNull(service.changeNoOf("", 1));
        assertNull(service.changeNoOf(null, 1));
    }

    @Test
    void revisionTimestampIsSet() {
        PdmRevisionService.Revision r = service.nextRevision(null);

        assertTrue(r.createdAt() != null, "修订应带创建时间");
        assertTrue(r.createdAt().isBefore(LocalDateTime.now().plusMinutes(1)));
    }
}
