package com.mes.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import java.time.LocalDateTime;

@TableName("panel_config")
public class PanelConfig {

    @TableId(type = IdType.AUTO)
    private Long id;
    private String panelCode;
    private String panelName;
    private String category;
    /** 所属业务模块分组（prod/sales/.../other），组织架构权限矩阵分组用 */
    private String moduleGroup;
    private String config;
    private String version;
    private LocalDateTime createTime;
    private LocalDateTime updateTime;

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }
    public String getPanelCode() { return panelCode; }
    public void setPanelCode(String panelCode) { this.panelCode = panelCode; }
    public String getPanelName() { return panelName; }
    public void setPanelName(String panelName) { this.panelName = panelName; }
    public String getCategory() { return category; }
    public void setCategory(String category) { this.category = category; }
    public String getModuleGroup() { return moduleGroup; }
    public void setModuleGroup(String moduleGroup) { this.moduleGroup = moduleGroup; }
    public String getConfig() { return config; }
    public void setConfig(String config) { this.config = config; }
    public String getVersion() { return version; }
    public void setVersion(String version) { this.version = version; }
    public LocalDateTime getCreateTime() { return createTime; }
    public void setCreateTime(LocalDateTime createTime) { this.createTime = createTime; }
    public LocalDateTime getUpdateTime() { return updateTime; }
    public void setUpdateTime(LocalDateTime updateTime) { this.updateTime = updateTime; }
}
