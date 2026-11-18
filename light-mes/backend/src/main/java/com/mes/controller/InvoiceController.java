package com.mes.controller;

import com.mes.dto.ApiResult;
import com.mes.entity.InvoiceFile;
import com.mes.service.InvoiceExportService;
import com.mes.service.InvoiceFileService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.io.FileSystemResource;
import org.springframework.core.io.Resource;
import org.springframework.http.ContentDisposition;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.util.List;
import java.util.Map;

/**
 * 发票开票渠道相关接口。
 *
 * <p>系统不签发发票：这里提供「导出开票清单」给开票软件/电子税务局，
 * 以及「回传官方票面（OFD/PDF）」归档，打印取官方文件。
 */
@RestController
@RequestMapping("/api/px")
public class InvoiceController {

    private static final Logger log = LoggerFactory.getLogger(InvoiceController.class);

    private final InvoiceFileService fileService;
    private final InvoiceExportService exportService;

    public InvoiceController(InvoiceFileService fileService, InvoiceExportService exportService) {
        this.fileService = fileService;
        this.exportService = exportService;
    }

    /**
     * 导出开票清单（.xlsx）。
     *
     * <p>请求体：{"codes": ["FP-...", ...]}，按发票号导出其明细。
     */
    @PostMapping("/invoiceExport")
    public ResponseEntity<byte[]> exportInvoice(@RequestBody Map<String, Object> body) {
        List<String> codes = new java.util.ArrayList<>();
        Object raw = body == null ? null : body.get("codes");
        if (raw instanceof List<?> list) {
            for (Object o : list) {
                String s = o == null ? "" : String.valueOf(o).trim();
                if (!s.isEmpty()) codes.add(s);
            }
        }
        if (codes.isEmpty()) {
            return ResponseEntity.badRequest()
                    .contentType(MediaType.TEXT_PLAIN)
                    .body("请提供要导出的发票号（codes）".getBytes(StandardCharsets.UTF_8));
        }
        List<Map<String, String>> rows = exportService.rows(codes);
        if (rows.isEmpty()) {
            return ResponseEntity.badRequest()
                    .contentType(MediaType.TEXT_PLAIN)
                    .body("所选发票没有可导出的明细".getBytes(StandardCharsets.UTF_8));
        }
        byte[] xlsx;
        try {
            xlsx = exportService.toXlsx(rows);
        } catch (IOException e) {
            log.error("生成开票清单失败", e);
            return ResponseEntity.internalServerError()
                    .contentType(MediaType.TEXT_PLAIN)
                    .body("生成 Excel 失败".getBytes(StandardCharsets.UTF_8));
        }
        String name = "开票清单-" + LocalDate.now().format(DateTimeFormatter.ofPattern("yyyyMMdd")) + ".xlsx";
        return ResponseEntity.ok()
                .header(HttpHeaders.CONTENT_DISPOSITION,
                        ContentDisposition.attachment().filename(name, StandardCharsets.UTF_8).build().toString())
                .contentType(MediaType.parseMediaType(
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
                .body(xlsx);
    }

    /** 某张发票已归档的票面文件。 */
    @GetMapping("/invoiceFile/list")
    public ApiResult<List<Map<String, Object>>> listFiles(@RequestParam("code") String code) {
        return ApiResult.ok(fileService.listByInvoice(code).stream().map(fileService::toView).toList());
    }

    /** 回传官方票面（只收 OFD/PDF）。 */
    @PostMapping("/invoiceFile/upload")
    public ApiResult<Map<String, Object>> upload(@RequestParam("code") String code,
                                                 @RequestParam("file") MultipartFile file,
                                                 Authentication auth) throws IOException {
        String operator = auth == null ? "system" : auth.getName();
        InvoiceFile rec = fileService.upload(code, file, operator);
        log.info("invoice file uploaded code={} name={} by={}", code, rec.getFileName(), operator);
        return ApiResult.ok(fileService.toView(rec));
    }

    /** 下载/预览官方票面。 */
    @GetMapping("/invoiceFile/download")
    public ResponseEntity<Resource> download(@RequestParam("id") Long id) {
        InvoiceFile rec = fileService.byId(id);
        if (rec == null) return ResponseEntity.notFound().build();
        Path path = fileService.pathOf(rec);
        if (path == null) return ResponseEntity.notFound().build();
        String type = "OFD".equalsIgnoreCase(rec.getFileType()) ? "application/ofd" : MediaType.APPLICATION_PDF_VALUE;
        return ResponseEntity.ok()
                .header(HttpHeaders.CONTENT_DISPOSITION,
                        ContentDisposition.inline()
                                .filename(rec.getFileName(), StandardCharsets.UTF_8).build().toString())
                .contentType(MediaType.parseMediaType(type))
                .body(new FileSystemResource(path));
    }
}