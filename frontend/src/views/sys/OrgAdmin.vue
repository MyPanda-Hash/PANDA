<!-- OrgAdmin.vue — 组织架构（仅管理员）：用户管理（分配角色）+ 角色管理（创建角色、勾选可见面板、审批权限） -->
<template>
  <div class="org-wrap">
    <div class="org-col users">
      <div class="col-head">
        <span class="col-title">用户（组织调整）</span>
        <el-button type="primary" size="small" @click="openUser()">新增用户</el-button>
      </div>
      <el-table :data="users" size="small" border height="600" highlight-current-row @row-click="openUser">
        <el-table-column prop="userName" label="账号" width="110" />
        <el-table-column prop="realName" label="姓名" min-width="90" />
        <el-table-column label="角色" min-width="110">
          <template #default="{ row }">{{ row.roleName || '-' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="70" align="center">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'success' : 'info'" size="small">{{ row.enabled ? '启用' : '停用' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="60" align="center">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click.stop="openUser(row)">编辑</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="col-tip">点击用户行可分配角色 / 启停用</div>
    </div>

    <div class="org-col roles">
      <div class="col-head">
        <span class="col-title">角色与面板权限</span>
        <el-button type="primary" size="small" @click="newRoleVisible = true">创建角色</el-button>
      </div>
      <el-table :data="roles" size="small" border height="220" highlight-current-row @current-change="onRoleSelect">
        <el-table-column prop="roleName" label="角色名称" min-width="110" />
        <el-table-column prop="roleCode" label="编码" width="110" />
        <el-table-column label="类型" width="70" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.isAdmin" type="danger" size="small">超级</el-tag>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="60" align="center">
          <template #default="{ row }">
            <el-button v-if="!row.isAdmin" size="small" link type="danger" @click.stop="delRole(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div v-if="selRole" class="perm-box">
        <div class="perm-head">
          「{{ selRole.roleName }}」可见面板
          <span class="perm-sub">（提交审批 / 查看审批历史为公开权限；勾选「审批权限」才能执行审批通过/驳回）</span>
        </div>
        <div v-if="selRole.isAdmin" class="admin-tip">管理员为超级权限：默认可见全部面板并拥有全部审批，无需配置。</div>
        <template v-else>
          <el-table :data="panelRows" size="small" border max-height="260">
            <el-table-column label="面板" prop="panelName" min-width="150" />
            <el-table-column label="可见" width="64" align="center">
              <template #default="{ row }"><el-checkbox v-model="row.checked" /></template>
            </el-table-column>
            <el-table-column label="审批权限" width="90" align="center">
              <template #default="{ row }">
                <el-checkbox v-if="row.hasApproval" v-model="row.canApprove" :disabled="!row.checked" />
                <span v-else>-</span>
              </template>
            </el-table-column>
          </el-table>
          <div class="perm-actions">
            <el-button type="primary" size="small" :loading="saving" @click="savePanels">保存面板权限</el-button>
            <el-button size="small" @click="loadRolePanels(selRole)">刷新</el-button>
          </div>
        </template>
      </div>
    </div>

    <!-- 新增/编辑用户 -->
    <el-dialog v-model="userVisible" :title="editingUser ? '编辑用户：' + editingUser.userName : '新增用户'" width="420px" append-to-body>
      <el-form label-width="80px">
        <el-form-item label="账号" required>
          <el-input v-model="userForm.userName" :disabled="!!editingUser" placeholder="登录账号" />
        </el-form-item>
        <el-form-item label="姓名">
          <el-input v-model="userForm.realName" placeholder="真实姓名" />
        </el-form-item>
        <el-form-item v-if="!editingUser" label="密码">
          <el-input v-model="userForm.password" type="password" placeholder="默认 123456" />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="userForm.roleId" placeholder="选择角色" clearable style="width: 100%">
            <el-option v-for="r in roles" :key="r.id" :label="r.roleName" :value="r.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="userForm.enabled" :active-value="1" :inactive-value="0" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="userVisible = false">取消</el-button>
        <el-button type="primary" :loading="savingUser" @click="saveUser">保存</el-button>
      </template>
    </el-dialog>

    <!-- 创建角色 -->
    <el-dialog v-model="newRoleVisible" title="创建角色" width="400px" append-to-body>
      <el-form label-width="80px">
        <el-form-item label="角色编码" required>
          <el-input v-model="roleForm.roleCode" placeholder="如 operator / workshop" />
        </el-form-item>
        <el-form-item label="角色名称" required>
          <el-input v-model="roleForm.roleName" placeholder="如 车间操作员" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="roleForm.remark" placeholder="说明该角色的职责范围" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="newRoleVisible = false">取消</el-button>
        <el-button type="primary" :loading="savingRole" @click="saveRole">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import request from '@core/request'
import { useUserStore } from '@/stores/user'

const user = useUserStore()

const users = ref([])
const roles = ref([])
const selRole = ref(null)
const panelRows = ref([])
const saving = ref(false)
const savingUser = ref(false)
const savingRole = ref(false)

