package com.mes.controller;

import com.mes.dto.ApiResult;
import com.mes.entity.SysBackupLog;
import com.mes.service.DbBackupService;
import com.mes.service.RoleService;
import org.springframework.core.io.FileSystemResource;
import org.springframework.core.io.Resource;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.nio.file.Path;
import java.util.List;
import java.util.Map;

/** 系统管理 · 数据安全：数据库主动备份与下载（仅管理员） */
@RestController
@RequestMapping("/api/sys/backup")
public class SysBackupController {

    private final DbBackupService backup;
    private final RoleService roleService;

    public SysBackupController(DbBackupService backup, RoleService roleService) {
        this.backup = backup;
        this.roleService = roleService;
    }

    @PostMapping("/run")
    public ApiResult<Map<String, Object>> run(Authentication auth) {
        if (!isAdmin(auth)) return ApiResult.fail(403, "仅管理员可操作");
        return ApiResult.ok(backup.runNow(auth.getName()));
    }

    @GetMapping("/list")
    public ApiResult<List<SysBackupLog>> list(Authentication auth,
                                              @RequestParam(defaultValue = "20") int limit) {
        if (!isAdmin(auth)) return ApiResult.fail(403, "仅管理员可操作");
        return ApiResult.ok(backup.logs(limit));
    }

    @GetMapping("/download/{fileName}")
    public ResponseEntity<Resource> download(Authentication auth, @PathVariable String fileName) {
        if (!isAdmin(auth)) return ResponseEntity.status(403).build();
        Path f = backup.resolveExisting(fileName);
        if (f == null) return ResponseEntity.notFound().build();
        return ResponseEntity.ok()
                .header(HttpHeaders.CONTENT_DISPOSITION, "attachment; filename=\"" + fileName + "\"")
                .contentType(MediaType.APPLICATION_OCTET_STREAM)
                .body(new FileSystemResource(f));
    }

    private boolean isAdmin(Authentication auth) {
        return auth != null && roleService.isAdminUser(auth.getName());
    }
}
