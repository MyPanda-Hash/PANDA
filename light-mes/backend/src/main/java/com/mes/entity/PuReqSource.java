package com.mes.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import java.math.BigDecimal;
import java.time.LocalDateTime;

/** 采购需求来源快照：计划分析的缺口行 + 手工补录需求。 */
@TableName("pu_req_source")
public class PuReqSource {

    @TableId(type = IdType.AUTO)
    private Long id;
    private String planNo;
    private String soNo;
    private String customer;
    private String dueDate;
    private String materialCode;
    private String materialName;
    private String spec;
    private String unit;
    private Integer level;
    private String path;
    private BigDecimal grossQty;
    private BigDecimal stockQty;
    private BigDecimal netQty;
    private String supplyType;
    private String sourceType;
    private String supplier;
    private String remark;
    private String status;
    private String createdBy;
    private LocalDateTime createTime;
    private LocalDateTime updateTime;

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }
    public String getPlanNo() { return planNo; }
    public void setPlanNo(String planNo) { this.planNo = planNo; }
    public String getSoNo() { return soNo; }
    public void setSoNo(String soNo) { this.soNo = soNo; }
    public String getCustomer() { return customer; }
    public void setCustomer(String customer) { this.customer = customer; }
    public String getDueDate() { return dueDate; }
    public void setDueDate(String dueDate) { this.dueDate = dueDate; }
    public String getMaterialCode() { return materialCode; }
    public void setMaterialCode(String materialCode) { this.materialCode = materialCode; }
    public String getMaterialName() { return materialName; }
    public void setMaterialName(String materialName) { this.materialName = materialName; }
    public String getSpec() { return spec; }
    public void setSpec(String spec) { this.spec = spec; }
    public String getUnit() { return unit; }
    public void setUnit(String unit) { this.unit = unit; }
    public Integer getLevel() { return level; }
    public void setLevel(Integer level) { this.level = level; }
    public String getPath() { return path; }
    public void setPath(String path) { this.path = path; }
    public BigDecimal getGrossQty() { return grossQty; }
    public void setGrossQty(BigDecimal grossQty) { this.grossQty = grossQty; }
    public BigDecimal getStockQty() { return stockQty; }
    public void setStockQty(BigDecimal stockQty) { this.stockQty = stockQty; }
    public BigDecimal getNetQty() { return netQty; }
    public void setNetQty(BigDecimal netQty) { this.netQty = netQty; }
    public String getSupplyType() { return supplyType; }
    public void setSupplyType(String supplyType) { this.supplyType = supplyType; }
    public String getSourceType() { return sourceType; }
    public void setSourceType(String sourceType) { this.sourceType = sourceType; }
    public String getSupplier() { return supplier; }
    public void setSupplier(String supplier) { this.supplier = supplier; }
    public String getRemark() { return remark; }
    public void setRemark(String remark) { this.remark = remark; }
    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }
    public String getCreatedBy() { return createdBy; }
    public void setCreatedBy(String createdBy) { this.createdBy = createdBy; }
    public LocalDateTime getCreateTime() { return createTime; }
    public void setCreateTime(LocalDateTime createTime) { this.createTime = createTime; }
    public LocalDateTime getUpdateTime() { return updateTime; }
    public void setUpdateTime(LocalDateTime updateTime) { this.updateTime = updateTime; }
}
