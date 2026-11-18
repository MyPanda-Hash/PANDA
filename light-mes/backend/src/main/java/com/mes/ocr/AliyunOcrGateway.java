package com.mes.ocr;

import com.aliyun.ocr_api20210707.models.RecognizeAllTextRequest;
import com.aliyun.ocr_api20210707.models.RecognizeAllTextResponse;
import com.aliyun.ocr_api20210707.models.RecognizeAllTextResponseBody;
import com.aliyun.tea.TeaException;
import com.aliyun.teautil.models.RuntimeOptions;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.io.InputStream;
import java.util.ArrayList;
import java.util.List;

@Component
public class AliyunOcrGateway implements OcrGateway {

    private static final Logger log = LoggerFactory.getLogger(AliyunOcrGateway.class);

    private final String accessKeyId;
    private final String accessKeySecret;
    private final String endpoint;
    private final int connectTimeoutMs;
    private final int readTimeoutMs;

    public AliyunOcrGateway(
            @Value("${mes.ocr.access-key-id:}") String accessKeyId,
            @Value("${mes.ocr.access-key-secret:}") String accessKeySecret,
            @Value("${mes.ocr.endpoint:ocr-api.cn-hangzhou.aliyuncs.com}") String endpoint,
            @Value("${mes.ocr.connect-timeout-ms:5000}") int connectTimeoutMs,
            @Value("${mes.ocr.read-timeout-ms:30000}") int readTimeoutMs) {
        this.accessKeyId = accessKeyId;
        this.accessKeySecret = accessKeySecret;
        this.endpoint = endpoint;
        this.connectTimeoutMs = connectTimeoutMs;
        this.readTimeoutMs = readTimeoutMs;
    }

    @Override
    public OcrDocument recognize(InputStream image) throws OcrGatewayException {
        if (accessKeyId.isBlank() || accessKeySecret.isBlank()) {
            throw new OcrGatewayException("OCR 服务未配置，请联系管理员");
        }
        try {
            var config = new com.aliyun.teaopenapi.models.Config()
                    .setAccessKeyId(accessKeyId)
                    .setAccessKeySecret(accessKeySecret)
                    .setEndpoint(endpoint);
            var client = new com.aliyun.ocr_api20210707.Client(config);
            var advanced = new RecognizeAllTextRequest.RecognizeAllTextRequestAdvancedConfig()
                    .setOutputTable(true)
                    .setOutputRow(true);
            var request = new RecognizeAllTextRequest()
                    .setType("Advanced")
                    .setAdvancedConfig(advanced)
                    .setBody(image);
            var runtime = new RuntimeOptions()
                    .setConnectTimeout(connectTimeoutMs)
                    .setReadTimeout(readTimeoutMs);
            return toDocument(client.recognizeAllTextWithOptions(request, runtime));
        } catch (OcrGatewayException e) {
            throw e;
        } catch (Exception e) {
            logFailure(e);
            throw new OcrGatewayException("OCR 服务暂时不可用，请稍后重试", e);
        }
    }

    OcrDocument toDocument(RecognizeAllTextResponse response) throws OcrGatewayException {
        RecognizeAllTextResponseBody body = response == null ? null : response.getBody();
        if (body == null) throw new OcrGatewayException("OCR 服务返回了无效响应");
        if (body.getCode() != null && !body.getCode().isBlank()) {
            log.warn("Aliyun OCR rejected request: code={}, requestId={}, message={}",
                    redact(body.getCode()), redact(body.getRequestId()), redact(body.getMessage()));
            throw new OcrGatewayException("OCR 服务未能完成识别，请稍后重试");
        }
        var data = body.getData();
        if (data == null) throw new OcrGatewayException("OCR 服务未返回识别结果");

        List<OcrLine> lines = new ArrayList<>();
        List<OcrTable> tables = new ArrayList<>();
        if (data.getSubImages() != null) {
            for (var subImage : data.getSubImages()) {
                if (subImage == null) continue;
                if (subImage.getRowInfo() != null && subImage.getRowInfo().getRowDetails() != null) {
                    for (var row : subImage.getRowInfo().getRowDetails()) {
                        if (row != null && row.getRowContent() != null) {
                            lines.add(new OcrLine(row.getRowContent(), confidenceOf(subImage, row.getBlockList())));
                        }
                    }
                }
                if (subImage.getTableInfo() != null && subImage.getTableInfo().getTableDetails() != null) {
                    for (var table : subImage.getTableInfo().getTableDetails()) {
                        List<OcrCell> cells = new ArrayList<>();
                        if (table != null && table.getCellDetails() != null) {
                            for (var cell : table.getCellDetails()) {
                                if (cell == null) continue;
                                cells.add(new OcrCell(number(cell.getRowStart()), number(cell.getRowEnd()),
                                        number(cell.getColumnStart()), number(cell.getColumnEnd()), cell.getCellContent()));
                            }
                        }
                        if (!cells.isEmpty()) tables.add(new OcrTable(cells));
                    }
                }
            }
        }
        return new OcrDocument(body.getRequestId(), data.getContent(), lines, tables);
    }

    private void logFailure(Exception error) {
        if (error instanceof TeaException tea) {
            Object requestId = tea.getData() == null ? null
                    : tea.getData().getOrDefault("RequestId", tea.getData().get("requestId"));
            log.warn("Aliyun OCR call failed: status={}, code={}, requestId={}, message={}",
                    tea.getStatusCode(), redact(tea.getCode()), redact(requestId), redact(tea.getMessage()));
            return;
        }
        log.warn("Aliyun OCR call failed: type={}, message={}",
                error.getClass().getSimpleName(), redact(error.getMessage()));
    }

    private String redact(Object value) {
        String text = value == null ? "" : String.valueOf(value);
        if (!accessKeyId.isBlank()) text = text.replace(accessKeyId, "***");
        if (!accessKeySecret.isBlank()) text = text.replace(accessKeySecret, "***");
        return text.length() <= 1000 ? text : text.substring(0, 1000) + "...";
    }

    private Integer confidenceOf(
            RecognizeAllTextResponseBody.RecognizeAllTextResponseBodyDataSubImages subImage,
            List<Integer> blockIds) {
        if (subImage.getBlockInfo() == null || subImage.getBlockInfo().getBlockDetails() == null
                || blockIds == null || blockIds.isEmpty()) return null;
        int sum = 0;
        int count = 0;
        for (var block : subImage.getBlockInfo().getBlockDetails()) {
            if (block != null && blockIds.contains(block.getBlockId()) && block.getBlockConfidence() != null) {
                sum += block.getBlockConfidence();
                count++;
            }
        }
        return count == 0 ? null : sum / count;
    }

    private int number(Integer value) {
        return value == null ? 0 : value;
    }
}
