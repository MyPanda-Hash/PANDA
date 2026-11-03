package com.mes.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import java.time.LocalDateTime;

@TableName("form_data")
public class FormData {

    @TableId(type = IdType.AUTO)
    private Long id;
    private String panelCode;
    private String formNo;
    private String data;
    private String detailData;
    private String status;
    private String createBy;
    private LocalDateTime createTime;
    private LocalDateTime updateTime;
    private String auditBy;
    private LocalDateTime auditTime;
    private LocalDateTime closeTime;
    /** 单据版本号，初始 1；变更单在父单基础上递增。 */
    private Integer version;
    /** 父单号：变更单指向被变更的原单，普通单据为空。 */
    private String parentNo;
    /** 是否当前生效版本：1 生效，0 已被变更单取代。 */
    private Boolean currentVersion;

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }
    public String getPanelCode() { return panelCode; }
    public void setPanelCode(String panelCode) { this.panelCode = panelCode; }
    public String getFormNo() { return formNo; }
    public void setFormNo(String formNo) { this.formNo = formNo; }
    public String getData() { return data; }
    public void setData(String data) { this.data = data; }
    public String getDetailData() { return detailData; }
    public void setDetailData(String detailData) { this.detailData = detailData; }
    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }
    public String getCreateBy() { return createBy; }
    public void setCreateBy(String createBy) { this.createBy = createBy; }
    public LocalDateTime getCreateTime() { return createTime; }
    public void setCreateTime(LocalDateTime createTime) { this.createTime = createTime; }
    public LocalDateTime getUpdateTime() { return updateTime; }
    public void setUpdateTime(LocalDateTime updateTime) { this.updateTime = updateTime; }
    public String getAuditBy() { return auditBy; }
    public void setAuditBy(String auditBy) { this.auditBy = auditBy; }
    public LocalDateTime getAuditTime() { return auditTime; }
    public void setAuditTime(LocalDateTime auditTime) { this.auditTime = auditTime; }
    public LocalDateTime getCloseTime() { return closeTime; }
    public void setCloseTime(LocalDateTime closeTime) { this.closeTime = closeTime; }
    public Integer getVersion() { return version; }
    public void setVersion(Integer version) { this.version = version; }
    public String getParentNo() { return parentNo; }
    public void setParentNo(String parentNo) { this.parentNo = parentNo; }
    public Boolean getCurrentVersion() { return currentVersion; }
    public void setCurrentVersion(Boolean currentVersion) { this.currentVersion = currentVersion; }
}
