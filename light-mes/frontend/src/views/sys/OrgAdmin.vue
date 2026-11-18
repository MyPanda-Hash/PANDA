<!-- OrgAdmin.vue — 组织架构（仅管理员）：部门树 + 用户管理（分配角色/部门）+ 角色与面板权限矩阵 -->
<template>
  <div class="adx org">
    <!-- 页头：三层关系一句话说清，右侧只放刷新 -->
    <header class="d-head">
      <div>
        <h1 class="d-head__title">{{ tt('组织架构') }}</h1>
        <p class="d-head__desc">
          {{ tt('部门定层级') }} · {{ tt('用户挂部门与角色') }} · {{ tt('角色决定能看到哪些面板') }}
        </p>
      </div>
      <el-tooltip :content="tt('重新加载')" placement="top">
        <el-button :loading="loadingAll" aria-label="重新加载" @click="reloadAll">
          <el-icon><Refresh /></el-icon>
        </el-button>
      </el-tooltip>
    </header>

    <div class="d-grid">
      <!-- 左：部门 -->
      <section class="d-card d-span-4">
        <div class="d-card__head">
          <div>
            <h2 class="d-card__title">{{ tt('部门') }}</h2>
            <p class="d-card__desc">{{ tt('支持多级部门，最多到车间/班组') }}</p>
          </div>
          <el-button type="primary" @click="newDept(0)">{{ tt('新增部门') }}</el-button>
        </div>

        <div class="d-tree">
          <el-tree
            v-if="deptTree.length"
            class="dept-tree"
            :data="deptTree"
            node-key="id"
            :props="{ label: 'deptName', children: 'children' }"
            highlight-current
            :expand-on-click-node="false"
            default-expand-all
            @node-click="onDeptClick"
          >
            <template #default="{ data }">
              <div class="d-tree__row">
                <span class="d-tree__name" :title="data.deptName">{{ data.deptName }}</span>
                <span class="d-tree__ops" @click.stop>
                  <el-button link type="primary" @click="newDept(data.id)">{{ tt('+子') }}</el-button>
                  <el-button link type="primary" @click="editDept(data)">{{ tt('改') }}</el-button>
                  <el-button v-if="data.id !== 1" link type="danger" @click="delDept(data)">{{ tt('删') }}</el-button>
                </span>
              </div>
            </template>
          </el-tree>
          <div v-else class="d-empty">
            <el-icon><OfficeBuilding /></el-icon>
            <p>{{ tt('还没有部门') }}</p>
            <el-button type="primary" size="small" @click="newDept(0)">{{ tt('新增部门') }}</el-button>
          </div>
        </div>

        <p class="d-tip">{{ tt('支持多级部门；「+子」新增下级部门') }}</p>
      </section>

      <!-- 中：用户 -->
      <section class="d-card d-span-8">
        <div class="d-card__head">
          <div>
            <h2 class="d-card__title">{{ tt('用户') }}</h2>
            <p class="d-card__desc">{{ tt('点击用户行分配部门 / 角色 / 启停用') }}</p>
          </div>
          <el-button type="primary" @click="openUser()">{{ tt('新增用户') }}</el-button>
        </div>

        <div class="d-toolbar">
          <el-input
            v-model="keyword"
            class="d-search"
            clearable
            :placeholder="tt('搜索账号或姓名')"
            :prefix-icon="Search"
          />
          <el-select v-model="statusFilter" class="d-filter">
            <el-option :label="tt('全部状态')" value="all" />
            <el-option :label="tt('启用')" value="on" />
            <el-option :label="tt('停用')" value="off" />
          </el-select>
          <span class="d-count">{{ filteredUsers.length }} / {{ users.length }} {{ tt('人') }}</span>
        </div>

        <el-table
          v-if="users.length"
          :data="filteredUsers"
          class="d-table"
          max-height="392"
          highlight-current-row
          @row-click="openUser"
        >
          <el-table-column prop="userName" :label="tt('账号')" min-width="110" />
          <el-table-column prop="realName" :label="tt('姓名')" min-width="90">
            <template #default="{ row }">{{ row.realName || '-' }}</template>
          </el-table-column>
          <el-table-column :label="tt('部门')" min-width="120">
            <template #default="{ row }">{{ row.deptName || '-' }}</template>
          </el-table-column>
          <el-table-column :label="tt('角色')" min-width="120">
            <template #default="{ row }">{{ row.roleName || '-' }}</template>
          </el-table-column>
          <el-table-column :label="tt('状态')" width="88">
            <template #default="{ row }">
              <span class="d-tag" :class="row.enabled ? 'is-stable' : 'is-neutral'">
                {{ row.enabled ? tt('启用') : tt('停用') }}
              </span>
            </template>
          </el-table-column>
          <el-table-column :label="tt('操作')" width="76" align="right">
            <template #default="{ row }">
              <el-button link type="primary" @click.stop="openUser(row)">{{ tt('编辑') }}</el-button>
            </template>
          </el-table-column>
        </el-table>
        <div v-else class="d-empty">
          <el-icon><User /></el-icon>
          <p>{{ tt('还没有用户') }}</p>
          <el-button type="primary" size="small" @click="openUser()">{{ tt('新增用户') }}</el-button>
        </div>
        <div v-if="users.length && !filteredUsers.length" class="d-empty d-empty--inline">
          <p>{{ tt('没有匹配的用户') }}</p>
          <el-button size="small" @click="keyword = ''; statusFilter = 'all'">{{ tt('清空筛选') }}</el-button>
        </div>
      </section>

      <!-- 下：角色与面板权限（整行，权限矩阵需要横向空间） -->
      <section class="d-card d-span-12">
        <div class="d-card__head">
          <div>
            <h2 class="d-card__title">{{ tt('角色与面板权限') }}</h2>
            <p class="d-card__desc">{{ tt('选中左侧角色，在右侧配置 11 项面板权限') }}</p>
          </div>
          <el-button type="primary" @click="newRoleVisible = true">{{ tt('创建角色') }}</el-button>
        </div>

        <div class="d-roles">
          <!-- 角色列表 -->
          <div class="d-roles__list">
            <el-table
              :data="roles"
              class="d-table d-table--roles"
              max-height="360"
              highlight-current-row
              :current-row-key="selRole?.id"
              row-key="id"
              @current-change="onRoleSelect"
            >
              <el-table-column prop="roleName" :label="tt('角色名称')" min-width="110" show-overflow-tooltip />
              <el-table-column prop="roleCode" :label="tt('编码')" width="76" show-overflow-tooltip />
              <el-table-column :label="tt('类型')" width="64">
                <template #default="{ row }">
                  <span v-if="row.isAdmin" class="d-tag is-risk">{{ tt('超级') }}</span>
                  <span v-else class="d-muted">-</span>
                </template>
              </el-table-column>
              <el-table-column :label="tt('操作')" width="58" align="right">
                <template #default="{ row }">
                  <el-button v-if="!row.isAdmin" link type="danger" @click.stop="delRole(row)">{{ tt('删除') }}</el-button>
                  <span v-else class="d-muted">-</span>
                </template>
              </el-table-column>
              <template #empty>
                <div class="d-empty d-empty--inline">
                  <p>{{ tt('还没有角色') }}</p>
                  <el-button type="primary" size="small" @click="newRoleVisible = true">{{ tt('创建角色') }}</el-button>
                </div>
              </template>
            </el-table>
          </div>

          <!-- 权限矩阵 -->
          <div class="d-roles__perm">
            <div v-if="!selRole" class="d-empty d-empty--box">
              <el-icon><Lock /></el-icon>
              <p>{{ tt('选择一个角色查看权限矩阵') }}</p>
            </div>

            <template v-else>
              <div class="d-permhead">
                <span class="d-permhead__name">「{{ selRole.roleName }}」{{ tt('面板权限矩阵') }}</span>
                <span class="d-permhead__hint">
                  {{ tt('勾「查看」才有菜单入口，取消「查看」清空整行；审批列仅审批流面板可勾') }}
                </span>
              </div>

              <div v-if="selRole.isAdmin" class="d-alert is-warning">
                <el-icon><Warning /></el-icon>
                <span>{{ tt('管理员为超级权限：默认全部面板全部权限，无需配置。') }}</span>
              </div>

              <template v-else>
                <el-collapse v-model="openGroups" class="perm-collapse">
                  <el-collapse-item v-for="g in groupedPanels" :key="g.code" :name="g.code">
                    <template #title>
                      <div class="d-grouphead">
                        <span class="d-grouphead__name">{{ tt(g.name) }}</span>
                        <span class="d-grouphead__count">{{ g.panels.length }} {{ tt('个面板') }}</span>
                        <span class="d-grouphead__ops" @click.stop>
                          <el-button link type="primary" @click="setGroupAll(g, true)">{{ tt('全选') }}</el-button>
                          <el-button link @click="setGroupAll(g, false)">{{ tt('清空') }}</el-button>
                        </span>
                      </div>
                    </template>
                    <el-table :data="g.panels" class="d-table d-table--perm">
                      <el-table-column :label="tt('面板')" prop="panelName" min-width="150" fixed="left" show-overflow-tooltip>
                        <template #default="{ row }">{{ tt(row.panelName) }}</template>
                      </el-table-column>
                      <el-table-column v-for="a in permActions" :key="a.code" width="60" align="center">
                        <template #header>
                          <div class="perm-col">
                            <span class="perm-col__label">{{ tt(a.name) }}</span>
                            <el-checkbox
                              :model-value="groupColChecked(g, a.code)"
                              :indeterminate="groupColIndeterminate(g, a.code)"
                              :disabled="a.reserved || (a.code === 'audit' && !rowsForCol(g, a.code).length)"
                              @change="(v) => setGroupCol(g, a.code, v)"
                            />
                          </div>
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

                <div class="d-permfoot">
                  <span class="d-permfoot__hint">{{ tt('展开模块后按列勾选可整列切换') }}</span>
                  <span class="d-permfoot__ops">
                    <el-button @click="loadRolePanels(selRole)">{{ tt('刷新') }}</el-button>
                    <el-button type="primary" :loading="saving" @click="savePanels">{{ tt('保存权限矩阵') }}</el-button>
                  </span>
                </div>
              </template>
            </template>
          </div>
        </div>
      </section>
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
import { Search } from '@element-plus/icons-vue'
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
const loadingAll = ref(false)

