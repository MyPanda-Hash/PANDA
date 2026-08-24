# 材料出库单「选单」按钮专项（MATERIAL_OUT.select.md）

| 属性 | 内容 |
|---|---|
| 文档类型 | 场景设计 |
| 适用场景 | 库存与供应链 |
| 维护状态 | 调研基线 |
| 最后整理 | 2026-08-24 |
| 文档导航 | [文档中心](../../../docs/README.md) |

> 数据来源：真实 T+ ST1022 材料出库单 topText（选单/选单转换 下拉全名），见 `material_out.dom.json` topText 与 `material_out-new-form.json` header。
> 参考结构：engine.js `SALE_OUT_CONFIG.selectConfig`（L804，source/detailMap 平铺）、`PROCESS_REPORT` 选单（L1224，headerMap + detailRows 单据粒度提取明细）。

---

## 一、真实选单子项 → 源单据映射

「选单」下拉 7 个子项 + 「选单转换」4 个子项：

| 选单子项 | 源单据 | 轻MES 现状 |
|---|---|---|
| 选材料出库单（直接领料） | 材料出库单（同单复制改单） | 同面板，无需新面板 |
| 选材料出库单（自制领料） | 材料出库单 | 同面板 |
| **选生产加工单**（含 选生产加工单(新增材料)） | 生产加工单 MANU_ORDER | ✅ 已有面板（selectConfig 主源） |
| 选工序派工单 | 工序派工单 | ❌ **需新增面板 DISPATCH_ORDER** |
| 选领料申请单 | 领料申请单 | ❌ **需新增面板 MATERIAL_REQ** |
| 选材料出库单（共耗领料） | 材料出库单 | 同面板 |

> 判定依据：`选单` 子项均为「选 <单据名>」拉式选单（T+ 语义：仅已审核且未中止的来源单）；轻MES 面板注册表（menus.js/engine.js）无 工序派工单、领料申请单 两个面板。

## 二、需新增面板及理由

1. **工序派工单（建议 panelCode=DISPATCH_ORDER）**
   理由：真实材料出库单可按「工序派工单」领料（派工后按工序材料出库）；轻MES 现有 工序汇报（PROCESS_REPORT）/返修汇报（REWORK_REPORT），无派工单环节，选单子项无源可拉。需先建面板（单据类，明细=工序材料行），再在 MATERIAL_OUT 选单组挂「选工序派工单」子项。

2. **领料申请单（建议 panelCode=MATERIAL_REQ）**
   理由：真实材料出库单可按「领料申请单」选单领料；MO 表头已有「启用领料申请」字段（engine.js L177），说明领料申请是真实流转环节，轻MES 缺该单据面板。需先建面板（单据类，明细=材料行，状态 已审核 后可被选单），再挂子项。

## 三、selectConfig 建议（主选单 = 选生产加工单）

替换现有 MATERIAL_OUT_CONFIG.selectConfig（现配置把 MO 产成品列当材料带入，映射语义错误：`产品名称→材料名称`；表头 `销售订单号` 字段已从新表头移除）。

```json
{
  "source": "MANU_ORDER",
  "title": "选生产加工单",
  "tip": "仅显示已审核且未中止的生产加工单，选中后按加工单材料明细带入材料出库单（对齐 T+ 选单前提：已审核且未中止）",
  "columns": ["单据编号", "单据日期", "生产车间", "预完工日", "客户", "产品名称", "材料名称", "规格型号", "计划数量", "计量单位", "现存量"],
  "headerMap": [
    { "from": "单据编号", "to": "来源单号" },
    { "from": "单据编号", "to": "加工单号" },
    { "from": "生产车间", "to": "生产车间" }
  ],
  "detailRows": "(row) => {\n  const r = MOCK_ROWS.find((x) => x['编号'] === (row['编号'] || row['单据编号']))\n  const head = ((r && r.detail && r.detail.products) || [])[0] || {}\n  return ((r && r.detail && r.detail.materials) || []).map((m) => ({\n    加工单号: r['锭号'] || r['单据编号'] || '',\n    产品名称: head['产品名称'] || '',\n    产品编码: head['产品编码'] || '',\n    材料编码: m['材料编码'],\n    材料名称: m['材料名称'],\n    规格型号: m['规格型号'],\n    计量单位: m['计量单位'],\n    数量: m['计划数量'],\n    仓库: m['预出仓库'],\n    现存量: m['现存量'],\n    可用量: m['可用量'],\n    来源单据: '生产加工单',\n    来源单号: r['锭号'] || r['单据编号'] || '',\n  }))\n}",
  "detailMap": [
    { "from": "材料编码", "to": "材料编码" },
    { "from": "材料名称", "to": "材料名称" },
    { "from": "规格型号", "to": "规格型号" },
    { "from": "计量单位", "to": "计量单位" },
    { "from": "数量", "to": "数量" },
    { "from": "仓库", "to": "仓库" },
    { "from": "现存量", "to": "现存量" },
    { "from": "可用量", "to": "可用量" }
  ]
}
```

要点：
- **detailRows 单据粒度提取**：对齐 PROCESS_REPORT 选单模式（L1263）——选单弹窗列 = 加工单 + 其材料行（columns 内含 材料名称/计划数量），确认后按 `detailRows` 逐张加工单展开 `detail.materials` 为材料出库明细行（加工单号=锭号、产品名称/编码取该单首产成品行补齐）。
- **detailMap 与 detailRows 配合**：detailRows 已产出目标行字段名，detailMap 按字段名配对带入（沿用 engine 契约，两处 from/to 一致即可）。
- **表头**：来源单号/加工单号 回填 MO 单据编号；生产车间带入 MO 生产车间。
- 选生产加工单(新增材料)：同源 MANU_ORDER，仅过滤「累计领用数量 < 计划数量」的材料行（未领完）——预留子项，与主选单共用 selectConfig，加过滤条件。
- 选材料出库单（直接领料/自制领料/共耗领料）：同面板复制改单（source=MATERIAL_OUT 自身），轻MES 暂以「复制」按钮承载，未单独实现。

## 四、实施顺序建议

1. 合并 MATERIAL_OUT.header.json / toolbar.json（主会话）。
2. 实现 selectConfig 替换 + 明细列改造（材料编码/产品名称/来源单号/可用量 等）。
3. 后续迭代：新增 DISPATCH_ORDER、MATERIAL_REQ 面板 → 在选单组挂对应子项（source 指向新面板）。
