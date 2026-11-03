package com.mes.service;

import com.baomidou.mybatisplus.core.conditions.query.QueryWrapper;
import com.mes.entity.SysBackupLog;
import com.mes.mapper.SysBackupLogMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.io.File;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.zip.GZIPOutputStream;

/**
 * 数据库主动备份：调用 mysqldump 导出当前库并 gzip 落盘，每次留痕到 sys_backup_log。
 * 密码只经环境变量 MYSQL_PWD 传递，不出现在命令行（ps 看不到）。
 */
@Service
public class DbBackupService {

    private static final Pattern SAFE_NAME = Pattern.compile("^[A-Za-z0-9][A-Za-z0-9._-]*\\.sql\\.gz$");
    private static final Pattern JDBC = Pattern.compile("jdbc:mysql://([^:/]+):(\\d+)/([^?]+)");
    private static final DateTimeFormatter TS = DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss");

    private final SysBackupLogMapper logMapper;

    @Value("${spring.datasource.url:}")
    private String jdbcUrl;
    @Value("${spring.datasource.username:root}")
    private String dbUser;
    @Value("${spring.datasource.password:}")
    private String dbPassword;
    @Value("${mes.backup.dir:/opt/light-mes/backup}")
    private String backupDir;

    public DbBackupService(SysBackupLogMapper logMapper) {
        this.logMapper = logMapper;
    }

    /** 只允许本服务生成的文件名，杜绝路径穿越 */
    public static boolean isSafeFileName(String name) {
        return name != null && name.length() <= 200 && SAFE_NAME.matcher(name).matches();
    }

    public Map<String, Object> runNow(String operator) {
        long start = System.currentTimeMillis();
        Path out = null;
        String fileName = "light_mes-" + LocalDateTime.now().format(TS) + ".sql.gz";
        SysBackupLog row = new SysBackupLog();
        row.setFileName(fileName);
        row.setOperator(operator);
        row.setCreateTime(LocalDateTime.now());

        try {
            Matcher m = JDBC.matcher(jdbcUrl == null ? "" : jdbcUrl);
            if (!m.find()) throw new IllegalStateException("无法解析数据库连接串");
            String host = m.group(1);
            String port = m.group(2);
            String db = m.group(3);

            Path dir = Paths.get(backupDir);
            Files.createDirectories(dir);
            out = dir.resolve(fileName);

            ProcessBuilder pb = new ProcessBuilder(
                    "mysqldump",
                    "-h", host, "-P", port, "-u", dbUser,
                    "--single-transaction", "--routines", "--triggers",
                    "--default-character-set=utf8mb4",
                    db);
            pb.environment().put("MYSQL_PWD", dbPassword == null ? "" : dbPassword);
            File errFile = File.createTempFile("mysqldump-", ".err");
            pb.redirectError(errFile);

            Process p = pb.start();
            try (InputStream in = p.getInputStream();
                 OutputStream raw = Files.newOutputStream(out);
                 GZIPOutputStream gz = new GZIPOutputStream(raw)) {
                in.transferTo(gz);
            }
            boolean done = p.waitFor(180, TimeUnit.SECONDS);
            if (!done) {
                p.destroyForcibly();
                throw new IllegalStateException("mysqldump 超时（>180s）");
            }
            String err = Files.readString(errFile.toPath(), StandardCharsets.UTF_8).trim();
            errFile.delete();
            if (p.exitValue() != 0) {
                throw new IllegalStateException("mysqldump 退出码 " + p.exitValue() + (err.isEmpty() ? "" : "：" + err));
            }
            long size = Files.size(out);
            if (size < 1024) {
                throw new IllegalStateException("备份文件过小（" + size + " 字节），可能是空库或导出失败");
            }
            row.setStatus("SUCCESS");
            row.setFileSizeKb(size / 1024);
            row.setMessage("ok");
        } catch (Exception e) {
            row.setStatus("FAIL");
            row.setMessage(trim(e.getMessage()));
            if (out != null) {
                try { Files.deleteIfExists(out); } catch (Exception ignored) { }
            }
        } finally {
            row.setElapsedMs(System.currentTimeMillis() - start);
            try { logMapper.insert(row); } catch (Exception ignored) { }
        }

        Map<String, Object> res = new LinkedHashMap<>();
        res.put("status", row.getStatus());
        res.put("fileName", row.getFileName());
        res.put("fileSizeKb", row.getFileSizeKb());
        res.put("elapsedMs", row.getElapsedMs());
        res.put("message", row.getMessage());
        return res;
    }

    public List<SysBackupLog> logs(int limit) {
        int n = Math.max(1, Math.min(limit, 200));
        QueryWrapper<SysBackupLog> q = new QueryWrapper<>();
        q.orderByDesc("id").last("limit " + n);
        return logMapper.selectList(q);
    }

    /** 校验并解析出可下载的真实文件路径（不存在/非法名返回 null） */
    public Path resolveExisting(String fileName) {
        if (!isSafeFileName(fileName)) return null;
        Path dir = Paths.get(backupDir).toAbsolutePath().normalize();
        Path f = dir.resolve(fileName).normalize();
        if (!f.startsWith(dir) || !Files.isRegularFile(f)) return null;
        return f;
    }

    private static String trim(String s) {
        if (s == null) return "unknown";
        return s.length() <= 500 ? s : s.substring(0, 500);
    }
}