// 用户列表筛选：账号/姓名关键字 + 启停状态（数据少，前端筛即可）
const keyword = ref('')
const statusFilter = ref('all')
const filteredUsers = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  return users.value.filter((u) => {
    if (statusFilter.value === 'on' && !u.enabled) return false
    if (statusFilter.value === 'off' && u.enabled) return false
    if (!kw) return true
    return [u.userName, u.realName].some((v) => String(v || '').toLowerCase().includes(kw))
  })
})

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
  loadingAll.value = true
  try {
    await Promise.all([loadDepts(), loadUsers(), loadRoles()])
  } finally {
    loadingAll.value = false
  }
  ensurePageDict()
}

async function reloadAll() {
  await load()
  ElMessage.success(tt('已重新加载'))
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
  '组织架构', '部门定层级', '用户挂部门与角色', '角色决定能看到哪些面板', '重新加载', '已重新加载',
  '部门', '新增部门', '+子', '改', '删', '用户', '新增用户', '编辑用户', '账号', '姓名', '状态', '操作',
  '启用', '停用', '编辑', '删除', '角色', '角色与面板权限', '创建角色', '角色名称', '角色编码', '编码', '类型',
  '超级', '面板', '面板权限矩阵', '个面板', '全选', '清空', '保存权限矩阵', '刷新', '取消', '保存', '创建',
  '上级部门', '部门名称', '备注', '密码', '选择角色', '编辑部门', '其他', '提示',
  '支持多级部门，最多到车间/班组', '点击用户行分配部门 / 角色 / 启停用', '选中左侧角色，在右侧配置 11 项面板权限',
  '搜索账号或姓名', '全部状态', '人', '还没有部门', '还没有用户', '没有匹配的用户', '清空筛选', '还没有角色',
  '选择一个角色查看权限矩阵', '勾「查看」才有菜单入口，取消「查看」清空整行；审批列仅审批流面板可勾',
  '展开模块后按列勾选可整列切换',
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
/* 设计变量来自全局 `.adx`；本页只管布局与 Element Plus 的 antd 化 */
.org {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

/* ===== 页头 ===== */
.d-head {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.d-head__title {
  margin: 0;
  color: var(--d-text);
  font-size: 20px;
  font-weight: 600;
  line-height: 28px;
}
.d-head__desc {
  margin: 4px 0 0;
  color: var(--d-text-3);
  font-size: 12px;
  line-height: 20px;
  text-wrap: pretty;
}

/* ===== 栅格与卡片（与「我的桌面」同一套） ===== */
.d-grid {
  display: grid;
  grid-template-columns: repeat(12, minmax(0, 1fr));
  gap: 16px;
  align-items: start;
}
.d-span-4 { grid-column: span 4; }
.d-span-8 { grid-column: span 8; }
.d-span-12 { grid-column: span 12; }

.d-card {
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding: 24px;
  border: 1px solid var(--d-border);
  border-radius: var(--d-radius);
  background: var(--d-card);
}
.d-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.d-card__title {
  margin: 0;
  color: var(--d-text);
  font-size: 16px;
  font-weight: 600;
  line-height: 24px;
}
.d-card__desc {
  margin: 2px 0 0;
  color: var(--d-text-3);
  font-size: 12px;
  line-height: 20px;
  text-wrap: pretty;
}
.d-count {
  margin-left: auto;
  color: var(--d-text-3);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.d-muted {
  color: var(--d-text-3);
}
.d-tip {
  margin: 12px 0 0;
  color: var(--d-text-3);
  font-size: 12px;
  line-height: 20px;
}

/* ===== 标签 ===== */
.d-tag {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 8px;
  border-radius: 4px;
  background: var(--d-fill);
  color: var(--d-text-2);
  font-size: 12px;
  line-height: 20px;
  white-space: nowrap;
}
.d-tag.is-stable { background: var(--d-ok-bg); color: var(--d-ok); }
.d-tag.is-risk { background: var(--d-bad-bg); color: var(--d-bad); }
.d-tag.is-neutral { background: var(--d-fill); color: var(--d-text-2); }

/* ===== 提示条 ===== */
.d-alert {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-top: 16px;
  padding: 8px 12px;
  border: 1px solid transparent;
  border-radius: var(--d-radius-sm);
  font-size: 12px;
  line-height: 20px;
}
.d-alert.is-warning {
  border-color: rgba(250, 173, 20, 0.35);
  background: var(--d-warn-bg);
  color: var(--d-warn);
}

/* ===== 工具栏 ===== */
.d-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 16px;
}
.d-search {
  max-width: 240px;
}
.d-filter {
  width: 112px;
}

/* ===== 部门树 ===== */
.d-tree {
  flex: 1;
  min-height: 200px;
  max-height: 420px;
  margin-top: 12px;
  overflow: auto;
}
.dept-tree :deep(.el-tree-node__content) {
  height: 32px;
  border-radius: 4px;
}
.dept-tree :deep(.el-tree-node__content:hover) {
  background: var(--d-fill-soft);
}
.dept-tree :deep(.el-tree-node.is-current > .el-tree-node__content) {
  background: var(--d-primary-bg);
  color: var(--d-primary);
  font-weight: 600;
}
.d-tree__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  gap: 8px;
  padding-right: 4px;
}
.d-tree__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.d-tree__ops {
  display: none;
  flex: 0 0 auto;
  gap: 4px;
}
.d-tree__row:hover .d-tree__ops {
  display: inline-flex;
}

/* ===== 表格：按 antd 表头/行高/分隔线重排 ===== */
.d-table {
  width: 100%;
  margin-top: 12px;
}
/* 固定表格布局：表头与表体列宽同源，否则表头内容（复选框+文字）会把列撑开导致对不齐 */
.d-table :deep(.el-table__inner-wrapper table) {
  table-layout: fixed;
}
.d-table :deep(.el-table__inner-wrapper::before) {
  display: none;
}
.d-table :deep(th.el-table__cell) {
  height: 40px;
  padding: 0;
  background: var(--d-fill-soft);
  border-bottom: 1px solid var(--d-border);
  color: var(--d-text-2);
  font-size: 12px;
  font-weight: 600;
  text-align: left;
}
.d-table :deep(th.el-table__cell .cell) {
  justify-content: flex-start !important;
  text-align: left !important;
  padding: 0 12px;
}
.d-table :deep(th.el-table__cell.is-right .cell) {
  justify-content: flex-end !important;
  text-align: right !important;
}
.d-table :deep(th.el-table__cell.is-center .cell) {
  justify-content: center !important;
  text-align: center !important;
}
.d-table :deep(td.el-table__cell) {
  height: 44px;
  padding: 0;
  border-bottom: 1px solid var(--d-border);
  color: var(--d-text);
  font-size: 14px;
}
.d-table :deep(td.el-table__cell .cell) {
  padding: 0 12px;
}
.d-table :deep(.el-table__row:hover > td.el-table__cell) {
  background: var(--d-fill-soft);
}
.d-table :deep(.el-table__row.current-row > td.el-table__cell) {
  background: var(--d-primary-bg);
}
.d-table :deep(.el-table__body tr > td.el-table__cell:first-child) {
  color: var(--d-text);
}
/* 窄屏表格需要横向滚动时，滚动条常显，避免「内容被切掉」而无提示 */
.d-table :deep(.el-scrollbar__bar.is-horizontal) {
  opacity: 1;
  height: 6px;
}
.d-table :deep(.el-scrollbar__bar.is-horizontal > div) {
  height: 6px;
}

/* ===== 角色 + 权限矩阵 ===== */
.d-roles {
  display: grid;
  grid-template-columns: 320px minmax(0, 1fr);
  gap: 24px;
  margin-top: 16px;
}
.d-roles__list {
  min-width: 0;
}
.d-table--roles {
  margin-top: 0;
}
.d-roles__perm {
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding-left: 24px;
  border-left: 1px solid var(--d-border);
}
.d-permhead {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 12px;
}
.d-permhead__name {
  color: var(--d-text);
  font-size: 14px;
  font-weight: 600;
}
.d-permhead__hint {
  color: var(--d-text-3);
  font-size: 12px;
  line-height: 20px;
}
.perm-collapse {
  border: 1px solid var(--d-border);
  border-radius: var(--d-radius-sm);
  max-height: 420px;
  overflow-y: auto;
}
.perm-collapse :deep(.el-collapse-item__header) {
  height: 40px;
  padding: 0 12px;
  border-bottom-color: var(--d-border);
  background: var(--d-fill-soft);
  color: var(--d-text);
  font-size: 13px;
  line-height: 40px;
}
.perm-collapse :deep(.el-collapse-item__wrap) {
  border-bottom-color: var(--d-border);
}
.perm-collapse :deep(.el-collapse-item__content) {
  padding: 0;
  background: var(--d-card);
}
.d-grouphead {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding-right: 12px;
}
.d-grouphead__name {
  font-weight: 600;
}
.d-grouphead__count {
  color: var(--d-text-3);
  font-size: 12px;
  font-weight: 400;
}
.d-grouphead__ops {
  margin-left: auto;
  display: inline-flex;
  gap: 8px;
}
.d-table--perm {
  margin-top: 0;
}
/* 权限列窄：表头改成「名字在上、整列勾选在下」，与表体复选框同列同轴 */
.d-table--perm :deep(th.el-table__cell) {
  height: 48px;
  vertical-align: middle;
}
.d-table--perm :deep(th.el-table__cell .cell) {
  justify-content: center !important;
  padding: 0 2px;
}
.perm-col {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  line-height: 16px;
}
.perm-col__label {
  color: var(--d-text-2);
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
}
.perm-col :deep(.el-checkbox) {
  height: 16px;
  margin-right: 0;
}
.d-table--perm :deep(td.el-table__cell) {
  height: 40px;
}
.d-table--perm :deep(td.el-table__cell .cell) {
  padding: 0 2px;
}
.perm-reserved {
  display: inline-flex;
  align-items: center;
}
.d-permfoot {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: auto;
  padding-top: 16px;
}
.d-permfoot__hint {
  color: var(--d-text-3);
  font-size: 12px;
}
.d-permfoot__ops {
  display: inline-flex;
  gap: 8px;
}

/* ===== 空态 ===== */
.d-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 32px 0;
  color: var(--d-text-3);
  font-size: 12px;
}
.d-empty .el-icon {
  color: var(--d-text-3);
  font-size: 24px;
}
.d-empty p {
  margin: 0;
}
.d-empty--inline {
  padding: 16px 0;
}
.d-empty--box {
  flex: 1;
  min-height: 240px;
}

/* ===== 响应式 ===== */
@media (max-width: 1100px) {
  .d-span-4 {
    grid-column: span 12;
  }
  .d-span-8 {
    grid-column: span 12;
  }
  .d-tree {
    max-height: 280px;
  }
  .d-roles {
    grid-template-columns: 1fr;
    gap: 16px;
  }
  .d-roles__perm {
    padding-left: 0;
    border-left: 0;
    border-top: 1px solid var(--d-border);
    padding-top: 16px;
  }
}

@media (max-width: 768px) {
  .d-card {
    padding: 16px;
  }
  .d-toolbar {
    flex-wrap: wrap;
  }
  .d-search {
    max-width: none;
    flex: 1 1 160px;
  }
  .d-table :deep(.el-table__body-wrapper),
  .d-table :deep(.el-scrollbar__wrap) {
    overflow-x: auto;
  }
}
</style>