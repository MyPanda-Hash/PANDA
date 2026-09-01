<!-- OrgAdmin.vue — 组织架构（仅管理员）：部门树 + 用户管理（分配角色/部门）+ 角色管理（面板勾选、审批权限） -->
<template>
  <div class="org-wrap">
    <!-- 左：部门 -->
    <div class="org-col dept">
      <div class="col-head">
        <span class="col-title">{{ tt('部门') }}</span>
        <el-button type="primary" size="small" @click="newDept(0)">{{ tt('新增部门') }}</el-button>
      </div>
      <el-tree
        class="dept-tree"
        :data="deptTree"
        node-key="id"
        :props="{ label: 'deptName', children: 'children' }"
        highlight-current
        :expand-on-click-node="false"
        @node-click="onDeptClick"
      >
        <template #default="{ data }">
          <div class="dept-node">
            <span>{{ data.deptName }}</span>
            <span class="dept-ops" @click.stop>
              <el-button size="small" link type="primary" @click="newDept(data.id)">{{ tt('+子') }}</el-button>
              <el-button size="small" link type="primary" @click="editDept(data)">{{ tt('改') }}</el-button>
              <el-button v-if="data.id !== 1" size="small" link type="danger" @click="delDept(data)">{{ tt('删') }}</el-button>
            </span>
          </div>
        </template>
      </el-tree>
      <div class="col-tip">{{ tt('支持多级部门；「+子」新增下级部门') }}</div>
    </div>

    <!-- 中：用户 -->
    <div class="org-col users">
      <div class="col-head">
        <span class="col-title">{{ tt('用户（组织调整）') }}</span>
        <el-button type="primary" size="small" @click="openUser()">{{ tt('新增用户') }}</el-button>
      </div>
      <el-table :data="users" size="small" border height="620" highlight-current-row @row-click="openUser">
        <el-table-column prop="userName" :label="tt('账号')" width="100" />
        <el-table-column prop="realName" :label="tt('姓名')" min-width="80" />
        <el-table-column :label="tt('部门')" min-width="110">
          <template #default="{ row }">{{ row.deptName || '-' }}</template>
        </el-table-column>
        <el-table-column :label="tt('角色')" min-width="100">
          <template #default="{ row }">{{ row.roleName || '-' }}</template>
        </el-table-column>
        <el-table-column :label="tt('状态')" width="64" align="center">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'success' : 'info'" size="small">{{ row.enabled ? tt('启用') : tt('停用') }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="tt('操作')" width="56" align="center">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click.stop="openUser(row)">{{ tt('编辑') }}</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="col-tip">{{ tt('点击用户行可分配部门 / 角色 / 启停用') }}</div>
    </div>

    <!-- 右：角色与面板权限 -->
    <div class="org-col roles">
      <div class="col-head">
        <span class="col-title">{{ tt('角色与面板权限') }}</span>
        <el-button type="primary" size="small" @click="newRoleVisible = true">{{ tt('创建角色') }}</el-button>
      </div>
      <el-table :data="roles" size="small" border height="200" highlight-current-row @current-change="onRoleSelect">
        <el-table-column prop="roleName" :label="tt('角色名称')" min-width="110" />
        <el-table-column prop="roleCode" :label="tt('编码')" width="100" />
        <el-table-column :label="tt('类型')" width="64" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.isAdmin" type="danger" size="small">{{ tt('超级') }}</el-tag>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column :label="tt('操作')" width="56" align="center">
          <template #default="{ row }">
            <el-button v-if="!row.isAdmin" size="small" link type="danger" @click.stop="delRole(row)">{{ tt('删除') }}</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div v-if="selRole" class="perm-box">
        <div class="perm-head">
          「{{ selRole.roleName }}」{{ tt('面板权限矩阵') }}
          <span class="perm-sub">{{ tt('（11 项权限；勾「查看」才有菜单入口，取消「查看」清空整行；审批列仅审批流面板可勾；复核/调整为预留位）') }}</span>
        </div>
        <div v-if="selRole.isAdmin" class="admin-tip">{{ tt('管理员为超级权限：默认全部面板全部权限，无需配置。') }}</div>
        <template v-else>
          <el-collapse v-model="openGroups" class="perm-collapse">
            <el-collapse-item v-for="g in groupedPanels" :key="g.code" :name="g.code">
              <template #title>
                <span class="g-title">{{ tt(g.name) }}</span>
                <span class="g-count">{{ g.panels.length }} {{ tt('个面板') }}</span>
                <span class="g-actions" @click.stop>
                  <el-button link size="small" type="primary" @click="setGroupAll(g, true)">{{ tt('全选') }}</el-button>
                  <el-button link size="small" @click="setGroupAll(g, false)">{{ tt('清空') }}</el-button>
                </span>
              </template>
              <el-table :data="g.panels" size="small" border class="perm-table">
                <el-table-column :label="tt('面板')" prop="panelName" min-width="130" fixed="left">
                  <template #default="{ row }">{{ tt(row.panelName) }}</template>
                </el-table-column>
                <el-table-column v-for="a in permActions" :key="a.code" width="56" align="center">
                  <template #header>
                    <el-checkbox
                      :model-value="groupColChecked(g, a.code)"
                      :indeterminate="groupColIndeterminate(g, a.code)"
                      :disabled="a.reserved || (a.code === 'audit' && !rowsForCol(g, a.code).length)"
                      @change="(v) => setGroupCol(g, a.code, v)"
                    >{{ tt(a.name) }}</el-checkbox>
                  </template>
                  <template #default="{ row }">
                    <el-tooltip v-if="a.reserved" :content="tt('预留权限位，当前版本暂无对应按钮')" placement="top">
                      <span class="perm-reserved"><el-checkbox :model-value="false" disabled /></span>
                    </el-tooltip>
                    <el-checkbox
                      v-else
                      :model-value="!!row.perms[a.code]"
                      :disabled="a.code === 'audit' && !row.hasApproval"
                      :title="a.code === 'audit' && !row.hasApproval ? tt('该面板无审批流') : ''"
                      @change="(v) => togglePerm(row, a.code, v)"
                    />
                  </template>
                </el-table-column>
              </el-table>
            </el-collapse-item>
          </el-collapse>
          <div class="perm-actions">
            <el-button type="primary" size="small" :loading="saving" @click="savePanels">{{ tt('保存权限矩阵') }}</el-button>
            <el-button size="small" @click="loadRolePanels(selRole)">{{ tt('刷新') }}</el-button>
          </div>
        </template>
      </div>
    </div>

    <!-- 新增/编辑部门 -->
    <el-dialog v-model="deptVisible" :title="editingDept ? tt('编辑部门') : tt('新增部门')" width="360px" append-to-body>
      <el-form label-width="80px">
        <el-form-item :label="tt('上级部门')">
          <el-tree-select v-model="deptForm.parentId" :data="deptSelectData" check-strictly clearable style="width: 100%" />
        </el-form-item>
        <el-form-item :label="tt('部门名称')" required>
          <el-input v-model="deptForm.deptName" :placeholder="tt('如 车间 / 质检部')" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="deptVisible = false">{{ tt('取消') }}</el-button>
        <el-button type="primary" :loading="savingDept" @click="saveDept">{{ tt('保存') }}</el-button>
      </template>
    </el-dialog>

    <!-- 新增/编辑用户 -->
    <el-dialog v-model="userVisible" :title="editingUser ? tt('编辑用户') + '：' + editingUser.userName : tt('新增用户')" width="420px" append-to-body>
      <el-form label-width="80px">
        <el-form-item :label="tt('账号')" required>
          <el-input v-model="userForm.userName" :disabled="!!editingUser" :placeholder="tt('登录账号')" />
        </el-form-item>
        <el-form-item :label="tt('姓名')">
          <el-input v-model="userForm.realName" :placeholder="tt('真实姓名')" />
        </el-form-item>
        <el-form-item v-if="!editingUser" :label="tt('密码')">
          <el-input v-model="userForm.password" type="password" :placeholder="tt('默认 123456')" />
        </el-form-item>
        <el-form-item :label="tt('部门')">
          <el-tree-select v-model="userForm.deptId" :data="deptSelectData" check-strictly clearable style="width: 100%" />
        </el-form-item>
        <el-form-item :label="tt('角色')">
          <el-select v-model="userForm.roleId" :placeholder="tt('选择角色')" clearable style="width: 100%">
            <el-option v-for="r in roles" :key="r.id" :label="r.roleName" :value="r.id" />
          </el-select>
        </el-form-item>
        <el-form-item :label="tt('启用')">
          <el-switch v-model="userForm.enabled" :active-value="1" :inactive-value="0" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="userVisible = false">{{ tt('取消') }}</el-button>
        <el-button type="primary" :loading="savingUser" @click="saveUser">{{ tt('保存') }}</el-button>
      </template>
    </el-dialog>

    <!-- 创建角色 -->
    <el-dialog v-model="newRoleVisible" :title="tt('创建角色')" width="400px" append-to-body>
      <el-form label-width="80px">
        <el-form-item :label="tt('角色编码')" required>
          <el-input v-model="roleForm.roleCode" :placeholder="tt('如 operator / workshop')" />
        </el-form-item>
        <el-form-item :label="tt('角色名称')" required>
          <el-input v-model="roleForm.roleName" :placeholder="tt('如 车间操作员')" />
        </el-form-item>
        <el-form-item :label="tt('备注')">
          <el-input v-model="roleForm.remark" :placeholder="tt('说明该角色的职责范围')" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="newRoleVisible = false">{{ tt('取消') }}</el-button>
        <el-button type="primary" :loading="savingRole" @click="saveRole">{{ tt('创建') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import request from '@core/request'
import { tt } from '@/i18n'
import { useUserStore } from '@/stores/user'
import { useLocaleStore } from '@/stores/locale'
import { PERM_ACTIONS } from '@/business/permissions'

const user = useUserStore()
const localeStore = useLocaleStore()

// 2026-09-01：面板模块分组与 11 项权限矩阵由后端提供（panel_config.module_group + sys_role_panel.perms），
// 前端不再硬编码分组常量；未迁移库的旧后端返回空时按「其他」单组兜底
const permActions = ref(PERM_ACTIONS)
const groupDefs = ref([{ code: 'other', name: '其他' }])
const openGroups = ref(['other'])

// 按模块分组渲染（行对象与 panelRows 同引用，勾选联动保存）；未映射/空分组归「其他」
const groupedPanels = computed(() => {
  const buckets = groupDefs.value.map((g) => ({ code: g.code, name: g.name, panels: [] }))
  const byCode = {}
  for (const b of buckets) byCode[b.code] = b
  if (!byCode.other) {
    const other = { code: 'other', name: '其他', panels: [] }
    byCode.other = other
    buckets.push(other)
  }
  for (const r of panelRows.value) {
    (byCode[r.moduleGroup] || byCode.other).panels.push(r)
  }
  return buckets
})

// 列可勾选行（审批列排除无审批流面板）
function rowsForCol(g, code) {
  if (code !== 'audit') return g.panels
  return g.panels.filter((r) => r.hasApproval)
}
function groupColChecked(g, code) {
  const rs = rowsForCol(g, code)
  return rs.length > 0 && rs.every((r) => r.perms[code])
}
function groupColIndeterminate(g, code) {
  const rs = rowsForCol(g, code)
  return !groupColChecked(g, code) && rs.some((r) => r.perms[code])
}
function setGroupCol(g, code, v) {
  for (const r of rowsForCol(g, code)) togglePerm(r, code, v)
}
function setGroupAll(g, v) {
  for (const r of g.panels) {
    if (!v) {
      r.perms = {}
      continue
    }
    for (const a of permActions.value) {
      if (a.reserved) continue
      if (a.code === 'audit' && !r.hasApproval) continue
      r.perms[a.code] = true
    }
  }
}
// 勾权限：勾任意权限自动带上「查看」；取消「查看」清空整行
function togglePerm(row, code, v) {
  if (code === 'view') {
    if (v) row.perms.view = true
    else row.perms = {}
    return
  }
  if (v) {
    row.perms.view = true
    row.perms[code] = true
  } else {
    row.perms[code] = false
  }
}

const deptTree = ref([])
const deptVisible = ref(false)
const editingDept = ref(null)
const savingDept = ref(false)
const deptForm = reactive({ id: null, parentId: 0, deptName: '' })

const users = ref([])
const roles = ref([])
const selRole = ref(null)
const panelRows = ref([])
const saving = ref(false)
const savingUser = ref(false)
const savingRole = ref(false)

const userVisible = ref(false)
const editingUser = ref(null)
const userForm = reactive({ userName: '', realName: '', password: '', deptId: null, roleId: null, enabled: 1 })
const newRoleVisible = ref(false)
const roleForm = reactive({ roleCode: '', roleName: '', remark: '' })

// el-tree-select 数据（value/label/children）
const deptSelectData = computed(() => toSelect(deptTree.value))
function toSelect(nodes) {
  return (nodes || []).map((n) => ({
    value: n.id,
    label: n.deptName,
    children: n.children && n.children.length ? toSelect(n.children) : undefined,
  }))
}

async function load() {
  await Promise.all([loadDepts(), loadUsers(), loadRoles()])
  ensurePageDict()
}

async function loadDepts() {
  try {
    const r = await request.get('/sys/dept/tree')
    deptTree.value = r?.data || []
  } catch (e) {
    ElMessage.error(tt('部门加载失败'))
  }
}

async function loadUsers() {
  try {
    const r = await request.get('/sys/user/list')
    users.value = r?.data || []
  } catch (e) {
    ElMessage.error(tt('用户列表加载失败'))
  }
}

async function loadRoles() {
  try {
    const r = await request.get('/sys/role/list')
    roles.value = r?.data || []
  } catch (e) {
    ElMessage.error(tt('角色列表加载失败'))
  }
}

function onDeptClick() {}

function newDept(parentId) {
  editingDept.value = null
  deptForm.id = null
  deptForm.parentId = parentId
  deptForm.deptName = ''
  deptVisible.value = true
}

function editDept(d) {
  editingDept.value = d
  deptForm.id = d.id
  deptForm.parentId = d.parentId
  deptForm.deptName = d.deptName
  deptVisible.value = true
}

async function saveDept() {
  if (!deptForm.deptName.trim()) return ElMessage.warning(tt('请输入部门名称'))
  savingDept.value = true
  try {
    await request.post('/sys/dept/save', { id: deptForm.id, parentId: deptForm.parentId || 0, deptName: deptForm.deptName })
    ElMessage.success(tt('部门已保存'))
    deptVisible.value = false
    await loadDepts()
  } catch (e) {
    ElMessage.error(e?.response?.data?.message || tt('保存失败'))
  } finally {
    savingDept.value = false
  }
}

async function delDept(d) {
  try {
    await ElMessageBox.confirm(tt('删除部门') + '「' + d.deptName + '」？', tt('提示'), { type: 'warning' })
  } catch (e) {
    return
  }
  try {
    await request.delete('/sys/dept/' + d.id)
    ElMessage.success(tt('部门已删除'))
    await loadDepts()
  } catch (e) {
    ElMessage.error(e?.response?.data?.message || tt('删除失败'))
  }
}

function openUser(row) {
  editingUser.value = row || null
  userForm.userName = row?.userName || ''
  userForm.realName = row?.realName || ''
  userForm.password = ''
  userForm.deptId = row?.deptId ?? null
  userForm.roleId = row?.roleId ?? null
  userForm.enabled = row?.enabled ?? 1
  userVisible.value = true
}

async function saveUser() {
  if (!userForm.userName.trim()) return ElMessage.warning(tt('请输入账号'))
  savingUser.value = true
  try {
    const body = { ...userForm }
    if (editingUser.value) body.id = editingUser.value.id
    await request.post('/sys/user/save', body)
    ElMessage.success(tt('用户已保存'))
    userVisible.value = false
    await loadUsers()
  } catch (e) {
    ElMessage.error(e?.response?.data?.message || tt('保存失败'))
  } finally {
    savingUser.value = false
  }
}

async function saveRole() {
  if (!roleForm.roleCode.trim() || !roleForm.roleName.trim()) return ElMessage.warning(tt('请填写编码与名称'))
  savingRole.value = true
  try {
    await request.post('/sys/role/save', { ...roleForm })
    ElMessage.success(tt('角色已创建'))
    newRoleVisible.value = false
    roleForm.roleCode = ''
    roleForm.roleName = ''
    roleForm.remark = ''
    await loadRoles()
  } catch (e) {
    ElMessage.error(e?.response?.data?.message || tt('创建失败'))
  } finally {
    savingRole.value = false
  }
}

async function delRole(row) {
  try {
    await ElMessageBox.confirm(tt('删除角色') + '「' + row.roleName + '」？' + tt('其下用户角色将清空'), tt('提示'), { type: 'warning' })
  } catch (e) {
    return
  }
  try {
    await request.delete('/sys/role/' + row.id)
    ElMessage.success(tt('角色已删除'))
    if (selRole.value && selRole.value.id === row.id) {
      selRole.value = null
      panelRows.value = []
    }
    await loadRoles()
  } catch (e) {
    ElMessage.error(e?.response?.data?.message || tt('删除失败'))
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
    permActions.value = (d.actions && d.actions.length) ? d.actions : PERM_ACTIONS
    groupDefs.value = (d.modules && d.modules.length) ? d.modules : [{ code: 'other', name: '其他' }]
    // 兼容旧后端：无 perms 时按 canApprove 回退 view / view+audit
    const grantedMap = {}
    for (const g of d.granted || []) {
      grantedMap[g.panelCode] = (g.perms && g.perms.length)
        ? g.perms
        : (g.canApprove ? ['view', 'audit'] : ['view'])
    }
    panelRows.value = (d.allPanels || []).map((p) => ({
      panelCode: p.panelCode,
      panelName: p.panelName,
      hasApproval: !!p.hasApproval,
      moduleGroup: p.moduleGroup || 'other',
      perms: Object.fromEntries((grantedMap[p.panelCode] || []).map((c) => [c, true])),
    }))
    openGroups.value = groupedPanels.value.filter((g) => g.panels.length).map((g) => g.code)
    ensurePageDict()
  } catch (e) {
    ElMessage.error(tt('面板权限加载失败'))
  }
}

async function savePanels() {
  saving.value = true
  try {
    const panels = panelRows.value
      .filter((p) => p.perms.view)
      .map((p) => ({
        panelCode: p.panelCode,
        perms: permActions.value.filter((a) => !a.reserved && p.perms[a.code]).map((a) => a.code),
      }))
    await request.post('/sys/role/' + selRole.value.id + '/panels', { panels })
    ElMessage.success(tt('权限矩阵已保存'))
    if (selRole.value.roleCode === user.roleCode) await user.fetchPerms()
  } catch (e) {
    ElMessage.error(e?.response?.data?.message || tt('保存失败'))
  } finally {
    saving.value = false
  }
}

// 非中文语言：预取本页可见词条（静态列头/按钮 + 权限名/模块组名/面板名；静态包缺失的走机翻补齐）
const PAGE_DICT_KEYS = [
  '部门', '新增部门', '+子', '改', '删', '用户', '新增用户', '编辑用户', '账号', '姓名', '状态', '操作',
  '启用', '停用', '编辑', '删除', '角色', '角色与面板权限', '创建角色', '角色名称', '角色编码', '编码', '类型',
  '超级', '面板', '面板权限矩阵', '个面板', '全选', '清空', '保存权限矩阵', '刷新', '取消', '保存', '创建',
  '上级部门', '部门名称', '备注', '密码', '选择角色', '编辑部门', '其他', '提示',
]
function ensurePageDict() {
  if (localeStore.isZh) return
  localeStore.ensureDict(localeStore.current, [
    ...PAGE_DICT_KEYS,
    ...permActions.value.map((a) => a.name),
    ...groupDefs.value.map((g) => g.name),
    ...panelRows.value.map((p) => p.panelName),
  ])
}
watch(() => localeStore.current, ensurePageDict)

onMounted(load)
</script>

<style scoped>
.org-wrap {
  display: flex;
  gap: 12px;
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
.org-col.dept { flex: 1; }
.org-col.users { flex: 1.4; }
.org-col.roles { flex: 1.6; }
.col-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}
.col-title { font-size: 14px; font-weight: 600; color: #1c4f8a; }
.col-tip { margin-top: 8px; font-size: 12px; color: #999; }
.dept-tree { overflow: auto; flex: 1; }
.dept-node {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding-right: 6px;
}
.dept-ops { display: none; }
.dept-node:hover .dept-ops { display: inline-flex; gap: 2px; }
.perm-box { margin-top: 12px; border-top: 1px dashed #d0d7e3; padding-top: 10px; }
.perm-head { font-size: 13px; font-weight: 600; color: #333; margin-bottom: 8px; }
.perm-sub { font-weight: 400; color: #888; font-size: 12px; }
.admin-tip { color: #c0392b; font-size: 12px; padding: 8px 0; }
.perm-actions { margin-top: 10px; display: flex; gap: 8px; }
/* 2026-08-25：按业务模块分组的权限配置 */
.perm-collapse {
  border: 1px solid #e3e8ef;
  border-radius: 6px;
  max-height: 340px;
  overflow-y: auto;
}
.perm-collapse :deep(.el-collapse-item__header) {
  height: 34px;
  line-height: 34px;
  padding: 0 10px;
  font-size: 13px;
  font-weight: 600;
  color: #1c4f8a;
  background: #f7f9fc;
}
.perm-collapse :deep(.el-collapse-item__wrap) {
  padding: 6px 10px 10px;
}
.g-count {
  font-weight: 400;
  color: #999;
  font-size: 12px;
  margin-left: 6px;
}
.g-actions {
  margin-left: auto;
  margin-right: 14px;
  display: inline-flex;
  align-items: center;
}
/* 2026-09-01：11 列权限矩阵（列头复选框列间收紧，预留位置灰） */
.perm-table :deep(.el-checkbox) {
  height: auto;
  margin-right: 0;
}
.perm-table :deep(.el-checkbox__label) {
  padding-left: 2px;
  font-size: 12px;
  font-weight: 400;
}
.perm-reserved {
  display: inline-flex;
  opacity: 0.45;
}
</style>
