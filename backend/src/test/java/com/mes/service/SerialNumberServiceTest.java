package com.mes.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 序列号登记/核销：数量一一对应、单内不重复、同一存货全局唯一；出库必须是在库且属于该存货。
 * 状况表/跟踪表靠聚合，弃审即回滚。
 */
class SerialNumberServiceTest {

    private final SerialNumberService service = new SerialNumberService(null);
    private final ObjectMapper json = new ObjectMapper();

    private FormData doc(String panel, String no, String status, Map<String, Object> head,
                         Map<String, Object> detail) {
        FormData fd = new FormData();
        fd.setPanelCode(panel);
        fd.setFormNo(no);
        fd.setStatus(status);
        try {
            fd.setData(json.writeValueAsString(head));
            fd.setDetailData(json.writeValueAsString(detail));
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
        return fd;
    }

    private FormData inventory(String... managedItems) {
        List<Map<String, Object>> items = new ArrayList<>();
        for (String code : managedItems) {
            items.add(new LinkedHashMap<>(Map.of("存货编码", code, "序列号管理", "是")));
        }
        return doc("INV", "INV", "启用", Map.of(), Map.of("items", items));
    }

    private FormData inbound(String no, String status, String item, double qty, String... serials) {
        List<Map<String, Object>> rows = new ArrayList<>();
        for (String sn : serials) {
            rows.add(new LinkedHashMap<>(Map.of("存货编码", item, "存货名称", "轴套 C", "序列号", sn)));
        }
        return doc("PURCHASE_IN", no, status,
                Map.of("单据日期", "2026-11-12"),
                Map.of("items", List.of(new LinkedHashMap<>(Map.of(
                        "存货编码", item, "存货名称", "轴套 C", "实收数量", qty))),
                        "serials", rows));
    }

    private FormData outbound(String no, String status, String item, double qty, String... serials) {
        List<Map<String, Object>> rows = new ArrayList<>();
        for (String sn : serials) {
            rows.add(new LinkedHashMap<>(Map.of("存货编码", item, "序列号", sn)));
        }
        return doc("SALE_OUT", no, status,
                Map.of("单据日期", "2026-11-13"),
                Map.of("items", List.of(new LinkedHashMap<>(Map.of(
                        "存货编码", item, "存货名称", "轴套 C", "数量", qty))),
                        "serials", rows));
    }

    @Test
    void inboundNeedsOneSerialPerUnit() {
        List<FormData> docs = List.of(inventory("CP005"));
        Map<String, Object> head = Map.of("单据日期", "2026-11-12");

        // 2 件 → 2 个序列号：通过
        service.validate(docs, "PURCHASE_IN", "RK-1", head,
                Map.of("items", List.of(new LinkedHashMap<>(Map.of("存货编码", "CP005", "实收数量", 2))),
                        "serials", List.of(new LinkedHashMap<>(Map.of("存货编码", "CP005", "序列号", "SN-1")),
                                new LinkedHashMap<>(Map.of("存货编码", "CP005", "序列号", "SN-2")))));

        // 2 件只登记 1 个：拒绝
        IllegalStateException e = assertThrows(IllegalStateException.class, () -> service.validate(
                docs, "PURCHASE_IN", "RK-2", head,
                Map.of("items", List.of(new LinkedHashMap<>(Map.of("存货编码", "CP005", "实收数量", 2))),
                        "serials", List.of(new LinkedHashMap<>(Map.of("存货编码", "CP005", "序列号", "SN-1"))))));
        assertTrue(e.getMessage().contains("一一对应"), e.getMessage());
    }

    @Test
    void inboundRejectsDuplicateAndUnmanagedAndReusedSerials() {
        List<FormData> docs = List.of(inventory("CP005"),
                inbound("RK-OLD", "已审核", "CP005", 1, "SN-9"));
        Map<String, Object> head = Map.of("单据日期", "2026-11-12");

        assertThrows(IllegalStateException.class, () -> service.validate(docs, "PURCHASE_IN", "RK-3", head,
                Map.of("items", List.of(new LinkedHashMap<>(Map.of("存货编码", "CP005", "实收数量", 2))),
                        "serials", List.of(new LinkedHashMap<>(Map.of("存货编码", "CP005", "序列号", "SN-1")),
                                new LinkedHashMap<>(Map.of("存货编码", "CP005", "序列号", "SN-1"))))));

        assertThrows(IllegalStateException.class, () -> service.validate(docs, "PURCHASE_IN", "RK-4", head,
                Map.of("items", List.of(new LinkedHashMap<>(Map.of("存货编码", "CL001", "实收数量", 1))),
                        "serials", List.of(new LinkedHashMap<>(Map.of("存货编码", "CL001", "序列号", "SN-1"))))));

        IllegalStateException reused = assertThrows(IllegalStateException.class,
                () -> service.validate(docs, "PURCHASE_IN", "RK-5", head,
                        Map.of("items", List.of(new LinkedHashMap<>(Map.of("存货编码", "CP005", "实收数量", 1))),
                                "serials", List.of(new LinkedHashMap<>(Map.of("存货编码", "CP005",
                                        "序列号", "SN-9"))))));
        assertTrue(reused.getMessage().contains("已存在"), reused.getMessage());
    }

    @Test
    void outboundConsumesInStockSerialsOnly() {
        List<FormData> docs = List.of(inventory("CP005"),
                inbound("RK-1", "已审核", "CP005", 2, "SN-1", "SN-2"));
        Map<String, Object> head = Map.of("单据日期", "2026-11-13");

        // 在库的序列号可以出库
        service.validate(docs, "SALE_OUT", "CK-1", head,
                Map.of("items", List.of(new LinkedHashMap<>(Map.of("存货编码", "CP005", "数量", 1))),
                        "serials", List.of(new LinkedHashMap<>(Map.of("存货编码", "CP005", "序列号", "SN-1")))));

        // 不存在的序列号 / 别的存货的序列号
        assertThrows(IllegalStateException.class, () -> service.validate(docs, "SALE_OUT", "CK-2", head,
                Map.of("items", List.of(new LinkedHashMap<>(Map.of("存货编码", "CP005", "数量", 1))),
                        "serials", List.of(new LinkedHashMap<>(Map.of("存货编码", "CP005", "序列号", "SN-X"))))));

        // 已经在别的出库单里出过的序列号
        List<FormData> shipped = new ArrayList<>(docs);
        shipped.add(outbound("CK-OLD", "已审核", "CP005", 1, "SN-1"));
        IllegalStateException e = assertThrows(IllegalStateException.class,
                () -> service.validate(shipped, "SALE_OUT", "CK-3", head,
                        Map.of("items", List.of(new LinkedHashMap<>(Map.of("存货编码", "CP005", "数量", 1))),
                                "serials", List.of(new LinkedHashMap<>(Map.of("存货编码", "CP005",
                                        "序列号", "SN-1"))))));
        assertTrue(e.getMessage().contains("不能重复出库"), e.getMessage());
    }

    @Test
    void aggregateTracksInStockAndShipped() {
        List<FormData> docs = List.of(inventory("CP005"),
                inbound("RK-1", "已审核", "CP005", 2, "SN-1", "SN-2"),
                outbound("CK-1", "已审核", "CP005", 1, "SN-1"));

        List<SerialNumberService.Serial> rows = service.aggregate(docs);
        assertEquals(2, rows.size());
        SerialNumberService.Serial sn1 = rows.stream().filter(s -> s.serialNo().equals("SN-1")).findFirst().orElseThrow();
        SerialNumberService.Serial sn2 = rows.stream().filter(s -> s.serialNo().equals("SN-2")).findFirst().orElseThrow();
        assertEquals("已出库", sn1.status());
        assertEquals("CK-1", sn1.outDoc());
        assertEquals("在库", sn2.status());
        assertEquals("RK-1", sn2.inDoc());
    }

    @Test
    void voidedDocsDoNotCount() {
        List<FormData> docs = List.of(inventory("CP005"),
                inbound("RK-1", "已审核", "CP005", 1, "SN-1"),
                inbound("RK-2", "作废", "CP005", 1, "SN-2"));
        List<SerialNumberService.Serial> rows = service.aggregate(docs);
        assertEquals(1, rows.size());
        assertEquals("SN-1", rows.get(0).serialNo());
    }

    @Test
    void draftInboundDoesNotReserveSerials() {
        // 草稿入库单不算已登记：否则同一批序列号先在草稿里占掉，审核时自己撞自己
        List<FormData> docs = List.of(inventory("CP005"),
                inbound("RK-DRAFT", "草稿", "CP005", 1, "SN-D"));
        assertEquals(0, service.aggregate(docs).size());
    }

    @Test
    void manualRegistryAlsoFeedsStock() {
        FormData manual = doc("SERIAL_NO", "XL-1", "已审核", Map.of("单据日期", "2026-11-01"),
                Map.of("items", List.of(new LinkedHashMap<>(Map.of("存货编码", "CP005", "序列号", "SN-M")))));
        List<FormData> docs = List.of(inventory("CP005"), manual);
        assertEquals(1, service.aggregate(docs).size());
        assertEquals("在库", service.aggregate(docs).get(0).status());
    }
}
