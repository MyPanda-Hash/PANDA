package com.mes.init;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.apache.poi.ss.usermodel.Cell;
import org.apache.poi.ss.usermodel.Row;
import org.apache.poi.ss.usermodel.Sheet;
import org.apache.poi.ss.usermodel.Workbook;
import org.apache.poi.ss.usermodel.WorkbookFactory;
import org.apache.poi.xssf.usermodel.XSSFWorkbook;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockMultipartFile;

/**
 * 初始化模板：表头契约、示例行跳过、缺必填列要指名道姓。
 */
class InitTemplateServiceTest {

    private final InitTemplateService service = new InitTemplateService();

    @Test
    @DisplayName("七个模块都能生成模板，首行表头与枚举一致（必填列带 *）")
    void everyModuleHasTemplate() throws Exception {
        for (InitModule module : InitModule.values()) {
            byte[] bytes = service.template(module);
            assertTrue(bytes.length > 0, module + " 模板为空");
            try (Workbook wb = WorkbookFactory.create(new ByteArrayInputStream(bytes))) {
                Sheet sheet = wb.getSheetAt(0);
                Row head = sheet.getRow(0);
                assertEquals(module.headers().size(), head.getLastCellNum(), module + " 列数不符");
                for (int c = 0; c < module.headers().size(); c++) {
                    String expected = module.headers().get(c);
                    String actual = head.getCell(c).getStringCellValue();
                    if (InitTemplateService.requiredFor(module, expected)) {
                        assertEquals(expected + "*", actual, module + " 第 " + c + " 列应为必填");
                    } else {
                        assertEquals(expected, actual, module + " 第 " + c + " 列名不符");
                    }
                }
                // 第二行是示例，必须带标记，避免被当成数据
                assertEquals(InitTemplateService.SAMPLE_TAG + moduleSample(module), 
                        sheet.getRow(1).getCell(0).getStringCellValue());
            }
        }
    }

    private static String moduleSample(InitModule module) {
        return switch (module) {
            case DEPT -> "D01";
            case EMPLOYEE -> "E001";
            case PARTNER -> "KH001";
            case UOM -> "U01";
            case WH -> "CK01";
            case ITEM -> "CP001";
            case PROCESS -> "OP10";
            case ROUTING -> "GY-CP001";
            case BOM -> "CP001";
            case OPENING_STOCK -> "原料仓";
        };
    }

    @Test
    @DisplayName("把生成的模板原样上传：示例行被跳过，报「没有数据行」")
    void templateRoundTripSkipsSampleRow() throws Exception {
        byte[] bytes = service.template(InitModule.ITEM);
        MockMultipartFile file = new MockMultipartFile("file", "存货.xlsx", null, bytes);

        IllegalArgumentException e = assertThrows(IllegalArgumentException.class,
                () -> service.parse(file, InitModule.ITEM));
        assertTrue(e.getMessage().contains("没有数据行"), e.getMessage());
    }

    @Test
    @DisplayName("填了数据行就能解析出来，按表头名对齐且带行号")
    void parsesDataRows() throws Exception {
        byte[] bytes = withDataRow(InitModule.ITEM, List.of(
                "CP001", "铝棒 Φ80", "Φ80×3000", "件", "全月平均", "20", "原材料", "GY-CP001"));
        MockMultipartFile file = new MockMultipartFile("file", "存货.xlsx", null, bytes);

        List<Map<String, String>> rows = service.parse(file, InitModule.ITEM);

        assertEquals(1, rows.size());
        assertEquals("CP001", rows.get(0).get("存货编码"));
        assertEquals("件", rows.get(0).get("计量单位"));
        assertEquals("3", rows.get(0).get("__row"), "第 3 行是数据（1 表头 / 2 示例）");
    }

    @Test
    @DisplayName("缺必填列时报出缺哪一列，并用模板文件名指路")
    void reportsMissingRequiredColumn() throws Exception {
        // 手搓一张只有「存货编码」的窄表
        byte[] bytes;
        try (XSSFWorkbook wb = new XSSFWorkbook(); ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            Sheet sheet = wb.createSheet("存货");
            Row head = sheet.createRow(0);
            head.createCell(0).setCellValue("存货编码");
            Row data = sheet.createRow(1);
            data.createCell(0).setCellValue("CP001");
            wb.write(out);
            bytes = out.toByteArray();
        }
        MockMultipartFile file = new MockMultipartFile("file", "x.xlsx", null, bytes);

        IllegalArgumentException e = assertThrows(IllegalArgumentException.class,
                () -> service.parse(file, InitModule.ITEM));

        assertTrue(e.getMessage().contains("存货名称"), e.getMessage());
        assertTrue(e.getMessage().contains("计量单位"), e.getMessage());
        assertTrue(e.getMessage().contains("存货.xlsx"), "要指路到正确模板：" + e.getMessage());
    }

    @Test
    @DisplayName("空文件与非模板文件给出明确提示")
    void rejectsEmptyOrHeadlessFile() throws Exception {
        MockMultipartFile empty = new MockMultipartFile("file", "a.xlsx", null, new byte[0]);
        assertThrows(IllegalArgumentException.class, () -> service.parse(empty, InitModule.DEPT));

        byte[] headless;
        try (XSSFWorkbook wb = new XSSFWorkbook(); ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            wb.createSheet("s");
            wb.write(out);
            headless = out.toByteArray();
        }
        MockMultipartFile file = new MockMultipartFile("file", "b.xlsx", null, headless);
        IllegalArgumentException e = assertThrows(IllegalArgumentException.class,
                () -> service.parse(file, InitModule.DEPT));
        assertTrue(e.getMessage().contains("表头"), e.getMessage());
    }

    @Test
    @DisplayName("必填列清单：编码/名称/数量/单价类必填，备注类不必填")
    void requiredColumns() {
        assertTrue(InitTemplateService.required("存货编码"));
        assertTrue(InitTemplateService.required("数量"));
        assertFalse(InitTemplateService.required("规格型号"));
        assertFalse(InitTemplateService.required("备注"));
        assertFalse(InitTemplateService.required("岗位职责"));
    }

    private byte[] withDataRow(InitModule module, List<String> values) throws Exception {
        byte[] base = service.template(module);
        try (Workbook wb = WorkbookFactory.create(new ByteArrayInputStream(base));
             ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            Sheet sheet = wb.getSheetAt(0);
            Row row = sheet.createRow(2);
            for (int c = 0; c < values.size(); c++) {
                Cell cell = row.createCell(c);
                cell.setCellValue(values.get(c));
            }
            wb.write(out);
            return out.toByteArray();
        }
    }
}
