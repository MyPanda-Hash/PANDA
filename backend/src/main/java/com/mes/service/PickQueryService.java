package com.mes.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.mapper.FormDataMapper;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 拣货工作台：汇总所有待出库单据的拣货任务，供仓管集中作业。
 * 扫描已审核的销售出库、材料出库、其他出库单。
 */
@Service
public class PickQueryService {

    /** 需要拣货的出库面板。 */
    private static final List<String> OUTBOUND_PANELS =
            List.of("SALE_OUT", "MATERIAL_OUT", "OTHER_OUT");

    private final FormDataMapper formMapper;
    private final ObjectMapper json;
    private final PickTaskService pickTaskService;

    public PickQueryService(FormDataMapper formMapper, ObjectMapper json,
                            PickTaskService pickTaskService) {
        this.formMapper = formMapper;
        this.json = json;
        this.pickTaskService = pickTaskService;
    }

    /** 拣货工作台明细：每张已审核出库单的每行任务。 */
    public List<Map<String, Object>> workbench() {
        List<Map<String, Object>> out = new ArrayList<>();
        for (String panel : OUTBOUND_PANELS) {
            List<FormData> docs = formMapper.selectList(new LambdaQueryWrapper<FormData>()
                    .eq(FormData::getPanelCode, panel)
                    .eq(FormData::getStatus, "已审核"));
            for (FormData fd : docs) {
                Map<String, Object> head = parse(fd.getData());
                Map<String, Object> detail = parse(fd.getDetailData());
                List<PickTaskService.PickTask> tasks =
                        pickTaskService.buildTasks(fd.getFormNo(), head, detail);
                for (PickTaskService.PickTask t : tasks) {
                    Map<String, Object> row = new LinkedHashMap<>();
                    row.put("来源面板", panel);
                    row.put("来源单号", t.sourceNo());
                    row.put("仓库", t.warehouse());
                    row.put("库位", t.location());
                    row.put("存货编码", t.itemCode());
                    row.put("存货名称", t.itemName());
                    row.put("计划数量", t.planQty());
                    row.put("实拣数量", t.pickedQty());
                    row.put("差异数量", t.shortageQty());
                    row.put("拣货状态", t.status());
                    out.add(row);
                }
            }
        }
        return out;
    }

    private Map<String, Object> parse(String value) {
        if (value == null || value.isBlank()) return new LinkedHashMap<>();
        try {
            return json.readValue(value, new TypeReference<Map<String, Object>>() {});
        } catch (Exception ignored) {
            return new LinkedHashMap<>();
        }
    }
}
