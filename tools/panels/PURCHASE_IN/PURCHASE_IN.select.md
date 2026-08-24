# 采购入库单「选单」按钮专项（PURCHASE_IN.select）

| 属性 | 内容 |
|---|---|
| 文档类型 | 场景设计 |
| 适用场景 | 库存与供应链 |
| 维护状态 | 调研基线 |
| 最后整理 | 2026-08-24 |
| 文档导航 | [文档中心](../../../docs/README.md) |

> 数据来源：`docs/ref/tplus-live/mech-20260819/st/purchase_in.dom.json` topText（真实下拉子项全名）
> 对齐规范：《面板交互设计规范》§3.3 拉式选单弹窗、§10 单据流转（选单/生单）标准、§10.3 selectConfig 契约。

## 一、真实 T+ 选单子项（4 个源单据）

工具栏「选单 ▾」下拉（topText 原文）：`选采购订单 / 选进货单 / 选到货单 / 选检验单 / 设置默认功能`
另：工具栏还有独立「智能选单」按钮（按 供应商+存货 自动匹配来源单据，T+ 服务端逻辑，轻MES 先映射到 采购订单）。

| 子项 | 源单据（T+ 单据） | 轻MES 现有面板 | 结论 |
|---|---|---|---|
| 选采购订单 | 采购订单 | 无 | **需新增面板 采购订单（PU_ORDER）** |
| 选进货单 | 进货单（采购进货/入库） | 无 | **需新增面板 进货单（PU_IN）** |
| 选到货单 | 到货单 | 无 | **需新增面板 到货单（PU_ARRIVAL）** |
| 选检验单 | 检验单 | 无 | **需新增面板 检验单（PU_INSPECT）** |

## 二、需新增面板清单及理由

| 面板 | 理由 |
|---|---|
| 采购订单 PU_ORDER | 机械行业采购主链路源头（采购订单 → 到货单 → 采购入库单），选单第一源；轻MES 目前只有销售侧 SO_ORDER，采购侧单据面板全部缺失 |
| 到货单 PU_ARRIVAL | 采购到货环节单据（带 到货数量/到货仓库），采购入库单常见选单源 |
| 进货单 PU_IN | 采购进货结算单（含发票信息），选单/生单目标（生单组「生成进货单(4 种发票)」也依赖它） |
| 检验单 PU_INSPECT | 来料质检单据（合格数量→实收数量），机械行业常用选单源 |

> 主链路建议：**采购订单（PU_ORDER）→ 到货单（PU_ARRIVAL）→ 采购入库单（PURCHASE_IN）**；进货单/检验单为并行支线。
> 新增面板按《页面开发规范》§五 Checklist 走（单据类：queryFields/gridTabs/detail.tabs/buttonGroups），编号前缀按 §9.1 登记（建议 PU-/AR-/JS-/JY-）。

## 三、轻MES selectConfig 建议（主源：采购订单）

```js
// 合并进 PURCHASE_IN_CONFIG 的 selectConfig（参考 SALE_OUT/PROCESS_REPORT 现有 selectConfig 写法）
selectConfig: {
  source: 'PU_ORDER',
  title: '选采购订单',
  tip: '仅显示已审核且未中止的采购订单，选中后明细带入采购入库单（对齐 T+ 选单前提）',
  columns: ['单据编号', '单据日期', '供应商', '供应商编码', '预计到货日期', '存货名称', '存货编码', '采购数量', '计量单位'],
  headerMap: [
    { from: '单据编号', to: '采购订单号' },
    { from: '供应商', to: '供应商' },
    { from: '供应商编码', to: '供应商编码' },
    { from: '单据编号', to: '匹配来源单号' },   // 若保留该字段；对齐 FINISH_IN 的 headerMap 写法
  ],
  detailMap: [
    { from: '存货编码', to: '存货编码' },
    { from: '存货名称', to: '存货名称' },
    { from: '规格型号', to: '规格型号' },
    { from: '计量单位', to: '计量单位' },
    { from: '采购数量', to: '实收数量' },       // 采购订单行数量 → 采购入库实收数量
    { from: '单价', to: '单价' },
    { from: '税率%', to: '税率%' },
    { from: '含税单价', to: '含税单价' },
    { from: '金额', to: '金额' },
    { from: '含税金额', to: '含税金额' },
  ],
}
```

说明：
- 数据前提：`queryFormDataList({ panelCode: source, condition: { 单据状态: '已审核' } })`（仅已审核且未中止，规范 §3.3）。
- `headerMap` 的「匹配来源单号」仅在保留该表头字段时启用；新表头设计（PURCHASE_IN.header.json）已删除该字段，改为「采购订单号」回填（对齐真实 readonly 字段）。
- 「选进货单/选到货单/选检验单」在对应面板建成后按同构配置扩展（可配置多个 selectConfig 入口，或由「选单」组 actions 分发到不同弹窗；当前实现层面每面板单 selectConfig，多源分发需主会话在 PanelxForm 选单分支按 action 名切换 source）。

## 四、与现有 selectConfig 配置对照

现有 PURCHASE_IN_CONFIG 无 selectConfig（仅 toolbarDiff 里放了『选单』组但无配置，点击会走 UNIMPLEMENTED 提示）。
对齐目标：FINISH_IN（选生产加工单）/SALE_OUT（选销售订单）/PROCESS_REPORT（选生产加工单，detailRows 单据粒度选单）为参照模板；采购入库单采用「单据粒度选单 + detailMap 明细带入」的 SALE_OUT 模式即可。

## 五、生单/转换联动（关联但独立于选单）

- 生单组「生成进货单(4 种发票)」：目标单 = 进货单（PU_IN）——需新增面板；推式生单后端分支复用 `createMoFromSo` 模式。
- 转换组「转成销售订单/转成销货单/转成销售出库单/转成材料出库单」：销售侧面板已存在（SO_ORDER/SALE_OUT/MATERIAL_OUT 均有），「转成销货单」对应轻MES 销售出库单（SALE_OUT），可先映射；未实现按钮保留 UNIMPLEMENTED 提示。
