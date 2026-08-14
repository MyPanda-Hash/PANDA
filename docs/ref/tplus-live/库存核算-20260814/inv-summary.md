# T+ 库存核算 6 单据面板实测汇总（2026-08-14 真实门户抓取）

> 来源：h2t.chanjet.com 演示账套 [012007] 轻MES，账号 tplusdemo12853
> 页面模式：BAPView/Voucher.aspx?sysId=ST&mId=XXXX&pId=voucherView（列表与表单同页，点新增切换）

## ST1001 采购入库单

### 工具栏分组（实测文本）

```
新增
新增
引入常用单据
设置默认功能
新增
引入常用单据
智能选单
保存
保存
Alt+S
保存新增
Alt+\
保存为草稿
保存为常用单据
保存打印
Alt+G
设置默认功能
保存
保存新增
保存为草稿
保存为常用单据
保存打印
删除
|
审核
|
转换
转成材料出库单
设置默认功能
转成材料出库单
协同
设置默认功能
|
变更
|
工具
现存量查询
变更历史
联查
入库调整情况
生单流程联查
设置
单据设置
移动控件位置
调整控件宽度
工具栏设置
|
打印
直接打印
Alt+P
打印
Alt+;
预览
Alt+/
打印模板设置
Alt+,
导出
Alt+X
明细标签打印
打印情况
设置默认功能
直接打印
打印
预览
打印模板设置
导出
明细标签打印
打印情况
更多
复制
放弃
Alt+Z
草稿
Alt+B
导入
下载导入模板
重新取价
附件
刷新
消息
```

### 表头字段（16）

*单据日期、*单据编号、*业务类型、入库类别、供应商编码、*供应商、*供应商简称、匹配来源单号、经手人、验货人、项目、仓库、来源单据、外部单据号、来源单号、销售订单号

### 明细列（25，含必需标记）

*仓库 | *存货名称 | 存货图片 | 规格型号 | *实收数量 | *计量单位 | 实收数量2 | 计量单位2 | 计量单位组合 | 换算率 | 单价 | 税率% | 单价2 | 含税单价2 | 含税单价 | 金额 | 含税金额 | 费用调整 | 费用金额 | 现存量 | 现存量说明 | 产成品图片 | 1 | 2 | 3

### 网格原始列（明细 tab）

可见：*仓库 | *存货名称 | 存货图片 | 规格型号 | *实收数量 | *计量单位 | 实收数量2 | 计量单位2 | 计量单位组合 | 换算率 | 单价 | 税率% | 单价2 | 含税单价2 | 含税单价 | 金额 | 含税金额 | 费用调整 | 费用金额 | 现存量 | 现存量说明 | 产成品图片 | Percent | Percent | Percent | Percent | 仓库 | 存货名称 | 存货图片 | 规格型号 | 计量单位 | 实收数量 | 单价 | 金额 | 含税单价 | 含税金额

全部（含隐藏）：
```

ID [HIDDEN]
<font>*</font>仓库
<font>*</font>存货名称
自由项组合 [HIDDEN]
存货图片
规格型号
BOM版本号 [HIDDEN]
<font>*</font>实收数量
<font>*</font>计量单位
批号 [HIDDEN]
实收数量2
计量单位2
计量单位组合
换算率
生产日期 [HIDDEN]
失效日期 [HIDDEN]
序列号个数 [HIDDEN]
序列号 [HIDDEN]
货位 [HIDDEN]
单价
税率%
单价2
含税单价2
含税单价
金额
含税金额
入库单号 [HIDDEN]
费用调整
费用金额
SnSeqUpdateToken [HIDDEN]
SNGuid [HIDDEN]
价格策略取值维度 [HIDDEN]
现存量
现存量说明
产成品图片
EditState [HIDDEN]
Val_Inventory [HIDDEN]
Code [HIDDEN]
货位_Val [HIDDEN]
HideValues [HIDDEN]



 [HIDDEN]


 [HIDDEN]


 [HIDDEN]


 [HIDDEN]




 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]

Percent





 [HIDDEN]


 [HIDDEN]
 [HIDDEN]
 [HIDDEN]



 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]


 [HIDDEN]


 [HIDDEN]


 [HIDDEN]




 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]

Percent





 [HIDDEN]


 [HIDDEN]
 [HIDDEN]
 [HIDDEN]



 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]


 [HIDDEN]


 [HIDDEN]


 [HIDDEN]




 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]

Percent





 [HIDDEN]


 [HIDDEN]
 [HIDDEN]
 [HIDDEN]



 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]


 [HIDDEN]


 [HIDDEN]


 [HIDDEN]




 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]

Percent





 [HIDDEN]


 [HIDDEN]
 [HIDDEN]
 [HIDDEN]



 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]

ID [HIDDEN]
仓库
存货名称
存货图片
规格型号
BOM版本号 [HIDDEN]
计量单位
实收数量
单价
金额
含税单价
含税金额
本币单价 [HIDDEN]
本币含税单价 [HIDDEN]
单价2 [HIDDEN]
本币单价2 [HIDDEN]
含税单价2 [HIDDEN]
本币含税单价2 [HIDDEN]
EditState [HIDDEN]
Val_Inventory [HIDDEN]
Code [HIDDEN]
HideValues [HIDDEN]



 [HIDDEN]




 [HIDDEN]






 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]




 [HIDDEN]






 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]




 [HIDDEN]






 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]




 [HIDDEN]






 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
 [HIDDEN]
```