const userVisible = ref(false)
const editingUser = ref(null)
const userForm = reactive({ userName: '', realName: '', password: '', roleId: null, enabled: 1 })
const newRoleVisible = ref(false)
const roleForm = reactive({ roleCode: '', roleName: '', remark: '' })

async function load() {
  await Promise.all([loadUsers(), loadRoles()])
}

async function loadUsers() {
  try {
    const r = await request.get('/sys/user/list')
    users.value = r?.data || []
  } catch (e) {
    ElMessage.error('用户列表加载失败')
  }
}

async function loadRoles() {
  try {
    const r = await request.get('/sys/role/list')
    roles.value = r?.data || []
  } catch (e) {
    ElMessage.error('角色列表加载失败')
  }
}

function openUser(row) {
  editingUser.value = row || null
  userForm.userName = row?.userName || ''
  userForm.realName = row?.realName || ''
  userForm.password = ''
  userForm.roleId = row?.roleId ?? null
  userForm.enabled = row?.enabled ?? 1
  userVisible.value = true
}

async function saveUser() {
  if (!userForm.userName.trim()) return ElMessage.warning('请输入账号')
  savingUser.value = true
  try {
    const body = { ...userForm }
    if (editingUser.value) body.id = editingUser.value.id
    await request.post('/sys/user/save', body)
    ElMessage.success('用户已保存')
    userVisible.value = false
    await loadUsers()
  } catch (e) {
    ElMessage.error(e?.response?.data?.message || '保存失败')
  } finally {
    savingUser.value = false
  }
}

async function saveRole() {
  if (!roleForm.roleCode.trim() || !roleForm.roleName.trim()) return ElMessage.warning('请填写编码与名称')
  savingRole.value = true
  try {
    await request.post('/sys/role/save', { ...roleForm })
    ElMessage.success('角色已创建')
    newRoleVisible.value = false
    roleForm.roleCode = ''
    roleForm.roleName = ''
    roleForm.remark = ''
    await loadRoles()
  } catch (e) {
    ElMessage.error(e?.response?.data?.message || '创建失败')
  } finally {
    savingRole.value = false
  }
}

async function delRole(row) {
  try {
    await ElMessageBox.confirm('删除角色「' + row.roleName + '」？其下用户角色将清空', '提示', { type: 'warning' })
  } catch (e) {
    return
  }
  try {
    await request.delete('/sys/role/' + row.id)
    ElMessage.success('角色已删除')
    if (selRole.value && selRole.value.id === row.id) {
      selRole.value = null
      panelRows.value = []
    }
    await loadRoles()
  } catch (e) {
    ElMessage.error(e?.response?.data?.message || '删除失败')
  }
}

async function onRoleSelect(row) {
  selRole.value = row
  if (row && !row.isAdmin) await loadRolePanels(row)
}

async function loadRolePanels(row) {
  try {
    const r = await request.get('/sys/role/' + row.id + '/panels')
    const d = r?.data || {}
    const all = d.allPanels || []
    const granted = d.granted || []
    const grantedMap = {}
    for (const g of granted) grantedMap[g.panelCode] = !!g.canApprove
    panelRows.value = all.map((p) => ({
      panelCode: p.panelCode,
      panelName: p.panelName,
      hasApproval: !!p.hasApproval,
      checked: !!grantedMap[p.panelCode],
      canApprove: !!grantedMap[p.panelCode],
    }))
  } catch (e) {
    ElMessage.error('面板权限加载失败')
  }
}

async function savePanels() {
  saving.value = true
  try {
    const panels = panelRows.value
      .filter((p) => p.checked)
      .map((p) => ({ panelCode: p.panelCode, canApprove: p.canApprove }))
    await request.post('/sys/role/' + selRole.value.id + '/panels', { panels })
    ElMessage.success('面板权限已保存')
    if (selRole.value.roleCode === user.roleCode) await user.fetchPerms()
  } catch (e) {
    ElMessage.error(e?.response?.data?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.org-wrap {
  display: flex;
  gap: 14px;
  padding: 14px;
  height: 100%;
  box-sizing: border-box;
}
.org-col {
  background: #fff;
  border: 1px solid #e3e8ef;
  border-radius: 6px;
  padding: 12px;
  display: flex;
  flex-direction: column;
}
.org-col.users { flex: 1; }
.org-col.roles { flex: 1.4; }
.col-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}
.col-title { font-size: 14px; font-weight: 600; color: #1c4f8a; }
.col-tip { margin-top: 8px; font-size: 12px; color: #999; }
.perm-box { margin-top: 12px; border-top: 1px dashed #d0d7e3; padding-top: 10px; }
.perm-head { font-size: 13px; font-weight: 600; color: #333; margin-bottom: 8px; }
.perm-sub { font-weight: 400; color: #888; font-size: 12px; }
.admin-tip { color: #c0392b; font-size: 12px; padding: 8px 0; }
.perm-actions { margin-top: 10px; display: flex; gap: 8px; }
</style>