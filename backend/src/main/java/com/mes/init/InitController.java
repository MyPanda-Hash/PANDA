package com.mes.init;

import java.io.IOException;
import java.util.List;
import java.util.Map;

import org.springframework.http.ContentDisposition;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

import com.mes.dto.ApiResult;
import com.mes.service.RoleService;

/**
 * 系统初始化向导接口（仅管理员）。
 *
 * <p>流程：{@code GET /state} 看状态 → {@code GET /template/{module}} 下模板 →
 * {@code POST /upload} 上传解析校验 → （P1）{@code POST /apply} 落库 →
 * （P2）{@code POST /opening/confirm} 期初核对落库 → {@code POST /finish} 锁定账套。
 *
 * <p>一次性锁（决策 D8）在服务层拦截：完成后再调任何导入接口返回 409。
 */
@RestController
@RequestMapping("/api/init")
public class InitController {

    private final InitService service;
    private final RoleService roleService;

    public InitController(InitService service, RoleService roleService) {
        this.service = service;
        this.roleService = roleService;
    }

    @GetMapping("/state")
    public ApiResult<Map<String, Object>> state(Authentication auth) {
        guard(auth);
        return ApiResult.ok(service.state());
    }

    /** 下载模板：文件名用中文（组织架构.xlsx / 存货.xlsx …），客户不用猜该填哪张表。 */
    @GetMapping("/template/{module}")
    public ResponseEntity<byte[]> template(@PathVariable String module, Authentication auth) throws IOException {
        guard(auth);
        InitModule m = InitModule.of(module);
        byte[] body = service.template(module);
        return ResponseEntity.ok()
                .header(HttpHeaders.CONTENT_DISPOSITION, ContentDisposition.attachment()
                        .filename(m.fileName(), java.nio.charset.StandardCharsets.UTF_8).build().toString())
                .contentType(MediaType.parseMediaType(
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
                .body(body);
    }

    /** 上传一个模块（分批上传）：返回行数据 + 逐行错误/警告，前端据此展示预览。 */
    @PostMapping("/upload")
    public ApiResult<Map<String, Object>> upload(@RequestParam String module,
                                                 @RequestParam("file") MultipartFile file,
                                                 Authentication auth) throws IOException {
        guard(auth);
        return ApiResult.ok(service.upload(module, file, auth.getName()));
    }

    /** 落库（P1）：当前明确拒绝并说明，避免半成品写脏库。 */
    @PostMapping("/apply")
    public ApiResult<Map<String, Object>> apply(@RequestBody Map<String, Object> body, Authentication auth) {
        guard(auth);
        String module = String.valueOf(body.getOrDefault("module", ""));
        List<Map<String, String>> rows = InitService.stringRows((List<?>) body.getOrDefault("rows", List.of()));
        return ApiResult.ok(service.apply(module, rows, auth.getName()));
    }

    /** 期初库存：核对（可改）后确认落库，同时写证据表。 */
    @PostMapping("/opening/confirm")
    public ApiResult<Map<String, Object>> openingConfirm(@RequestBody Map<String, Object> body,
                                                         Authentication auth) {
        guard(auth);
        List<Map<String, String>> rows = InitService.stringRows((List<?>) body.getOrDefault("rows", List.of()));
        String fileName = String.valueOf(body.getOrDefault("fileName", ""));
        return ApiResult.ok(service.openingConfirm(rows, fileName, auth.getName()));
    }

    /** 期初证据：原始上传值 / 人工确认值 / 操作人 / 时间。 */
    @GetMapping("/opening/evidence")
    public ApiResult<Map<String, Object>> openingEvidence(Authentication auth) {
        guard(auth);
        return ApiResult.ok(service.openingEvidence());
    }

    /** 完成初始化并锁定账套。 */
    @PostMapping("/finish")
    public ApiResult<Map<String, Object>> finish(Authentication auth) {
        guard(auth);
        return ApiResult.ok(service.finish(auth.getName()));
    }

    private void guard(Authentication auth) {
        if (auth == null || !roleService.isAdminUser(auth.getName())) {
            throw new AccessDeniedException("仅管理员可使用初始化向导");
        }
    }
}