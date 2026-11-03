package com.mes.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

@TableName("sys_role_panel")
public class SysRolePanel {
    @TableId(type = IdType.AUTO)
    private Long id;
    private Long roleId;
    private String panelCode;
    /** 逗号分隔权限码（view,query,add,...11 项）；NULL 为未迁移存量行，读取时按 can_approve 回退 */
    private String perms;
    private Integer canApprove;

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }
    public Long getRoleId() { return roleId; }
    public void setRoleId(Long roleId) { this.roleId = roleId; }
    public String getPanelCode() { return panelCode; }
    public void setPanelCode(String panelCode) { this.panelCode = panelCode; }
    public String getPerms() { return perms; }
    public void setPerms(String perms) { this.perms = perms; }
    public Integer getCanApprove() { return canApprove; }
    public void setCanApprove(Integer canApprove) { this.canApprove = canApprove; }
}