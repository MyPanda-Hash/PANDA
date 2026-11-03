package com.mes.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import java.math.BigDecimal;
import java.time.LocalDateTime;

/**
 * 库存余额：四维（存货 + 仓库 + 库位 + 批次）。
 * 单厂自用，不引入组织维度；结构化业务表按项目规则预留 30 个 ext_* 扩展列。
 */
@TableName("stock_balance")
public class StockBalance {

    @TableId(type = IdType.AUTO)
    private Long id;
    private String itemCode;
    private String itemName;
    private String spec;
    private String unit;
    private String warehouse;
    private String location;
    private String batchNo;
    private BigDecimal quantity;
    private BigDecimal lockedQuantity;
    private BigDecimal amount;
    private LocalDateTime updateTime;

    private String extStr1;
    private String extStr2;
    private String extStr3;
    private String extStr4;
    private String extStr5;
    private String extStr6;
    private String extStr7;
    private String extStr8;
    private String extStr9;
    private String extStr10;
    private BigDecimal extNum1;
    private BigDecimal extNum2;
    private BigDecimal extNum3;
    private BigDecimal extNum4;
    private BigDecimal extNum5;
    private BigDecimal extNum6;
    private BigDecimal extNum7;
    private BigDecimal extNum8;
    private BigDecimal extNum9;
    private BigDecimal extNum10;
    private LocalDateTime extDate1;
    private LocalDateTime extDate2;
    private LocalDateTime extDate3;
    private LocalDateTime extDate4;
    private LocalDateTime extDate5;
    private Boolean extFlag1;
    private Boolean extFlag2;
    private Boolean extFlag3;
    private Boolean extFlag4;
    private Boolean extFlag5;

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }
    public String getItemCode() { return itemCode; }
    public void setItemCode(String itemCode) { this.itemCode = itemCode; }
    public String getItemName() { return itemName; }
    public void setItemName(String itemName) { this.itemName = itemName; }
    public String getSpec() { return spec; }
    public void setSpec(String spec) { this.spec = spec; }
    public String getUnit() { return unit; }
    public void setUnit(String unit) { this.unit = unit; }
    public String getWarehouse() { return warehouse; }
    public void setWarehouse(String warehouse) { this.warehouse = warehouse; }
    public String getLocation() { return location; }
    public void setLocation(String location) { this.location = location; }
    public String getBatchNo() { return batchNo; }
    public void setBatchNo(String batchNo) { this.batchNo = batchNo; }
    public BigDecimal getQuantity() { return quantity; }
    public void setQuantity(BigDecimal quantity) { this.quantity = quantity; }
    public BigDecimal getLockedQuantity() { return lockedQuantity; }
    public void setLockedQuantity(BigDecimal lockedQuantity) { this.lockedQuantity = lockedQuantity; }
    public BigDecimal getAmount() { return amount; }
    public void setAmount(BigDecimal amount) { this.amount = amount; }
    public LocalDateTime getUpdateTime() { return updateTime; }
    public void setUpdateTime(LocalDateTime updateTime) { this.updateTime = updateTime; }
    public String getExtStr1() { return extStr1; }
    public void setExtStr1(String extStr1) { this.extStr1 = extStr1; }
    public String getExtStr2() { return extStr2; }
    public void setExtStr2(String extStr2) { this.extStr2 = extStr2; }
    public String getExtStr3() { return extStr3; }
    public void setExtStr3(String extStr3) { this.extStr3 = extStr3; }
    public String getExtStr4() { return extStr4; }
    public void setExtStr4(String extStr4) { this.extStr4 = extStr4; }
    public String getExtStr5() { return extStr5; }
    public void setExtStr5(String extStr5) { this.extStr5 = extStr5; }
    public String getExtStr6() { return extStr6; }
    public void setExtStr6(String extStr6) { this.extStr6 = extStr6; }
    public String getExtStr7() { return extStr7; }
    public void setExtStr7(String extStr7) { this.extStr7 = extStr7; }
    public String getExtStr8() { return extStr8; }
    public void setExtStr8(String extStr8) { this.extStr8 = extStr8; }
    public String getExtStr9() { return extStr9; }
    public void setExtStr9(String extStr9) { this.extStr9 = extStr9; }
    public String getExtStr10() { return extStr10; }
    public void setExtStr10(String extStr10) { this.extStr10 = extStr10; }
    public BigDecimal getExtNum1() { return extNum1; }
    public void setExtNum1(BigDecimal extNum1) { this.extNum1 = extNum1; }
    public BigDecimal getExtNum2() { return extNum2; }
    public void setExtNum2(BigDecimal extNum2) { this.extNum2 = extNum2; }
    public BigDecimal getExtNum3() { return extNum3; }
    public void setExtNum3(BigDecimal extNum3) { this.extNum3 = extNum3; }
    public BigDecimal getExtNum4() { return extNum4; }
    public void setExtNum4(BigDecimal extNum4) { this.extNum4 = extNum4; }
    public BigDecimal getExtNum5() { return extNum5; }
    public void setExtNum5(BigDecimal extNum5) { this.extNum5 = extNum5; }
    public BigDecimal getExtNum6() { return extNum6; }
    public void setExtNum6(BigDecimal extNum6) { this.extNum6 = extNum6; }
    public BigDecimal getExtNum7() { return extNum7; }
    public void setExtNum7(BigDecimal extNum7) { this.extNum7 = extNum7; }
    public BigDecimal getExtNum8() { return extNum8; }
    public void setExtNum8(BigDecimal extNum8) { this.extNum8 = extNum8; }
    public BigDecimal getExtNum9() { return extNum9; }
    public void setExtNum9(BigDecimal extNum9) { this.extNum9 = extNum9; }
    public BigDecimal getExtNum10() { return extNum10; }
    public void setExtNum10(BigDecimal extNum10) { this.extNum10 = extNum10; }
    public LocalDateTime getExtDate1() { return extDate1; }
    public void setExtDate1(LocalDateTime extDate1) { this.extDate1 = extDate1; }
    public LocalDateTime getExtDate2() { return extDate2; }
    public void setExtDate2(LocalDateTime extDate2) { this.extDate2 = extDate2; }
    public LocalDateTime getExtDate3() { return extDate3; }
    public void setExtDate3(LocalDateTime extDate3) { this.extDate3 = extDate3; }
    public LocalDateTime getExtDate4() { return extDate4; }
    public void setExtDate4(LocalDateTime extDate4) { this.extDate4 = extDate4; }
    public LocalDateTime getExtDate5() { return extDate5; }
    public void setExtDate5(LocalDateTime extDate5) { this.extDate5 = extDate5; }
    public Boolean getExtFlag1() { return extFlag1; }
    public void setExtFlag1(Boolean extFlag1) { this.extFlag1 = extFlag1; }
    public Boolean getExtFlag2() { return extFlag2; }
    public void setExtFlag2(Boolean extFlag2) { this.extFlag2 = extFlag2; }
    public Boolean getExtFlag3() { return extFlag3; }
    public void setExtFlag3(Boolean extFlag3) { this.extFlag3 = extFlag3; }
    public Boolean getExtFlag4() { return extFlag4; }
    public void setExtFlag4(Boolean extFlag4) { this.extFlag4 = extFlag4; }
    public Boolean getExtFlag5() { return extFlag5; }
    public void setExtFlag5(Boolean extFlag5) { this.extFlag5 = extFlag5; }
}
