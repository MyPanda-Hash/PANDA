package com.mes.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.mes.entity.InvoiceFile;
import com.mes.mapper.InvoiceFileMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardCopyOption;
import java.security.DigestInputStream;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;

/**
 * 发票官方票面归档：上传 / 查询 / 取文件。
 *
 * <p>系统不签发发票，正式票面由电子税务局或开票服务商开具。企业开具后把 OFD/PDF
 * 回传到这里，打印与下载都取这份官方文件，票样页只用于核对。
 */
@Service
public class InvoiceFileService {

    private static final Logger log = LoggerFactory.getLogger(InvoiceFileService.class);
    private static final long MAX_BYTES = 20L * 1024 * 1024;

    private final InvoiceFileMapper mapper;

    /** 归档根目录，默认 backend/data/invoice-files，可用 mes.invoice.file-dir 覆盖。 */
    @Value("${mes.invoice.file-dir:data/invoice-files}")
    private String fileDir;

    public InvoiceFileService(InvoiceFileMapper mapper) {
        this.mapper = mapper;
    }

    private Path root() {
        return Paths.get(fileDir == null || fileDir.isBlank() ? "data/invoice-files" : fileDir).toAbsolutePath().normalize();
    }

    /** 该发票已有的票面文件（新到旧）。 */
    public List<InvoiceFile> listByInvoice(String invoiceNo) {
        if (invoiceNo == null || invoiceNo.isBlank()) return List.of();
        return mapper.selectList(new LambdaQueryWrapper<InvoiceFile>()
                .eq(InvoiceFile::getInvoiceNo, invoiceNo)
                .orderByDesc(InvoiceFile::getId));
    }

    /** 视图用：只保留展示需要的信息，不暴露服务器绝对路径。 */
    public Map<String, Object> toView(InvoiceFile f) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("id", f.getId());
        m.put("发票号", f.getInvoiceNo());
        m.put("文件名", f.getFileName());
        m.put("文件类型", f.getFileType());
        m.put("大小", f.getFileSize());
        m.put("上传人", f.getUploadBy());
        m.put("上传时间", f.getUploadTime() == null ? "" : f.getUploadTime().format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss")));
        return m;
    }

    /**
     * 保存回传的票面文件。
     *
     * <p>只接受 OFD/PDF：这两种是税务认可的版式文件；图片或 Office 文档不能作为票面证据。
     */
    public InvoiceFile upload(String invoiceNo, MultipartFile file, String operator) throws IOException {
        if (invoiceNo == null || invoiceNo.isBlank()) {
            throw new IllegalArgumentException("缺少发票单据号");
        }
        if (file == null || file.isEmpty()) {
            throw new IllegalArgumentException("请选择要上传的票面文件");
        }
        if (file.getSize() > MAX_BYTES) {
            throw new IllegalArgumentException("文件超过 20MB，请上传电子税务局下载的原文件");
        }
        String original = file.getOriginalFilename() == null ? "invoice" : file.getOriginalFilename();
        String ext = extOf(original);
        if (!"ofd".equals(ext) && !"pdf".equals(ext)) {
            throw new IllegalArgumentException("只接受 OFD 或 PDF 票面文件（当前：" + (ext.isEmpty() ? "无扩展名" : ext) + "）");
        }

        Path dir = root().resolve(safe(invoiceNo));
        Files.createDirectories(dir);
        String stored = UUID.randomUUID().toString().replace("-", "").substring(0, 12) + "." + ext;
        Path target = dir.resolve(stored);

        String sha;
        try (InputStream in = file.getInputStream()) {
            MessageDigest md = MessageDigest.getInstance("SHA-256");
            try (DigestInputStream dis = new DigestInputStream(in, md)) {
                Files.copy(dis, target, StandardCopyOption.REPLACE_EXISTING);
            }
            sha = hex(md.digest());
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("无法计算文件指纹", e);
        }

        InvoiceFile rec = new InvoiceFile();
        rec.setInvoiceNo(invoiceNo);
        rec.setFileName(original);
        rec.setFilePath(safe(invoiceNo) + "/" + stored);
        rec.setFileSize(file.getSize());
        rec.setFileType(ext.toUpperCase(Locale.ROOT));
        rec.setSha256(sha);
        rec.setUploadBy(operator);
        rec.setUploadTime(LocalDateTime.now());
        mapper.insert(rec);
        log.info("发票票面已归档 invoiceNo={} file={} type={} size={}", invoiceNo, original, ext, file.getSize());
        return rec;
    }

    /** 取物理文件，调用方负责关闭流；文件不存在时返回 null。 */
    public Path pathOf(InvoiceFile rec) {
        if (rec == null || rec.getFilePath() == null) return null;
        Path p = root().resolve(rec.getFilePath()).normalize();
        // 防目录穿越：必须仍在归档根目录之下
        if (!p.startsWith(root())) return null;
        return Files.exists(p) ? p : null;
    }

    public InvoiceFile byId(Long id) {
        return id == null ? null : mapper.selectById(id);
    }

    private static String extOf(String name) {
        int i = name.lastIndexOf('.');
        return i < 0 ? "" : name.substring(i + 1).toLowerCase(Locale.ROOT);
    }

    /** 单据号里只允许字母数字与连字符，避免拼出跨目录路径。 */
    private static String safe(String s) {
        String out = s.replaceAll("[^A-Za-z0-9._-]", "_");
        return out.isEmpty() ? "unknown" : out;
    }

    private static String hex(byte[] bytes) {
        StringBuilder sb = new StringBuilder(bytes.length * 2);
        for (byte b : bytes) sb.append(String.format("%02x", b));
        return sb.toString();
    }
}