### 表尾

```
1


2


3
```

---

## ST1002 产成品入库单

### 工具栏分组（实测文本）

```
新增
新增
引入常用单据
设置默认功能
新增
引入常用单据
选单
选产成品入库单（自制加工）
选生产加工单
设置默认功能
选产成品入库单（自制加工）
选生产加工单
智能选单
保存
保存
Alt+S
保存新增
Alt+\
保存为草稿
保存为常用单据
保存打印
Alt+G
设置默认功能
保存
保存新增
保存为草稿
保存为常用单据
保存打印
删除
|
审核
|
生单
生成产成品入库单（自制退库）
生成补投生产加工单
生成返工生产加工单
设置默认功能
生成产成品入库单（自制退库）
生成补投生产加工单
生成返工生产加工单
转换
转成销售出库单
设置默认功能
转成销售出库单
设置默认功能
|
变更
|
工具
现存量查询
变更历史
联查
生产加工情况
退库情况
生单流程联查
设置
单据设置
移动控件位置
调整控件宽度
工具栏设置
智能选单设置
|
打印
直接打印
Alt+P
打印
Alt+;
预览
Alt+/
打印模板设置
Alt+,
导出
Alt+X
明细标签打印
打印情况
设置默认功能
直接打印
打印
预览
打印模板设置
导出
明细标签打印
打印情况
更多
复制
放弃
Alt+Z
草稿
Alt+B
导入
下载导入模板
附件
刷新
消息
```

### 表头字段（8）

*单据日期、*单据编号、*业务类型、入库类别、生产车间、加工单号、仓库、匹配来源单号

### 明细列（18，含必需标记）

*产品名称 | *仓库 | 存货图片 | 规格型号 | 智能选单 | *计量单位 | 金额 | 单价 | *实收数量 | 现存量 | 现存量说明 | 图号 | 1 | 2 | 3 | 4 | 5 | 6

### 网格原始列（明细 tab）

可见：IsHasSameFuncAuth | Shorthand | Department_Name | IsSalesman | CreditDate | CreditQuantity | PositionTitle_Name | Memo | Specification | IsMadeSelf | InventoryPriceDTOs_RetailPriceNew | InvoiceSpecification | InvoiceUnit_Name | AutoAddFigureNo | SKUCode | SKUName | SKUInvSCost | SKULatestCost | SKUAvagCost | Inventory_Code | Inventory_Specification | Inventory_Name | Version | Manufactureplant_Name | Warehouse_Name

全部（含隐藏）：
```
编码 [HIDDEN]
名称 [HIDDEN]
IsHasSameFuncAuth
Shorthand
Department_Name
IsSalesman
CreditDate
CreditQuantity
PositionTitle_Name
Memo
Specification
IsMadeSelf
InventoryPriceDTOs_RetailPriceNew
InvoiceSpecification
InvoiceUnit_Name
AutoAddFigureNo
SKUCode
SKUName
SKUInvSCost
SKULatestCost
SKUAvagCost
Inventory_Code
Inventory_Specification
Inventory_Name
Version
Manufactureplant_Name
Warehouse_Name
详细 [HIDDEN]
 [HIDDEN]
 [HIDDEN]

























 [HIDDEN]
 [HIDDEN]
 [HIDDEN]

























 [HIDDEN]
```

### 表尾

```
1


2


3


4


5


6
```

---

## ST1004 其他入库单

### 工具栏分组（实测文本）

