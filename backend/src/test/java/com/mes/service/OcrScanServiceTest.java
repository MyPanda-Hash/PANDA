package com.mes.service;

import com.mes.ocr.OcrGateway;
import com.mes.ocr.OcrGateway.OcrDocument;
import com.mes.panel.PanelRuntimeService;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.web.multipart.MultipartFile;

import javax.imageio.ImageIO;
import java.awt.image.BufferedImage;
import java.io.ByteArrayOutputStream;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class OcrScanServiceTest {

    @Mock private OcrGateway gateway;
    @Mock private PanelRuntimeService panelRuntimeService;
    @Mock private RoleService roleService;
    @Mock private MultipartFile oversizedImage;

    @Test
    void validatesImageThenMapsGatewayResultWithoutSavingIt() throws Exception {
        var service = service();
        allowPanel("worker", "PU_REQ");
        when(panelRuntimeService.getPanelConfig("PU_REQ")).thenReturn(Map.of(
                "metadata", Map.of("panelCategory", "单据"),
                "dataSchema", Map.of("fields", List.of(Map.of("dataName", "供应商", "dataType", "文本"))),
                "detail", Map.of("tabs", List.of())));
        when(gateway.recognize(any())).thenReturn(new OcrDocument(
                "req-2", "供应商：华东铝业", List.of(), List.of()));

        var result = service.scan("PU_REQ", png(100, 60), "worker");

        assertEquals("req-2", result.requestId());
        assertEquals("华东铝业", result.header().get("供应商"));
        verify(gateway).recognize(any());
        verify(panelRuntimeService).getPanelConfig("PU_REQ");
    }

    @Test
    void rejectsUndecodableImageBeforeCallingPanelOrCloud() throws Exception {
        var service = service();
        allowPanel("worker", "PU_REQ");
        var image = new MockMultipartFile("image", "scan.webp", "image/webp", new byte[]{1, 2, 3});

        IllegalArgumentException error = assertThrows(IllegalArgumentException.class,
                () -> service.scan("PU_REQ", image, "worker"));

        assertEquals("当前服务无法解码该图片，请转换为 JPG 或 PNG 后重试", error.getMessage());
        verify(gateway, never()).recognize(any());
        verify(panelRuntimeService, never()).getPanelConfig(any());
    }

    @Test
    void rejectsOversizedImageBeforeReadingIt() throws Exception {
        var service = service();
        allowPanel("worker", "PU_REQ");
        when(oversizedImage.isEmpty()).thenReturn(false);
        when(oversizedImage.getSize()).thenReturn(OcrScanService.MAX_IMAGE_BYTES + 1);

        IllegalArgumentException error = assertThrows(IllegalArgumentException.class,
                () -> service.scan("PU_REQ", oversizedImage, "worker"));

        assertEquals("图片不能超过10MB", error.getMessage());
        verify(oversizedImage, never()).getInputStream();
        verify(gateway, never()).recognize(any());
    }

    @Test
    void rejectsUserWithoutPanelPermissionBeforeReadingOrCallingCloud() throws Exception {
        var service = service();
        when(roleService.getPerms("worker")).thenReturn(Map.of(
                "isAdmin", false,
                "visiblePanels", List.of("OTHER_PANEL")));

        assertThrows(org.springframework.security.access.AccessDeniedException.class,
                () -> service.scan("PU_REQ", oversizedImage, "worker"));

        verify(oversizedImage, never()).getInputStream();
        verify(panelRuntimeService, never()).getPanelConfig(any());
        verify(gateway, never()).recognize(any());
    }

    @Test
    void rejectsReadonlyDocumentBeforeCallingCloud() throws Exception {
        var service = service();
        allowPanel("worker", "READONLY_DOC");
        when(panelRuntimeService.getPanelConfig("READONLY_DOC")).thenReturn(Map.of(
                "metadata", Map.of("panelCategory", "单据", "readonly", true)));

        IllegalArgumentException error = assertThrows(IllegalArgumentException.class,
                () -> service.scan("READONLY_DOC", png(100, 60), "worker"));

        assertEquals("该面板不支持扫描填单", error.getMessage());
        verify(gateway, never()).recognize(any());
    }

    @Test
    void acceptsOpeningBalanceDocument() throws Exception {
        var service = service();
        allowPanel("worker", "INIT_BALANCE");
        when(panelRuntimeService.getPanelConfig("INIT_BALANCE")).thenReturn(Map.of(
                "metadata", Map.of("panelCategory", "期初单据"),
                "dataSchema", Map.of("fields", List.of()),
                "detail", Map.of("tabs", List.of())));
        when(gateway.recognize(any())).thenReturn(new OcrDocument("req-opening", "", List.of(), List.of()));

        var result = service.scan("INIT_BALANCE", png(100, 60), "worker");

        assertEquals("req-opening", result.requestId());
        verify(gateway).recognize(any());
    }

    private OcrScanService service() {
        return new OcrScanService(gateway, panelRuntimeService, new OcrFormMapper(), roleService,
                new OcrRequestLimiter(100, 10, System::currentTimeMillis));
    }

    private void allowPanel(String userName, String panelCode) {
        when(roleService.getPerms(userName)).thenReturn(Map.of(
                "isAdmin", false,
                "visiblePanels", List.of(panelCode)));
    }

    private MockMultipartFile png(int width, int height) throws Exception {
        BufferedImage image = new BufferedImage(width, height, BufferedImage.TYPE_INT_RGB);
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        ImageIO.write(image, "png", output);
        return new MockMultipartFile("image", "scan.png", "image/png", output.toByteArray());
    }
}
