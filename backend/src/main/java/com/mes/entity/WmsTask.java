package com.mes.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.LocalDateTime;

/**
 * 仓内作业任务：把「已审核的出入库单」拆成现场可执行的作业行。
 *
 * <p>单据是计划层（要入/出什么），任务才是执行层（谁、在哪个库位、实际做了多少）。
 * 拣货（PICK）来自出库单，上架（PUTAWAY）来自入库单；一张单可以拆成多行任务，
 * 同一张单的行任务按「类型 + 来源面板 + 来源单号 + 行号」唯一，重复同步不会造重复任务。
 */
@TableName("wms_task")
public class WmsTask {

    @TableId(type = IdType.AUTO)
    private Long id;
    /** PICK 拣货 / PUTAWAY 上架 */
    private String taskType;
    /** 波次号：同一仓库的待拣任务归到一个波次，空串表示未编波 */
    private String waveNo;
    private String sourcePanel;
    private String sourceNo;
    private Integer rowNo;
    private String itemCode;
    private String itemName;
    private String spec;
    private String unit;
    private String warehouse;
    /** 拣货 = 来源库位；上架 = 收货暂存位 */
    private String fromLocation;
    /** 上架目标货位（上架确认时填） */
    private String toLocation;
    private BigDecimal planQty;
    private BigDecimal doneQty;
    /** 待作业 / 部分完成 / 已完成 */
    private String status;
    private String operator;
    /** 复核结果：空 / 已复核 / 有差异 */
    private String reviewStatus;
    private String reviewRemark;
    private LocalDate bizDate;
    private LocalDateTime createTime;
    private LocalDateTime doneTime;

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }
    public String getTaskType() { return taskType; }
    public void setTaskType(String taskType) { this.taskType = taskType; }
    public String getWaveNo() { return waveNo; }
    public void setWaveNo(String waveNo) { this.waveNo = waveNo; }
    public String getSourcePanel() { return sourcePanel; }
    public void setSourcePanel(String sourcePanel) { this.sourcePanel = sourcePanel; }
    public String getSourceNo() { return sourceNo; }
    public void setSourceNo(String sourceNo) { this.sourceNo = sourceNo; }
    public Integer getRowNo() { return rowNo; }
    public void setRowNo(Integer rowNo) { this.rowNo = rowNo; }
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
    public String getFromLocation() { return fromLocation; }
    public void setFromLocation(String fromLocation) { this.fromLocation = fromLocation; }
    public String getToLocation() { return toLocation; }
    public void setToLocation(String toLocation) { this.toLocation = toLocation; }
    public BigDecimal getPlanQty() { return planQty; }
    public void setPlanQty(BigDecimal planQty) { this.planQty = planQty; }
    public BigDecimal getDoneQty() { return doneQty; }
    public void setDoneQty(BigDecimal doneQty) { this.doneQty = doneQty; }
    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }
    public String getOperator() { return operator; }
    public void setOperator(String operator) { this.operator = operator; }
    public String getReviewStatus() { return reviewStatus; }
    public void setReviewStatus(String reviewStatus) { this.reviewStatus = reviewStatus; }
    public String getReviewRemark() { return reviewRemark; }
    public void setReviewRemark(String reviewRemark) { this.reviewRemark = reviewRemark; }
    public LocalDate getBizDate() { return bizDate; }
    public void setBizDate(LocalDate bizDate) { this.bizDate = bizDate; }
    public LocalDateTime getCreateTime() { return createTime; }
    public void setCreateTime(LocalDateTime createTime) { this.createTime = createTime; }
    public LocalDateTime getDoneTime() { return doneTime; }
    public void setDoneTime(LocalDateTime doneTime) { this.doneTime = doneTime; }
}