```
新增
新增
引入常用单据
设置默认功能
新增
引入常用单据
选单
其他出库单
设置默认功能
其他出库单
智能选单
保存
保存
Alt+S
保存新增
Alt+\
保存为草稿
保存为常用单据
保存打印
Alt+G
设置默认功能
保存
保存新增
保存为草稿
保存为常用单据
保存打印
删除
|
审核
|
转换
转换成其他出库单
设置默认功能
转换成其他出库单
|
变更
|
工具
现存量查询
变更历史
联查
其他出库情况
生单流程联查
设置
单据设置
移动控件位置
调整控件宽度
工具栏设置
智能选单设置
|
打印
直接打印
Alt+P
打印
Alt+;
预览
Alt+/
打印模板设置
Alt+,
导出
Alt+X
明细标签打印
打印情况
设置默认功能
直接打印
打印
预览
打印模板设置
导出
明细标签打印
打印情况
更多
复制
放弃
Alt+Z
草稿
Alt+B
导入
下载导入模板
附件
刷新
消息
```

### 表头字段（7）

*单据日期、*单据编号、*业务类型、入库类别、仓库、匹配来源单号、来料客户

### 明细列（18，含必需标记）

*仓库 | *存货名称 | 规格型号 | *计量单位 | *数量 | 智能选单 | 计量单位2 | 数量2 | 单价 | 金额 | 现存量 | 现存量说明 | 1 | 2 | 3 | 4 | 5 | 6

### 网格原始列（明细 tab）

可见：IsHasSameFuncAuth | Shorthand | Department_Name | IsSalesman | CreditDate | CreditQuantity | PositionTitle_Name | Memo | SettlementPartner_Name | Specification | IsMadeSelf | InventoryPriceDTOs_RetailPriceNew | InvoiceSpecification | InvoiceUnit_Name | AutoAddFigureNo | SKUCode | SKUName | SKUInvSCost | SKULatestCost | SKUAvagCost | Inventory_Code | Inventory_Specification | Inventory_Name | Version | Manufactureplant_Name | Warehouse_Name

全部（含隐藏）：
```
编码 [HIDDEN]
名称 [HIDDEN]
IsHasSameFuncAuth
Shorthand
Department_Name
IsSalesman
CreditDate
CreditQuantity
PositionTitle_Name
Memo
SettlementPartner_Name
Specification
IsMadeSelf
InventoryPriceDTOs_RetailPriceNew
InvoiceSpecification
InvoiceUnit_Name
AutoAddFigureNo
SKUCode
SKUName
SKUInvSCost
SKULatestCost
SKUAvagCost
Inventory_Code
Inventory_Specification
Inventory_Name
Version
Manufactureplant_Name
Warehouse_Name
详细 [HIDDEN]
 [HIDDEN]
 [HIDDEN]


























 [HIDDEN]
 [HIDDEN]
 [HIDDEN]


























 [HIDDEN]
```

### 表尾

```
1


2


3


4


5


6
```

---

## ST1021 销售出库单

### 工具栏分组（实测文本）

```
新增
新增
引入常用单据
设置默认功能
新增
引入常用单据
选单
选销售订单
设置默认功能
选销售订单
智能选单
保存
保存
Alt+S
保存新增
Alt+\
保存为草稿
保存为常用单据
保存打印
Alt+G
设置默认功能
保存
保存新增
保存为草稿
保存为常用单据
保存打印
删除
|
审核
|
生单
生成销售出库单(销售退货)
设置默认功能
生成销售出库单(销售退货)
|
变更
|
工具
现存量查询
变更历史
联查
销售订单情况
退库情况
出库情况
生单流程联查
设置
单据设置
移动控件位置
调整控件宽度
工具栏设置
智能选单设置
|
打印
直接打印
Alt+P
打印
Alt+;
预览
Alt+/
打印模板设置
Alt+,
导出
Alt+X
明细标签打印
打印情况
设置默认功能
直接打印
打印
预览
打印模板设置
导出
明细标签打印
打印情况
更多
复制
放弃
Alt+Z
草稿
Alt+B
导入
下载导入模板
附件
刷新
消息
```

### 表头字段（9）

*单据日期、*单据编号、*业务类型、退货原因、*客户、*结算客户、匹配来源单号、经手人、仓库

### 明细列（24，含必需标记）

*仓库 | *存货名称 | *存货编码 | 规格型号 | *计量单位 | *数量 | 智能选单 | 成本价 | 税率% | 售价 | 含税售价 | 销售金额 | 税额 | 含税销售金额 | 折扣金额 | 现存量 | 现存量说明 | 需求令号 | 退货原因 | 1 | 2 | 3 | 4 | 5

