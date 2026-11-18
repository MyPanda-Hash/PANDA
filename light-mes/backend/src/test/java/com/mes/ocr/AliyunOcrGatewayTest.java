package com.mes.ocr;

import com.aliyun.ocr_api20210707.models.RecognizeAllTextResponse;
import com.aliyun.ocr_api20210707.models.RecognizeAllTextResponseBody;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class AliyunOcrGatewayTest {

    @Test
    void convertsOfficialSdkResponseIntoStableGatewayModel() throws Exception {
        var block = new RecognizeAllTextResponseBody
                .RecognizeAllTextResponseBodyDataSubImagesBlockInfoBlockDetails()
                .setBlockId(7)
                .setBlockConfidence(96)
                .setBlockContent("供应商：华东铝业");
        var blockInfo = new RecognizeAllTextResponseBody
                .RecognizeAllTextResponseBodyDataSubImagesBlockInfo()
                .setBlockDetails(List.of(block));
        var row = new RecognizeAllTextResponseBody
                .RecognizeAllTextResponseBodyDataSubImagesRowInfoRowDetails()
                .setRowContent("供应商：华东铝业")
                .setBlockList(List.of(7));
        var rowInfo = new RecognizeAllTextResponseBody
                .RecognizeAllTextResponseBodyDataSubImagesRowInfo()
                .setRowDetails(List.of(row));
        var cell = new RecognizeAllTextResponseBody
                .RecognizeAllTextResponseBodyDataSubImagesTableInfoTableDetailsCellDetails()
                .setRowStart(0)
                .setRowEnd(0)
                .setColumnStart(0)
                .setColumnEnd(0)
                .setCellContent("存货编码");
        var table = new RecognizeAllTextResponseBody
                .RecognizeAllTextResponseBodyDataSubImagesTableInfoTableDetails()
                .setCellDetails(List.of(cell));
        var tableInfo = new RecognizeAllTextResponseBody
                .RecognizeAllTextResponseBodyDataSubImagesTableInfo()
                .setTableDetails(List.of(table));
        var subImage = new RecognizeAllTextResponseBody
                .RecognizeAllTextResponseBodyDataSubImages()
                .setBlockInfo(blockInfo)
                .setRowInfo(rowInfo)
                .setTableInfo(tableInfo);
        var data = new RecognizeAllTextResponseBody
                .RecognizeAllTextResponseBodyData()
                .setContent("供应商：华东铝业")
                .setSubImages(List.of(subImage));
        var body = new RecognizeAllTextResponseBody()
                .setRequestId("aliyun-request-1")
                .setData(data);
        var response = new RecognizeAllTextResponse().setBody(body);

        OcrGateway.OcrDocument document = gateway().toDocument(response);

        assertEquals("aliyun-request-1", document.requestId());
        assertEquals("供应商：华东铝业", document.rawText());
        assertEquals(96, document.lines().get(0).confidence());
        assertEquals("存货编码", document.tables().get(0).cells().get(0).text());
    }

    @Test
    void hidesCloudErrorDetailsFromCaller() {
        var response = new RecognizeAllTextResponse().setBody(
                new RecognizeAllTextResponseBody().setCode("InvalidParameter").setMessage("cloud detail"));

        OcrGatewayException error = assertThrows(OcrGatewayException.class,
                () -> gateway().toDocument(response));

        assertEquals("OCR 服务未能完成识别，请稍后重试", error.getMessage());
    }

    private AliyunOcrGateway gateway() {
        return new AliyunOcrGateway("", "", "ocr-api.cn-hangzhou.aliyuncs.com", 5000, 35000);
    }
}
