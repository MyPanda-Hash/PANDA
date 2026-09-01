package com.mes.service;

import com.mes.ocr.OcrGateway;
import com.mes.ocr.OcrGatewayException;
import com.mes.ocr.OcrScanResponse;
import com.mes.ocr.OcrServiceException;
import com.mes.panel.PanelRuntimeService;
import org.springframework.stereotype.Service;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.web.multipart.MultipartFile;

import javax.imageio.ImageIO;
import javax.imageio.ImageReadParam;
import javax.imageio.ImageReader;
import javax.imageio.stream.MemoryCacheImageInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.Iterator;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

@Service
public class OcrScanService {

    static final long MAX_IMAGE_BYTES = 10L * 1024 * 1024;
    private static final int MIN_DIMENSION = 15;
    private static final int MAX_DIMENSION = 8192;
    private static final Set<String> ALLOWED_FORMATS = Set.of("jpeg", "jpg", "png", "bmp", "gif", "tif", "tiff");

    private final OcrGateway gateway;
    private final PanelRuntimeService panelRuntimeService;
    private final OcrFormMapper formMapper;
    private final RoleService roleService;
    private final OcrRequestLimiter requestLimiter;

    public OcrScanService(OcrGateway gateway, PanelRuntimeService panelRuntimeService,
                          OcrFormMapper formMapper, RoleService roleService,
                          OcrRequestLimiter requestLimiter) {
        this.gateway = gateway;
        this.panelRuntimeService = panelRuntimeService;
        this.formMapper = formMapper;
        this.roleService = roleService;
        this.requestLimiter = requestLimiter;
    }

    public OcrScanResponse scan(String panelCode, MultipartFile image, String userName) {
        if (panelCode == null || panelCode.isBlank()) throw new IllegalArgumentException("面板编码不能为空");
        String normalizedPanelCode = panelCode.trim();
        requirePanelAccess(normalizedPanelCode, userName);
        try (OcrRequestLimiter.Permit ignored = requestLimiter.acquire(userName)) {
            validateImage(image);
            Map<String, Object> config = panelRuntimeService.getPanelConfig(normalizedPanelCode);
            requireEditableDocument(config);
            try (InputStream input = image.getInputStream()) {
                return formMapper.map(config, gateway.recognize(input));
            } catch (OcrGatewayException e) {
                throw new OcrServiceException(e.getMessage(), e);
            } catch (IOException e) {
                throw new OcrServiceException("图片读取失败，请重新选择图片", e);
            }
        }
    }

    private void requirePanelAccess(String panelCode, String userName) {
        if (userName == null || userName.isBlank()) throw new AccessDeniedException("请先登录");
        Map<String, Object> permissions = roleService.getPerms(userName);
        if (Boolean.TRUE.equals(permissions.get("isAdmin"))) return;
        Object visiblePanels = permissions.get("visiblePanels");
        if (visiblePanels instanceof Iterable<?> panels) {
            for (Object panel : panels) {
                if (panelCode.equals(String.valueOf(panel))) return;
            }
        }
        throw new AccessDeniedException("当前用户无权使用该面板的扫描填单");
    }

    @SuppressWarnings("unchecked")
    private void requireEditableDocument(Map<String, Object> config) {
        Object metadataObject = config.get("metadata");
        if (!(metadataObject instanceof Map<?, ?> rawMetadata)) {
            throw new IllegalArgumentException("该面板不支持扫描填单");
        }
        Map<String, Object> metadata = (Map<String, Object>) rawMetadata;
        boolean readonly = Boolean.parseBoolean(String.valueOf(metadata.getOrDefault("readonly", false)))
                || Boolean.parseBoolean(String.valueOf(metadata.getOrDefault("readOnly", false)));
        if (!String.valueOf(metadata.getOrDefault("panelCategory", "")).trim().endsWith("单据") || readonly) {
            throw new IllegalArgumentException("该面板不支持扫描填单");
        }
    }

    private void validateImage(MultipartFile image) {
        if (image == null || image.isEmpty()) throw new IllegalArgumentException("请选择需要扫描的图片");
        if (image.getSize() > MAX_IMAGE_BYTES) throw new IllegalArgumentException("图片不能超过10MB");
        try (InputStream input = image.getInputStream();
             var stream = new MemoryCacheImageInputStream(input)) {
            Iterator<ImageReader> readers = ImageIO.getImageReaders(stream);
            if (!readers.hasNext()) {
                throw new IllegalArgumentException("当前服务无法解码该图片，请转换为 JPG 或 PNG 后重试");
            }
            ImageReader reader = readers.next();
            try {
                String format = reader.getFormatName().toLowerCase(Locale.ROOT);
                if (!ALLOWED_FORMATS.contains(format)) {
                    throw new IllegalArgumentException("不支持该图片格式，请上传 JPG、PNG、BMP、GIF 或 TIFF 图片");
                }
                reader.setInput(stream, true, true);
                int width = reader.getWidth(0);
                int height = reader.getHeight(0);
                if (width <= MIN_DIMENSION || height <= MIN_DIMENSION
                        || width >= MAX_DIMENSION || height >= MAX_DIMENSION) {
                    throw new IllegalArgumentException("图片宽高必须大于15且小于8192像素");
                }
                double ratio = (double) Math.max(width, height) / Math.min(width, height);
                if (ratio >= 50) throw new IllegalArgumentException("图片长宽比必须小于50");

                // Decode a bounded sample to reject corrupt payloads without allocating a full 8192x8192 bitmap.
                ImageReadParam param = reader.getDefaultReadParam();
                int sample = Math.max(1, Math.max((width + 1023) / 1024, (height + 1023) / 1024));
                param.setSourceSubsampling(sample, sample, 0, 0);
                if (reader.read(0, param) == null) {
                    throw new IllegalArgumentException("当前服务无法解码该图片，请转换为 JPG 或 PNG 后重试");
                }
            } finally {
                reader.dispose();
            }
        } catch (IllegalArgumentException e) {
            throw e;
        } catch (IOException | RuntimeException e) {
            throw new IllegalArgumentException("当前服务无法解码该图片，请转换为 JPG 或 PNG 后重试", e);
        }
    }
}
