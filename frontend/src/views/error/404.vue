<template>
  <div class="nf">
    <el-result icon="warning" title="404" :sub-title="tt('页面不存在')">
      <template #extra>
        <el-button type="primary" @click="$router.replace('/dashboard')">{{ tt('回我的桌面') }}</el-button>
      </template>
    </el-result>
  </div>
</template>

<script setup>
import { tt } from '@/i18n'
</script>

<style scoped>
.nf {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}
</style>
