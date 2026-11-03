package com.mes.service;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 备份文件名校验：只放行本服务生成的 *.sql.gz，阻断路径穿越 */
class DbBackupServiceTest {

    @Test
    void acceptsGeneratedFileName() {
        assertTrue(DbBackupService.isSafeFileName("light_mes-20261021-174500.sql.gz"));
    }

    @Test
    void rejectsPathTraversal() {
        assertFalse(DbBackupService.isSafeFileName("../etc/passwd"));
        assertFalse(DbBackupService.isSafeFileName("/etc/passwd"));
        assertFalse(DbBackupService.isSafeFileName("a/../../b.sql.gz"));
        assertFalse(DbBackupService.isSafeFileName(".."));
        assertFalse(DbBackupService.isSafeFileName("."));
    }

    @Test
    void rejectsNonBackupExtensions() {
        assertFalse(DbBackupService.isSafeFileName("light_mes.sql"));
        assertFalse(DbBackupService.isSafeFileName("x.sql.gz.exe"));
        assertFalse(DbBackupService.isSafeFileName("x.tar.gz"));
    }

    @Test
    void rejectsNullOrOversize() {
        assertFalse(DbBackupService.isSafeFileName(null));
        assertFalse(DbBackupService.isSafeFileName("a".repeat(300) + ".sql.gz"));
    }
}
