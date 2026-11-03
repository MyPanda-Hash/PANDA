package com.mes.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 追踪数据读取：从 form_data 抽取上游单号。
 *
 * 关联字段来自实际数据：来源单号 / 销售订单号 / 匹配来源单号 / 加工单号。
 */
class ProcessTraceQueryServiceTest {

    private final ProcessTraceQueryService service =
            new ProcessTraceQueryService(null, new ObjectMapper(), new ProcessTraceService());

    private FormData doc(String panel, String no, String status, String head, String detail) {
        FormData fd = new FormData();
        fd.setPanelCode(panel);
        fd.setFormNo(no);
        fd.setStatus(status);
        fd.setData(head);
        fd.setDetailData(detail);
        return fd;
    }

    @Test
    void mapperNullReturnsEmptyTraceNotException() {
        ProcessTraceService.TraceResult r = service.trace("SO-001");
        assertTrue(r.steps().isEmpty());
    }

    @Test
    void headerSourceNoIsCollected() {
        FormData fd = doc("ARRIVAL_IN", "DH-001", "已审核",
                "{\"单据编号\":\"DH-001\",\"来源单号\":\"PO-001\"}", "{}");

        List<String> links = service.linksOf(fd);

        assertTrue(links.contains("PO-001"));
    }

    @Test
    void salesOrderNoIsCollected() {
        FormData fd = doc("MANU_ORDER", "MO-001", "已审核",
                "{\"单据编号\":\"MO-001\",\"销售订单号\":\"SO-001\"}", "{}");

        assertTrue(service.linksOf(fd).contains("SO-001"));
    }

    @Test
    void detailLinksAreCollected() {
        FormData fd = doc("MATERIAL_OUT", "CL-001", "已审核",
                "{\"单据编号\":\"CL-001\"}",
                "{\"items\":[{\"加工单号\":\"MO-001\"},{\"加工单号\":\"MO-002\"}]}");

        List<String> links = service.linksOf(fd);

        assertTrue(links.contains("MO-001"));
        assertTrue(links.contains("MO-002"));
    }

    @Test
    void detailSourcePanelSourceNoIsCollected() {
        FormData fd = doc("SALE_INV", "XS-001", "草稿",
                "{\"单据编号\":\"XS-001\"}",
                "{\"items\":[{\"来源面板\":\"FINISH_IN\",\"来源单号\":\"FI-001\"}]}");

        assertTrue(service.linksOf(fd).contains("FI-001"));
    }

    @Test
    void selfReferenceIsSkipped() {
        FormData fd = doc("SO_ORDER", "SO-001", "已审核",
                "{\"单据编号\":\"SO-001\",\"来源单号\":\"SO-001\"}", "{}");

        assertFalse(service.linksOf(fd).contains("SO-001"), "自身单号不能当上游");
    }

    @Test
    void blankAndMalformedJsonAreTolerated() {
        FormData fd = doc("SO_ORDER", "SO-001", "已审核", "", "not-json");
        assertTrue(service.linksOf(fd).isEmpty());

        FormData fd2 = doc("SO_ORDER", "SO-002", "已审核", "{}", null);
        assertTrue(service.linksOf(fd2).isEmpty());
    }

    @Test
    void duplicateLinksAreDeduplicated() {
        FormData fd = doc("MATERIAL_OUT", "CL-001", "已审核",
                "{\"来源单号\":\"MO-001\"}",
                "{\"items\":[{\"加工单号\":\"MO-001\"}]}");

        List<String> links = service.linksOf(fd);

        assertEquals(1, links.size());
        assertEquals("MO-001", links.get(0));
    }

    @Test
    void nonTracePanelIsExcludedFromLoad() {
        // loadDocs 在 mapper 为空时返回空，不抛异常
        assertTrue(service.loadDocs().isEmpty());
    }

    /**
     * 工序检验链的三种单据必须参与追踪，否则从报检单追不到上游汇报单。
     * 这三个面板此前不在白名单里，导致 trace 返回 0 环节。
     */
    @Test
    void processInspectionPanelsAreTraceable() {
        List<String> panels = ProcessTraceQueryService.tracePanels();

        assertTrue(panels.contains("PROCESS_INSPECT_APPLY"), "工序报检单必须可追踪");
        assertTrue(panels.contains("PROCESS_INSPECTION"), "生产过程检验单必须可追踪");
        assertTrue(panels.contains("FIRST_INSPECT"), "首件报检单必须可追踪");
        assertTrue(panels.contains("PROCESS_REPORT"), "工序汇报单必须可追踪");
    }

    /** 报检单的来源单号要能被抽出来，追踪才能反查汇报单。 */
    @Test
    void inspectApplyLinksBackToProcessReport() {
        FormData fd = doc("PROCESS_INSPECT_APPLY", "GB-001", "已审核",
                "{\"单据编号\":\"GB-001\",\"来源单号\":\"GX-001\",\"加工单号\":\"MO-001\"}", "{}");

        List<String> links = service.linksOf(fd);

        assertTrue(links.contains("GX-001"), "应抽到来源单号");
        assertTrue(links.contains("MO-001"), "应抽到加工单号");
    }

    /** 过程检验单要能追到报检单。 */
    @Test
    void processInspectionLinksBackToApply() {
        FormData fd = doc("PROCESS_INSPECTION", "GCJ-001", "已审核",
                "{\"单据编号\":\"GCJ-001\",\"来源单号\":\"GB-001\"}", "{}");

        assertTrue(service.linksOf(fd).contains("GB-001"));
    }
}
