package com.mes.init;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.apache.poi.ss.usermodel.Cell;
import org.apache.poi.ss.usermodel.CellStyle;
import org.apache.poi.ss.usermodel.FillPatternType;
import org.apache.poi.ss.usermodel.Font;
import org.apache.poi.ss.usermodel.HorizontalAlignment;
import org.apache.poi.ss.usermodel.IndexedColors;
import org.apache.poi.ss.usermodel.Row;
import org.apache.poi.ss.usermodel.Sheet;
import org.apache.poi.ss.usermodel.Workbook;
import org.apache.poi.ss.usermodel.WorkbookFactory;
import org.apache.poi.xssf.usermodel.XSSFWorkbook;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

/**
 * 初始化模板的生成与解析（服务端 POI，前后端不用各写一套列名）。
 *
 * <p>模板：首行表头（加粗灰底、冻结），第二行示例（浅色斜体说明用什么格式填），
 * 表头带「*」的是必填列。第二步是示例不是数据，解析时跳过。
 *
 * <p>解析：按表头名对齐（不看列顺序），返回「列名 → 文本值」的行列表；
 * 缺必填列直接报出缺哪列，而不是整批静默失败。
 */
@Service
public class InitTemplateService {

    /** 表头里表示必填的后缀 */
    static final String REQUIRED_MARK = "*";
    /** 示例行的首列标记：解析时整行跳过 */
    static final String SAMPLE_TAG = "（示例）";

    /** 生成该模块的模板；format=xls 时给旧版 Excel（2003）也能打开的文件。 */
    public byte[] template(InitModule module) throws IOException {
        return template(module, "xlsx");
    }

    public byte[] template(InitModule module, String format) throws IOException {
        boolean xls = "xls".equalsIgnoreCase(format == null ? "" : format.trim());
        try (Workbook wb = xls ? new org.apache.poi.hssf.usermodel.HSSFWorkbook() : new XSSFWorkbook();
             ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            Sheet sheet = wb.createSheet(module.label());
            sheet.createFreezePane(0, 1);

            Font headFont = wb.createFont();
            headFont.setBold(true);
            headFont.setFontHeightInPoints((short) 11);
            CellStyle headStyle = wb.createCellStyle();
            headStyle.setFont(headFont);
            headStyle.setAlignment(HorizontalAlignment.CENTER);
            headStyle.setFillForegroundColor(IndexedColors.GREY_25_PERCENT.getIndex());
            headStyle.setFillPattern(FillPatternType.SOLID_FOREGROUND);

            Font sampleFont = wb.createFont();
            sampleFont.setItalic(true);
            sampleFont.setColor(IndexedColors.GREY_50_PERCENT.getIndex());
            CellStyle sampleStyle = wb.createCellStyle();
            sampleStyle.setFont(sampleFont);

            Row head = sheet.createRow(0);
            head.setHeightInPoints(22);
            List<String> headers = module.headers();
            for (int c = 0; c < headers.size(); c++) {
                Cell cell = head.createCell(c);
                cell.setCellValue(requiredFor(module, headers.get(c))
                        ? headers.get(c) + REQUIRED_MARK : headers.get(c));
                cell.setCellStyle(headStyle);
                sheet.setColumnWidth(c, Math.max(14, headers.get(c).length() * 3) * 256);
            }

            Row sample = sheet.createRow(1);
            List<String> values = sampleRow(module);
            for (int c = 0; c < values.size(); c++) {
                Cell cell = sample.createCell(c);
                cell.setCellValue(values.get(c));
                cell.setCellStyle(sampleStyle);
            }

            wb.write(out);
            return out.toByteArray();
        }
    }

    /** 模板下载用的文件名（按格式给扩展名）。 */
    public static String fileNameOf(InitModule module, String format) {
        String base = module.fileName();
        if (!"xls".equalsIgnoreCase(format == null ? "" : format.trim())) return base;
        return base.replaceAll("\\.xlsx$", ".xls");
    }

    /**
     * 模块内的必填列：期初库存只认「仓库 / 存货编码 / 数量」——
     * 存货名称、规格、单位由存货档案带出，客户表里没有也不该拦。
     */
    static boolean requiredFor(InitModule module, String header) {
        if (module == InitModule.OPENING_STOCK) {
            return "仓库".equals(header) || "存货编码".equals(header) || "数量".equals(header);
        }
        return required(header);
    }

    /** 必填列：编码/名称/数量/单价这几类，其余可空。 */
    static boolean required(String header) {
        return switch (header) {
            case "部门编码", "部门名称", "员工编码", "姓名", "所属部门编码",
                 "往来单位编码", "往来单位名称", "性质",
                 "仓库编码", "仓库名称",
                 "存货编码", "存货名称", "计量单位", "工序编码", "工序名称",
                 "工艺路线编码", "工艺路线名称", "工序顺序", "产品编码", "子件编码",
                 "定额数量", "仓库", "数量" -> true;
            default -> false;
        };
    }

