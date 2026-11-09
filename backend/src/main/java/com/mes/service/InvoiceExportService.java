package com.mes.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.mapper.FormDataMapper;
import org.apache.poi.ss.usermodel.BorderStyle;
import org.apache.poi.ss.usermodel.Cell;
import org.apache.poi.ss.usermodel.CellStyle;
import org.apache.poi.ss.usermodel.FillPatternType;
import org.apache.poi.ss.usermodel.Font;
import org.apache.poi.ss.usermodel.HorizontalAlignment;
import org.apache.poi.ss.usermodel.IndexedColors;
import org.apache.poi.ss.usermodel.Row;
import org.apache.poi.ss.usermodel.Sheet;
import org.apache.poi.ss.usermodel.VerticalAlignment;
import org.apache.poi.xssf.usermodel.XSSFWorkbook;
import org.springframework.stereotype.Service;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.util.Set;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 开票清单导出。
 *
 * <p>系统不签发发票，但它是开票数据的唯一来源。这里把「发票单据 + 客户开票四要素
 * + 存货税收分类编码/税率」拼成开票渠道需要的清单：
 * <ul>
 *   <li>开票软件导入格式：一行一个商品，购买方信息重复填写；</li>
 *   <li>电子税务局手填参考：同样的数据，便于照着界面录入。</li>
 * </ul>
 *
 * <p>缺少税收分类编码的行会带上提示列，避免导入后才在开票软件里报错。
 */
@Service
public class InvoiceExportService {

    private final FormDataMapper formMapper;
    private final ObjectMapper json = new ObjectMapper();

    public InvoiceExportService(FormDataMapper formMapper) {
        this.formMapper = formMapper;
    }

