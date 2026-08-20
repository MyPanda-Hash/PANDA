# 销售出库单（SALE_OUT）「选单」按钮专项：真实子项 + 源单据 + selectConfig 建议

> 数据来源：sale_out.dom.json topText（选单下拉全名）、mech-isc-tree-raw.json（T+ 菜单确认源单据面板存在性）
> 现有配置：engine.js SALE_OUT_CONFIG.selectConfig（source=SO_ORDER，L804-822）

## 一、真实「选单」下拉子项（4 个业务源 + 1 个设置项）
```
选单 ▾
├─ 选销售订单
├─ 选销货单
├─ 选销售出库单(普通销售)
├─ 选配货单
└─ 设置默认功能
```

## 二、子项 → 源单据面板对照
| 真实子项 | 源单据面板 | 轻MES 现状 | 结论 |
|---|---|---|---|
| 选销售订单 | 销售订单（SO_ORDER，SA03） | 已有面板，且 SALE_OUT 现有 selectConfig 已配 source=SO_ORDER | **可直接用**（建议补 sourceNoField/销售订单号 回填） |
| 选销货单 | 销货单（T+ 菜单「销货单」，mech-isc-tree L519） | **缺失**（frontend/src 全库无 销货单） | **需新增面板 销货单（建议 panelCode=SALE_INV）** |
| 选销售出库单(普通销售) | 销售出库单自身（SALE_OUT，出库类别=普通销售，已审核） | 有面板，但 selectConfig 不支持自选 | 可配置 source=SALE_OUT + 出库类别 过滤（自选/复制源） |
| 选配货单 | 配货单（T+ 菜单「配货单」，mech-isc-tree L2085） | **缺失**（frontend/src 全库无 配货单） | **需新增面板 配货单（建议 panelCode=PICK_ORDER）** |
| 设置默认功能 | 工具栏偏好设置 | 未实现（UNIMPLEMENTED） | 保留占位 |

## 三、真实「生单」下拉子项（2 个业务动作）
```
生单 ▾
├─ 生成销货单          → 目标面板 销货单（缺失 → 需新增面板 SALE_INV）
├─ 生成销售出库单(销售退货) → 目标 SALE_OUT 自身（出库类别=销售退货，红字退货单）
└─ 设置默认功能
```
> 生单与选单共享同一组源/目标面板：销货单、配货单 缺失是同一批新增工作。

## 四、selectConfig 建议（轻MES 格式，参考现有 SALE_OUT selectConfig）

### 4.1 保留并增强：选销售订单（source=SO_ORDER）
```js
selectConfig: {
  source: 'SO_ORDER',
  title: '选销售订单',
  tip: '仅显示已审核且未中止的销售订单，选中后明细带入销售出库单（对齐 T+ 选单前提）',
  columns: ['单据编号', '单据日期', '客户', '业务员', '预计交货日期', '存货名称', '数量', '销售单位'],
  headerMap: [
    { from: '客户', to: '客户' },
    { from: '结算客户', to: '结算客户' },
    { from: '单据编号', to: '来源单号' },        // 增强：回填来源单号（真实只读字段）
    { from: '单据编号', to: '销售订单号' },      // 增强：回填销售订单号（联查需要）
  ],
  detailMap: [
    { from: '存货名称', to: '存货名称' },
    { from: '存货编码', to: '存货编码' },
    { from: '规格型号', to: '规格型号' },
    { from: '销售单位', to: '计量单位' },
    { from: '数量', to: '数量' },
    { from: '现存量', to: '现存量' },
  ],
  sourceNoField: '来源单号',   // 增强：未配置时默认写「来源单据」
}
```

### 4.2 新增：选销售出库单(普通销售)（source=SALE_OUT 自选）
```js
// 语义：把已审核的普通销售出库单整单/选行复制为新的销售出库单（改单/补单场景）
{
  source: 'SALE_OUT',
  title: '选销售出库单(普通销售)',
  tip: '仅显示已审核且出库类别=普通销售的销售出库单，选中后明细整单带入',
  condition: { 单据状态: '已审核', 出库类别: '普通销售' },
  headerMap: [
    { from: '客户', to: '客户' },
    { from: '结算客户', to: '结算客户' },
    { from: '单据编号', to: '来源单号' },
  ],
  detailMap: [ /* 明细整单直带：仓库/存货名称/存货编码/规格型号/计量单位/数量/成本价/售价/含税售价/销售金额/税额/含税销售金额/折扣金额/现存量/退货原因 */ ],
  sourceNoField: '来源单号',
}
```

### 4.3 待新增面板落地的两个源（选销货单 / 选配货单）
- 引擎 selectConfig 目前**单源限制**（一个面板只读一个 source）。真实 T+ 一个选单按钮 4 个源子项 →
  **建议引擎扩展 selectConfig 支持多入口**（对象数组 `selectConfigs: [{source,title,...}...]` 或按 actions 映射），
  前端「选单」组 actions 逐个绑定弹窗；当前最小实现 = 保留 SO_ORDER 单选单 + 其余子项 UNIMPLEMENTED 提示。
- 销货单面板（SALE_INV）落地后：`selectConfig.source='SALE_INV'`，headerMap 客户/结算客户/来源单号，detailMap 存货/数量/售价/含税售价/销售金额（销货单即销售发票口径）。
- 配货单面板（PICK_ORDER）落地后：`selectConfig.source='PICK_ORDER'`，detailMap 按配货明细（存货/数量/货位）带入。

## 五、需新增面板清单与理由
| 需新增面板 | 建议 panelCode | 理由（来源） |
|---|---|---|
| 销货单 | SALE_INV | 选单源「选销货单」+ 生单目标「生成销货单」；T+ 菜单确认存在（mech-isc-tree L519「销货单」、L708 销售订单批量生销货单）；机械行业销售开票/销货业务闭环需要 |
| 配货单 | PICK_ORDER | 选单源「选配货单」；T+ 菜单确认存在（mech-isc-tree L2085「配货单」、L717 销售订单批量生配货单）；按客户订单配货出库场景需要 |

> 说明：真实工具栏另有「选销售出库单(普通销售)」自选源，属 SALE_OUT 自身，无需新面板（见 4.2）。