    private static List<String> sampleRow(InitModule module) {
        return switch (module) {
            case DEPT -> List.of(SAMPLE_TAG + "D01", "生产部", "", "10");
            case EMPLOYEE -> List.of(SAMPLE_TAG + "E001", "张伟", "D01", "车间主任",
                    "13800000000", "排产、报工审核", "zhangwei", "生产", "MANU_ORDER");
            case PARTNER -> List.of(SAMPLE_TAG + "KH001", "华东铝业", "客户", "销售一部", "张伟", "", "否");
            case WH -> List.of(SAMPLE_TAG + "CK01", "原料仓", "原料仓", "厂区东侧", "陈仓管", "否");
            case ITEM -> List.of(SAMPLE_TAG + "CP001", "铝棒 Φ80", "Φ80×3000", "件",
                    "全月平均", "20", "原材料", "GY-CP001");
            case PROCESS -> List.of(SAMPLE_TAG + "OP10", "下料", "机加车间", "99", "自制", "计件");
            case ROUTING -> List.of(SAMPLE_TAG + "GY-CP001", "铝棒加工路线", "10", "OP10", "计件", "");
            case BOM -> List.of(SAMPLE_TAG + "CP001", "CL001", "1.2", "1", "2", "原料仓");
            case OPENING_STOCK -> List.of(SAMPLE_TAG + "原料仓", "CL001", "45#圆钢", "Φ60", "kg", "500", "4.2");
        };
    }

    /**
     * 解析上传的 xlsx：按表头名对齐，返回每一行「列名 → 文本值」。
     *
     * @throws IllegalArgumentException 缺必填列 / 空文件 / 只有表头
     */
    public List<Map<String, String>> parse(MultipartFile file, InitModule module) throws IOException {
        if (file == null || file.isEmpty()) throw new IllegalArgumentException("请选择要上传的 Excel 文件");
        try (Workbook wb = WorkbookFactory.create(file.getInputStream())) {
            Sheet sheet = wb.getSheetAt(0);
            Row head = sheet.getRow(0);
            if (head == null) throw new IllegalArgumentException("模板缺少表头行（第一行应为列名）");
            return parseSheet(sheet, head, module);
        }
    }

    /** 供单测直接喂 sheet（不依赖上传）。 */
    static List<Map<String, String>> parseSheet(Sheet sheet, Row head, InitModule module) {
        Map<Integer, String> columns = new LinkedHashMap<>();
        for (Cell cell : head) {
            String name = text(cell).replace(REQUIRED_MARK, "").trim();
            if (!name.isEmpty()) columns.put(cell.getColumnIndex(), name);
        }
        List<String> missing = new ArrayList<>();
        for (String header : module.headers()) {
            if (requiredFor(module, header) && columns.values().stream().noneMatch(header::equals)) missing.add(header);
        }
        if (!missing.isEmpty()) {
            throw new IllegalArgumentException("模板缺少必填列：" + String.join("、", missing)
                    + "；请用系统提供的「" + module.fileName() + "」模板");
        }
        List<Map<String, String>> rows = new ArrayList<>();
        for (int r = 1; r <= sheet.getLastRowNum(); r++) {
            Row row = sheet.getRow(r);
            if (row == null) continue;
            Map<String, String> values = new LinkedHashMap<>();
            boolean allBlank = true;
            for (Map.Entry<Integer, String> col : columns.entrySet()) {
                String value = text(row.getCell(col.getKey()));
                values.put(col.getValue(), value);
                if (!value.isBlank()) allBlank = false;
            }
            if (allBlank) continue;
            // 示例行（第二行带「（示例）」标记）跳过，避免把示例当数据导进去
            if (String.valueOf(values.values().iterator().next()).contains(SAMPLE_TAG)) continue;
            values.put("__row", String.valueOf(r + 1));
            rows.add(values);
        }
        if (rows.isEmpty()) throw new IllegalArgumentException("文件里没有数据行（第 1 行是表头，第 2 行是示例）");
        return rows;
    }

    private static String text(Cell cell) {
        if (cell == null) return "";
        return switch (cell.getCellType()) {
            case STRING -> cell.getStringCellValue().trim();
            case NUMERIC -> {
                double v = cell.getNumericCellValue();
                yield v == Math.rint(v) ? String.valueOf((long) v) : String.valueOf(v);
            }
            case BOOLEAN -> String.valueOf(cell.getBooleanCellValue());
            case FORMULA -> {
                try {
                    yield cell.getStringCellValue().trim();
                } catch (Exception e) {
                    double v = cell.getNumericCellValue();
                    yield v == Math.rint(v) ? String.valueOf((long) v) : String.valueOf(v);
                }
            }
            default -> "";
        };
    }
}