    /** 一行开票商品。null 值统一转空串，CSV 里不留 "null"。 */
    public List<Map<String, String>> rows(List<String> invoiceNos) {
        Map<String, Map<String, Object>> partnerByCode = partnerCache();
        Map<String, Map<String, Object>> invByCode = invCache();

        List<Map<String, String>> out = new ArrayList<>();
        for (String no : invoiceNos) {
            FormData doc = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                    .eq(FormData::getPanelCode, "SALE_INVOICE").eq(FormData::getFormNo, no));
            if (doc == null) continue;
            Map<String, Object> head = parse(doc.getData());
            Map<String, Object> detail = parse(doc.getDetailData());
            String customerCode = str(head.get("客户编码"));
            Map<String, Object> partner = partnerByCode.getOrDefault(customerCode, Map.of());

            List<?> items = detail.get("items") instanceof List<?> l ? l : List.of();
            int line = 0;
            for (Object o : items) {
                if (!(o instanceof Map<?, ?> item)) continue;
                line++;
                String invCode = str(item.get("存货编码"));
                Map<String, Object> inv = invByCode.getOrDefault(invCode, Map.of());
                double gross = num(item.get("价税合计"));
                double tax = num(item.get("税额"));
                double amount = round2(gross - tax);
                String taxCode = str(inv.get("税收分类编码"));

                Map<String, String> r = new LinkedHashMap<>();
                r.put("发票号", no);
                r.put("行号", String.valueOf(line));
                r.put("购买方名称", str(head.getOrDefault("客户", partner.get("往来单位名称"))));
                r.put("购买方纳税人识别号", str(partner.get("统一社会信用代码")));
                r.put("购买方地址", str(partner.get("地址")));
                r.put("购买方电话", str(partner.get("电话")));
                r.put("购买方开户行", str(partner.get("开户行")));
                r.put("购买方银行账号", str(partner.get("银行账号")));
                r.put("商品名称", str(item.get("存货名称")));
                r.put("规格型号", str(item.get("规格型号")));
                r.put("单位", str(item.get("计量单位")));
                r.put("数量", plain(item.get("数量")));
                r.put("单价", plain(item.get("无税单价")));
                r.put("金额", plain(amount));
                r.put("税率", plain(item.get("税率%")));
                r.put("税额", plain(tax));
                r.put("税收分类编码", taxCode);
                r.put("缺失项", missing(head, partner, taxCode));
                r.put("备注", str(head.get("备注")));
                r.put("开票类型", str(head.get("开票类型")));
                out.add(r);
            }
        }
        return out;
    }

    /** 导出列顺序：开票软件导入优先，附带手填需要的列。 */
    public static final List<String> COLUMNS = List.of(
            "发票号", "行号",
            "购买方名称", "购买方纳税人识别号", "购买方地址", "购买方电话", "购买方开户行", "购买方银行账号",
            "商品名称", "规格型号", "单位", "数量", "单价", "金额", "税率", "税额", "税收分类编码",
            "开票类型", "备注", "缺失项");

    /** 这些列按数值写入，Excel 里可直接求和；其余按文本写，避免编码/单号被吃掉前导零。 */
    private static final Set<String> NUMERIC_COLUMNS = Set.of("行号", "数量", "单价", "金额", "税率", "税额");

    /**
     * 生成真正的 .xlsx（不是 CSV）。
     *
     * <p>表头冻结、加粗、灰底；数值列写成数字单元格，金额列保留两位；
     * 「缺失项」列非空时标红，导出的同时就能看出哪张票还缺开票要素。
     */
    public byte[] toXlsx(List<Map<String, String>> rows) throws IOException {
        try (XSSFWorkbook wb = new XSSFWorkbook(); ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            Sheet sheet = wb.createSheet("开票清单");
            sheet.createFreezePane(0, 1);

            Font headFont = wb.createFont();
            headFont.setBold(true);
            headFont.setFontHeightInPoints((short) 11);

            CellStyle headStyle = wb.createCellStyle();
            headStyle.setFont(headFont);
            headStyle.setAlignment(HorizontalAlignment.CENTER);
            headStyle.setVerticalAlignment(VerticalAlignment.CENTER);
            headStyle.setFillForegroundColor(IndexedColors.GREY_25_PERCENT.getIndex());
            headStyle.setFillPattern(FillPatternType.SOLID_FOREGROUND);
            headStyle.setBorderBottom(BorderStyle.THIN);

            CellStyle textStyle = wb.createCellStyle();
            textStyle.setVerticalAlignment(VerticalAlignment.CENTER);
            textStyle.setBorderBottom(BorderStyle.HAIR);

            CellStyle numStyle = wb.createCellStyle();
            numStyle.cloneStyleFrom(textStyle);
            numStyle.setAlignment(HorizontalAlignment.RIGHT);
            numStyle.setDataFormat(wb.createDataFormat().getFormat("0.00"));

            CellStyle intStyle = wb.createCellStyle();
            intStyle.cloneStyleFrom(textStyle);
            intStyle.setAlignment(HorizontalAlignment.RIGHT);

            CellStyle warnStyle = wb.createCellStyle();
            warnStyle.cloneStyleFrom(textStyle);
            warnStyle.setAlignment(HorizontalAlignment.LEFT);

            Font warnFont = wb.createFont();
            warnFont.setColor(IndexedColors.RED.getIndex());
            CellStyle warnTextStyle = wb.createCellStyle();
            warnTextStyle.cloneStyleFrom(textStyle);
            warnTextStyle.setFont(warnFont);

            Row head = sheet.createRow(0);
            head.setHeightInPoints(22);
            for (int c = 0; c < COLUMNS.size(); c++) {
                Cell cell = head.createCell(c);
                cell.setCellValue(COLUMNS.get(c));
                cell.setCellStyle(headStyle);
            }

            int r = 1;
            for (Map<String, String> row : rows) {
                Row xr = sheet.createRow(r++);
                for (int c = 0; c < COLUMNS.size(); c++) {
                    String name = COLUMNS.get(c);
                    String raw = row.getOrDefault(name, "");
                    Cell cell = xr.createCell(c);
                    if ("缺失项".equals(name)) {
                        cell.setCellValue(raw);
                        cell.setCellStyle(raw.isEmpty() ? warnStyle : warnTextStyle);
                    } else if (NUMERIC_COLUMNS.contains(name) && !raw.isEmpty() && isNumeric(raw)) {
                        double v = Double.parseDouble(raw);
                        cell.setCellValue(v);
                        cell.setCellStyle("行号".equals(name) ? intStyle : numStyle);
                    } else {
                        cell.setCellValue(raw);
                        cell.setCellStyle(textStyle);
                    }
                }
            }

            int[] widths = {16, 6, 18, 26, 22, 16, 20, 22, 22, 16, 8, 10, 12, 14, 8, 12, 22, 16, 20, 24};
            for (int c = 0; c < COLUMNS.size(); c++) {
                sheet.setColumnWidth(c, (widths[Math.min(c, widths.length - 1)]) * 256);
            }
            sheet.setAutoFilter(new org.apache.poi.ss.util.CellRangeAddress(0, Math.max(0, rows.size()), 0, COLUMNS.size() - 1));

            wb.write(out);
            return out.toByteArray();
        }
    }

    private static boolean isNumeric(String s) {
        try {
            Double.parseDouble(s);
            return true;
        } catch (NumberFormatException e) {
            return false;
        }
    }

    private String missing(Map<String, Object> head, Map<String, Object> partner, String taxCode) {
        List<String> m = new ArrayList<>();
        if (str(partner.get("统一社会信用代码")).isEmpty()) m.add("购买方税号");
        if (taxCode.isEmpty()) m.add("税收分类编码");
        if (str(head.get("客户")).isEmpty()) m.add("购买方名称");
        return String.join("；", m);
    }

    private Map<String, Map<String, Object>> partnerCache() {
        Map<String, Map<String, Object>> out = new HashMap<>();
        for (FormData d : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "PARTNER"))) {
            Map<String, Object> head = parse(d.getData());
            String code = str(head.get("往来单位编码"));
            if (!code.isEmpty()) out.putIfAbsent(code, head);
        }
        return out;
    }

    /** 存货档案：税收分类编码在明细里，按存货编码索引。 */
    private Map<String, Map<String, Object>> invCache() {
        Map<String, Map<String, Object>> out = new HashMap<>();
        for (FormData d : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "INV"))) {
            Map<String, Object> detail = parse(d.getDetailData());
            if (!(detail.get("items") instanceof List<?> items)) continue;
            for (Object o : items) {
                if (!(o instanceof Map<?, ?> it)) continue;
                String code = str(it.get("存货编码"));
                if (!code.isEmpty()) out.putIfAbsent(code, castMap(it));
            }
        }
        return out;
    }

    private Map<String, Object> castMap(Map<?, ?> src) {
        Map<String, Object> m = new LinkedHashMap<>();
        src.forEach((k, v) -> m.put(String.valueOf(k), v));
        return m;
    }

    private Map<String, Object> parse(String raw) {
        if (raw == null || raw.isBlank()) return Map.of();
        try {
            return json.readValue(raw, new com.fasterxml.jackson.core.type.TypeReference<Map<String, Object>>() { });
        } catch (Exception e) {
            return Map.of();
        }
    }

    private static String str(Object o) {
        return o == null ? "" : String.valueOf(o).trim();
    }

    private static String plain(Object o) {
        double d = num(o);
        if (d == Math.rint(d)) return String.valueOf((long) d);
        return String.valueOf(round2(d));
    }

    private static double num(Object o) {
        if (o == null) return 0;
        try {
            return Double.parseDouble(String.valueOf(o).trim());
        } catch (NumberFormatException e) {
            return 0;
        }
    }

    private static double round2(double v) {
        return Math.round(v * 100) / 100.0;
    }

}