### 网格原始列（明细 tab）

可见：IsHasSameFuncAuth | SettlementPartner_Name | CardCode | MemberType_Name | Mobilephone | StoreType_Name | Person_Name | Warehouse_Name | Department_Name | Customer_Name | Shorthand | IsSalesman | CreditDate | CreditQuantity | PositionTitle_Name | Memo | LogisticsCampany_Name | contactperson | contactphone | province | city | districtarea | detailaddress | SendWarehouseDTOs_Warehouse_Name | SendInfoDTOs_SendPerson | SendInfoDTOs_SendMobilePhone | SendInfoDTOs_CarInfo | TruckType_Name | TruckNum | Specification | IsMadeSelf | InventoryPriceDTOs_RetailPriceNew | InvoiceSpecification | InvoiceUnit_Name | AutoAddFigureNo | SKUCode | SKUName | SKUInvSCost | SKULatestCost | SKUAvagCost | Inventory_Code | Inventory_Specification | Inventory_Name | Version | Manufactureplant_Name

全部（含隐藏）：
```
编码 [HIDDEN]
名称 [HIDDEN]
IsHasSameFuncAuth
SettlementPartner_Name
CardCode
MemberType_Name
Mobilephone
StoreType_Name
Person_Name
Warehouse_Name
Department_Name
Customer_Name
Shorthand
IsSalesman
CreditDate
CreditQuantity
PositionTitle_Name
Memo
LogisticsCampany_Name
contactperson
contactphone
province
city
districtarea
detailaddress
SendWarehouseDTOs_Warehouse_Name
SendInfoDTOs_SendPerson
SendInfoDTOs_SendMobilePhone
SendInfoDTOs_CarInfo
TruckType_Name
TruckNum
Specification
IsMadeSelf
InventoryPriceDTOs_RetailPriceNew
InvoiceSpecification
InvoiceUnit_Name
AutoAddFigureNo
SKUCode
SKUName
SKUInvSCost
SKULatestCost
SKUAvagCost
Inventory_Code
Inventory_Specification
Inventory_Name
Version
Manufactureplant_Name
详细 [HIDDEN]
 [HIDDEN]
 [HIDDEN]













































 [HIDDEN]
 [HIDDEN]
 [HIDDEN]













































 [HIDDEN]
```

### 表尾

```
1


2


3


4


5
```

---

## ST1022 材料出库单

### 工具栏分组（实测文本）

```
新增
新增
引入常用单据
设置默认功能
新增
引入常用单据
选单
选材料出库单（直接领料）
选材料出库单（自制领料）
选生产加工单
选生产加工单(新增材料)
设置默认功能
选材料出库单（直接领料）
选材料出库单（自制领料）
选生产加工单
选生产加工单(新增材料)
智能选单
选单转换
选材料出库单（自制领料）
选生产加工单
设置默认功能
选材料出库单（自制领料）
选生产加工单
保存
保存
Alt+S
保存新增
Alt+\
保存为草稿
保存为常用单据
保存打印
Alt+G
设置默认功能
保存
保存新增
保存为草稿
保存为常用单据
保存打印
删除
|
审核
|
生单
生成材料出库单（直接退料）
生成材料出库单（自制退料）
设置默认功能
生成材料出库单（直接退料）
生成材料出库单（自制退料）
|
变更
|
工具
现存量查询
变更历史
联查
生产加工情况
退料情况
生单流程联查
设置
单据设置
移动控件位置
调整控件宽度
工具栏设置
|
打印
直接打印
Alt+P
打印
Alt+;
预览
Alt+/
打印模板设置
Alt+,
导出
Alt+X
明细标签打印
打印情况
设置默认功能
直接打印
打印
预览
打印模板设置
导出
明细标签打印
打印情况
更多
复制
放弃
Alt+Z
草稿
Alt+B
导入
下载导入模板
附件
刷新
消息
```

### 表头字段（10）

*单据日期、*单据编号、*业务类型、出库类别、生产车间、领用人、仓库、来源单据、销售订单号、匹配来源单号

### 明细列（17，含必需标记）

*仓库 | 加工单号 | *材料名称 | *计量单位 | *数量 | 单价 | 金额 | 规格型号 | 手工确定成本 | 明细备注 | 现存量 | 现存量说明 | 1 | 2 | 3 | 4 | 5

### 网格原始列（明细 tab）

