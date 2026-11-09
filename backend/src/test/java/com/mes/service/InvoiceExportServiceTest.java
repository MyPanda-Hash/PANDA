package com.mes.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.mapper.FormDataMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;

/**
 * 开票清单导出：把发票明细 + 客户开票四要素 + 存货税收分类编码拼成开票渠道要的清单。
 */
@ExtendWith(MockitoExtension.class)
class InvoiceExportServiceTest {

    @Mock private FormDataMapper formMapper;

    private final ObjectMapper json = new ObjectMapper();
    private InvoiceExportService service;

    @BeforeEach
    void setUp() {
        service = new InvoiceExportService(formMapper);
    }

    private FormData doc(String panel, String no, Map<String, Object> head, Map<String, Object> detail) throws Exception {
        FormData d = new FormData();
        d.setPanelCode(panel);
        d.setFormNo(no);
        d.setData(json.writeValueAsString(head));
        d.setDetailData(json.writeValueAsString(detail));
        return d;
    }

    private void seed() throws Exception {
        FormData partner = doc("PARTNER", "PARTNER-001",
                Map.of("往来单位编码", "KH001", "往来单位名称", "华东铝业",
                        "统一社会信用代码", "91310114666025597Y",
                        "地址", "上海市", "电话", "021-1234",
                        "开户行", "招商银行", "银行账号", "6228"),
                Map.of());
        FormData inv = doc("INV", "INV-001", Map.of("备注", ""),
                Map.of("items", List.of(Map.of("存货编码", "CP001", "税收分类编码", "1090101030000000000"))));
        FormData invoice = doc("SALE_INVOICE", "FP-1",
                Map.of("单据编号", "FP-1", "客户", "华东铝业", "客户编码", "KH001", "开票类型", "增值税普通发票"),
                Map.of("items", List.of(Map.of(
                        "存货编码", "CP001", "存货名称", "铝棒 Φ80", "规格型号", "Φ80×3000",
                        "计量单位", "件", "数量", 10, "无税单价", 33.9, "税率%", 13,
                        "税额", 39, "价税合计", 339))));

        when(formMapper.selectList(any())).thenReturn(List.of(partner), List.of(inv));
        when(formMapper.selectOne(any())).thenReturn(invoice);
    }

    @Test
    void buildsRowWithBuyerTaxInfoAndTaxCode() throws Exception {
        seed();
        List<Map<String, String>> rows = service.rows(List.of("FP-1"));

        assertEquals(1, rows.size());
        Map<String, String> r = rows.get(0);
        assertEquals("华东铝业", r.get("购买方名称"));
        assertEquals("91310114666025597Y", r.get("购买方纳税人识别号"));
        assertEquals("1090101030000000000", r.get("税收分类编码"));
        assertEquals("铝棒 Φ80", r.get("商品名称"));
        assertEquals("10", r.get("数量"));
        assertEquals("300", r.get("金额"), "金额 = 价税合计 - 税额");
        assertEquals("39", r.get("税额"));
        assertEquals("", r.get("缺失项"), "数据齐全时不应有缺失项提示");
    }

    @Test
    void flagsMissingTaxCode() throws Exception {
        FormData partner = doc("PARTNER", "P1", Map.of("往来单位编码", "KH001", "往来单位名称", "华东铝业"), Map.of());
        FormData inv = doc("INV", "I1", Map.of("备注", ""),
                Map.of("items", List.of(Map.of("存货编码", "CP001"))));
        FormData invoice = doc("SALE_INVOICE", "FP-1",
                Map.of("单据编号", "FP-1", "客户", "华东铝业", "客户编码", "KH001"),
                Map.of("items", List.of(Map.of("存货编码", "CP001", "存货名称", "铝棒", "数量", 1,
                        "税额", 0, "价税合计", 10))));
        when(formMapper.selectList(any())).thenReturn(List.of(partner), List.of(inv));
        when(formMapper.selectOne(any())).thenReturn(invoice);

        Map<String, String> r = service.rows(List.of("FP-1")).get(0);
        assertTrue(r.get("缺失项").contains("税收分类编码"));
        assertTrue(r.get("缺失项").contains("购买方税号"));
    }

    /** 导出必须是真正的 xlsx（zip + POI 可解析），数值列是数字单元格而不是文本。 */
    @Test
    void exportsRealXlsxWithNumericCells() throws Exception {
        seed();
        List<Map<String, String>> rows = service.rows(List.of("FP-1"));
        byte[] xlsx = service.toXlsx(rows);

        // xlsx 是 zip：头两字节 PK
        assertEquals('P', (char) xlsx[0]);
        assertEquals('K', (char) xlsx[1]);

        try (org.apache.poi.xssf.usermodel.XSSFWorkbook wb =
                     new org.apache.poi.xssf.usermodel.XSSFWorkbook(new java.io.ByteArrayInputStream(xlsx))) {
            var sheet = wb.getSheet("开票清单");
            assertEquals("发票号", sheet.getRow(0).getCell(0).getStringCellValue());
            assertEquals("购买方纳税人识别号", sheet.getRow(0).getCell(3).getStringCellValue());
            assertEquals("FP-1", sheet.getRow(1).getCell(0).getStringCellValue());
            // 数量列（第 11 列，索引 11）应为数字 10
            var qty = sheet.getRow(1).getCell(11);
            assertEquals(org.apache.poi.ss.usermodel.CellType.NUMERIC, qty.getCellType());
            assertEquals(10.0, qty.getNumericCellValue(), 0.001);
            // 金额列（索引 13）= 价税合计 - 税额 = 300
            assertEquals(300.0, sheet.getRow(1).getCell(13).getNumericCellValue(), 0.001);
            // 缺失项为空
            assertEquals("", sheet.getRow(1).getCell(19).getStringCellValue());
        }
    }

}
