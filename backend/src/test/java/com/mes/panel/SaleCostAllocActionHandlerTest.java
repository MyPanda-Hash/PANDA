package com.mes.panel;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.atLeastOnce;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.mapper.FormDataMapper;

/**
 * 销售费用「执行分摊」：把分摊单的金额回写到发票。
 *
 * <p>两条容易踩的规则：
 * <ul>
 *   <li>同一张票被多张分摊单摊过时，`分摊费用` 累加、`分摊单号` 要**记全**（用「、」拼接），
 *       不能后一张把前一张覆盖掉——否则票上只剩最后一张单号，对不上账；</li>
 *   <li>分摊单只能执行一次，已执行的再点直接拒绝（幂等）。</li>
 * </ul>
 */
@ExtendWith(MockitoExtension.class)
class SaleCostAllocActionHandlerTest {

    @Mock private FormDataMapper formMapper;

    private final ObjectMapper json = new ObjectMapper();

    private FormData doc(String panel, String no, String status, Map<String, Object> head,
                         Map<String, Object> detail) throws Exception {
        FormData d = new FormData();
        d.setPanelCode(panel);
        d.setFormNo(no);
        d.setStatus(status);
        d.setData(json.writeValueAsString(head));
        d.setDetailData(json.writeValueAsString(detail));
        d.setCreateTime(LocalDateTime.now());
        return d;
    }

    private FormData alloc(String status, String 分摊状态, double amount) throws Exception {
        Map<String, Object> head = new LinkedHashMap<>();
        head.put("单据编号", "FT-1");
        head.put("业务类型", "销售费用分摊");
        head.put("分摊状态", 分摊状态);
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("费用单号", "FY-1");
        row.put("费用项目", "运费");
        row.put("分摊对象", "销售发票");
        row.put("销售发票号", "FP-1");
        row.put("分摊金额", amount);
        return doc("SALE_COST_ALLOC", "FT-1", status, head, Map.of("items", List.of(row)));
    }

    private FormData invoice(String existingFee, String existingNos) throws Exception {
        Map<String, Object> head = new LinkedHashMap<>();
        head.put("单据编号", "FP-1");
        head.put("价税合计", 29.6);
        head.put("分摊费用", new BigDecimal(existingFee));
        head.put("分摊单号", existingNos);
        return doc("SALE_INVOICE", "FP-1", "已审核", head, Map.of("items", List.of()));
    }

    private FormData execute(FormData allocDoc, FormData invoiceDoc) {
        when(formMapper.selectOne(any())).thenReturn(allocDoc, invoiceDoc);
        SaleCostAllocActionHandler handler = new SaleCostAllocActionHandler(formMapper, json);
        Map<String, Object> out = handler.handle(new PanelActionContext("SALE_COST_ALLOC", "执行分摊",
                Map.of("编号", "FT-1"), Map.of(), "admin"));
        return invoiceDoc;
    }

    @Test
    void accumulatesFeeAndAppendsEveryAllocationNo() throws Exception {
        FormData invoice = execute(alloc("已审核", "未分摊", 300), invoice("200", "FT-0"));

        ArgumentCaptor<FormData> updated = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper, atLeastOnce()).updateById(updated.capture());
        FormData saved = updated.getAllValues().stream()
                .filter(row -> "SALE_INVOICE".equals(row.getPanelCode())).findFirst().orElseThrow();
        Map<?, ?> head = json.readValue(saved.getData(), Map.class);
        assertEquals(0, new BigDecimal("500").compareTo(new BigDecimal(String.valueOf(head.get("分摊费用")))),
                "同一张票多张分摊单要累加：" + head);
        assertEquals("FT-0、FT-1", head.get("分摊单号"), "分摊单号要记全，不能覆盖：" + head);
    }

    @Test
    void reexecutingTheSameAllocationIsRejected() throws Exception {
        FormData allocDoc = alloc("已审核", "已分摊", 300);
        when(formMapper.selectOne(any())).thenReturn(allocDoc);
        SaleCostAllocActionHandler handler = new SaleCostAllocActionHandler(formMapper, json);

        IllegalStateException ex = assertThrows(IllegalStateException.class, () -> handler.handle(
                new PanelActionContext("SALE_COST_ALLOC", "执行分摊",
                        Map.of("编号", "FT-1"), Map.of(), "admin")));
        assertTrue(ex.getMessage().contains("已经执行过分摊"), ex.getMessage());
    }

    @Test
    void customerOnlyAllocationIsRejected() throws Exception {
        Map<String, Object> head = new LinkedHashMap<>();
        head.put("单据编号", "FT-2");
        head.put("分摊状态", "未分摊");
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("分摊对象", "客户");
        row.put("客户", "华东铝业");
        row.put("分摊金额", 300);
        FormData allocDoc = doc("SALE_COST_ALLOC", "FT-2", "已审核", head,
                Map.of("items", List.of(row)));
        when(formMapper.selectOne(any())).thenReturn(allocDoc);
        SaleCostAllocActionHandler handler = new SaleCostAllocActionHandler(formMapper, json);

        IllegalStateException ex = assertThrows(IllegalStateException.class, () -> handler.handle(
                new PanelActionContext("SALE_COST_ALLOC", "执行分摊",
                        Map.of("编号", "FT-2"), Map.of(), "admin")));
        assertTrue(ex.getMessage().contains("销售发票号"), "按客户分摊要提示改成按发票分摊：" + ex.getMessage());
    }
}