可见：IsHasSameFuncAuth | Shorthand | Department_Name | IsSalesman | CreditDate | CreditQuantity | PositionTitle_Name | Memo | Inventory_Code | Inventory_Specification | Inventory_Name | Version | Manufactureplant_Name | Warehouse_Name | Specification | IsMadeSelf | InventoryPriceDTOs_RetailPriceNew | InvoiceSpecification | InvoiceUnit_Name | AutoAddFigureNo | SKUCode | SKUName | SKUInvSCost | SKULatestCost | SKUAvagCost

全部（含隐藏）：
```
编码 [HIDDEN]
名称 [HIDDEN]
IsHasSameFuncAuth
Shorthand
Department_Name
IsSalesman
CreditDate
CreditQuantity
PositionTitle_Name
Memo
Inventory_Code
Inventory_Specification
Inventory_Name
Version
Manufactureplant_Name
Warehouse_Name
Specification
IsMadeSelf
InventoryPriceDTOs_RetailPriceNew
InvoiceSpecification
InvoiceUnit_Name
AutoAddFigureNo
SKUCode
SKUName
SKUInvSCost
SKULatestCost
SKUAvagCost
详细 [HIDDEN]
 [HIDDEN]
 [HIDDEN]

























 [HIDDEN]
 [HIDDEN]
 [HIDDEN]

























 [HIDDEN]
```

### 表尾

```
1


2


3


4


5
```

---

## ST1024 其他出库单

### 工具栏分组（实测文本）

```
新增
新增
引入常用单据
设置默认功能
新增
引入常用单据
保存
保存
Alt+S
保存新增
Alt+\
保存为草稿
保存为常用单据
保存打印
Alt+G
设置默认功能
保存
保存新增
保存为草稿
保存为常用单据
保存打印
删除
|
审核
|
转换
转换成其他入库单
设置默认功能
转换成其他入库单
|
变更
|
工具
现存量查询
变更历史
联查
其他入库情况
联查设备投放单
生单流程联查
设置
单据设置
移动控件位置
调整控件宽度
工具栏设置
|
打印
直接打印
Alt+P
打印
Alt+;
预览
Alt+/
打印模板设置
Alt+,
导出
Alt+X
明细标签打印
打印情况
设置默认功能
直接打印
打印
预览
打印模板设置
导出
明细标签打印
打印情况
更多
复制
放弃
Alt+Z
草稿
Alt+B
导入
下载导入模板
附件
刷新
消息
```

### 表头字段（5）

*单据日期、*单据编号、*业务类型、仓库、来料客户

### 明细列（16，含必需标记）

*仓库 | *存货名称 | 规格型号 | *计量单位 | *数量 | 单价 | 金额 | 现存量 | 现存量说明 | 1 | 2 | 3 | 4 | 5 | 6 | 7

### 网格原始列（明细 tab）

可见：IsHasSameFuncAuth | Shorthand | Department_Name | IsSalesman | CreditDate | CreditQuantity | PositionTitle_Name | Memo | SettlementPartner_Name | Specification | IsMadeSelf | InventoryPriceDTOs_RetailPriceNew | InvoiceSpecification | InvoiceUnit_Name | AutoAddFigureNo | SKUCode | SKUName | SKUInvSCost | SKULatestCost | SKUAvagCost | Inventory_Code | Inventory_Specification | Inventory_Name | Version | Manufactureplant_Name | Warehouse_Name

全部（含隐藏）：
```
编码 [HIDDEN]
名称 [HIDDEN]
IsHasSameFuncAuth
Shorthand
Department_Name
IsSalesman
CreditDate
CreditQuantity
PositionTitle_Name
Memo
SettlementPartner_Name
Specification
IsMadeSelf
InventoryPriceDTOs_RetailPriceNew
InvoiceSpecification
InvoiceUnit_Name
AutoAddFigureNo
SKUCode
SKUName
SKUInvSCost
SKULatestCost
SKUAvagCost
Inventory_Code
Inventory_Specification
Inventory_Name
Version
Manufactureplant_Name
Warehouse_Name
详细 [HIDDEN]
 [HIDDEN]
 [HIDDEN]


























 [HIDDEN]
 [HIDDEN]
 [HIDDEN]


























 [HIDDEN]
```

### 表尾

```

	备注
制单人
代理人
修改人
审核机器人审核人
审核日期
		
	
审核时间
		
	
打印次数
是否手工修改过单据号VoucherType
		
	
出入库方向是否自动生成创建时间
		
	


```

---

