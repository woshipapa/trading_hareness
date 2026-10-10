<script setup lang="ts">
import { computed, inject, onMounted, ref } from 'vue';
import { dashboardContextKey } from '../../dashboard-context';
import {
  categoryLabel, filterCapabilities, isResearchReadable, queryFromForm, statusLabel, statusType,
  type DatasourceBinding, type DatasourceCapability, type DatasourceCatalog, type DatasourceRead,
} from '../../research/datasource-directory';

const dashboard = inject(dashboardContextKey);
if (!dashboard) throw new Error('datasource directory requires the dashboard shell context');
const getJson = (dashboard as any).transport.getJson as <T>(path: string) => Promise<T>;
const catalog = ref<DatasourceCatalog | null>(null);
const loading = ref(false);
const error = ref('');
const sourceFilter = ref('');
const statusFilter = ref('');
const textFilter = ref('');
const selectedBinding = ref<DatasourceBinding | null>(null);
const formValues = ref<Record<string, string>>({});
const readLoading = ref(false);
const readError = ref('');
const readResult = ref<DatasourceRead | null>(null);

const sourceLabels = computed(() => Object.fromEntries((catalog.value?.sources ?? []).map((source) => [source.key, source.label])));
const filteredCapabilities = computed(() => filterCapabilities(catalog.value?.capabilities ?? [], sourceFilter.value, statusFilter.value, textFilter.value));
const groupedCapabilities = computed(() => filteredCapabilities.value.reduce<Record<string, DatasourceCapability[]>>((groups, capability) => {
  (groups[capability.category] ??= []).push(capability);
  return groups;
}, {}));

async function loadCatalog() {
  loading.value = true; error.value = '';
  try { catalog.value = await getJson<DatasourceCatalog>('/api/research/datasources/catalog'); }
  catch (reason) { error.value = reason instanceof Error ? reason.message : String(reason); }
  finally { loading.value = false; }
}

function openRead(binding: DatasourceBinding) {
  selectedBinding.value = binding; readResult.value = null; readError.value = '';
  formValues.value = Object.fromEntries(Object.keys(binding.spec?.params ?? {}).map((key) => [key, '']));
}

async function runRead(capability: DatasourceCapability) {
  const binding = selectedBinding.value;
  if (!binding) return;
  readLoading.value = true; readError.value = ''; readResult.value = null;
  try {
    const query = queryFromForm(formValues.value);
    const suffix = query ? `?${query}` : '';
    readResult.value = await getJson<DatasourceRead>('/api/research/datasources/read/' + binding.source + '/' + capability.key + suffix);
  } catch (reason) {
    readError.value = reason instanceof Error ? reason.message : String(reason);
  } finally { readLoading.value = false; }
}

function display(value: unknown): string {
  if (value === null || value === undefined || value === '') return '-';
  return typeof value === 'object' ? JSON.stringify(value) : String(value);
}

onMounted(loadCatalog);
</script>

<template>
  <el-card shadow="never" class="section-gap datasource-directory">
    <template #header><div class="card-header"><span>数据源能力目录</span><el-button :loading="loading" @click="loadCatalog">刷新目录</el-button></div></template>
    <el-alert v-if="error" :title="`数据源目录读取失败：${error}`" type="error" show-icon :closable="false" class="section-gap" />
    <el-alert title="目录展示的是研究能力与证据状态；研究试读不会进入策略或订单路径。" type="info" show-icon :closable="false" class="section-gap" />
    <el-form inline class="section-gap" @submit.prevent>
      <el-form-item label="来源"><el-select v-model="sourceFilter" clearable filterable placeholder="全部来源" style="width: 210px"><el-option label="TDX" value="tdx_public" /><el-option v-for="source in catalog?.sources ?? []" :key="source.key" :label="source.label" :value="source.key" /></el-select></el-form-item>
      <el-form-item label="状态"><el-select v-model="statusFilter" clearable placeholder="全部状态" style="width: 170px"><el-option v-for="status in ['live_verified', 'declared', 'dormant', 'unsupported', 'retired']" :key="status" :label="statusLabel(status)" :value="status" /></el-select></el-form-item>
      <el-form-item label="搜索"><el-input v-model="textFilter" clearable placeholder="能力、来源或说明" style="width: 240px" /></el-form-item>
    </el-form>
    <el-empty v-if="!loading && !filteredCapabilities.length" description="没有匹配的能力" />
    <el-collapse v-else>
      <el-collapse-item v-for="(items, category) in groupedCapabilities" :key="category" :name="category">
        <template #title><strong>{{ categoryLabel(category) }}</strong><el-tag size="small" type="info" class="category-count">{{ items.length }}</el-tag></template>
        <el-table :data="items" size="small" class="section-gap" row-key="key">
          <el-table-column prop="key" label="能力" min-width="190" />
          <el-table-column prop="label" label="名称" min-width="125" />
          <el-table-column label="绑定" min-width="420">
            <template #default="{ row }">
              <div v-for="binding in row.bindings" :key="`${binding.source}-${binding.capability}`" class="binding-line">
                <el-tag size="small" :type="statusType(binding.status)">{{ statusLabel(binding.status) }}</el-tag>
                <span class="binding-source">{{ sourceLabels[binding.source] ?? binding.source }}</span>
                <span>优先级 {{ binding.priority }}</span>
                <span>{{ binding.decision_eligible ? '决策可用' : '仅证据' }}</span>
                <el-button v-if="isResearchReadable(binding)" link type="primary" @click="openRead(binding)">研究试读</el-button>
                <div v-if="binding.notes" class="muted">{{ binding.notes }}</div>
                <div v-if="binding.spec" class="muted">参数 {{ Object.keys(binding.spec.params).join('、') || '无' }}；{{ binding.spec.time_semantics || '时间语义未声明' }}</div>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="证据位置" min-width="220">
            <template #default="{ row }"><div v-for="location in row.evidence_locations ?? []" :key="`${location.source}-${location.table}`" class="muted">{{ location.source }} · {{ location.table }}<span v-if="location.filter"> {{ display(location.filter) }}</span></div><span v-if="!row.evidence_locations?.length">-</span></template>
          </el-table-column>
          <el-table-column label="口径" min-width="240"><template #default="{ row }"><div class="muted">{{ row.time_semantics || '-' }}</div><div v-if="row.fields?.length" class="muted">字段：{{ row.fields.join('、') }}</div></template></el-table-column>
        </el-table>
        <el-card v-if="selectedBinding && items.some((item) => item.key === selectedBinding?.capability)" shadow="never" class="read-panel">
          <template #header><div class="card-header"><span>研究试读 · {{ selectedBinding.source }} / {{ selectedBinding.capability }}</span><el-tag type="warning">研究证据</el-tag></div></template>
          <el-form inline @submit.prevent>
            <el-form-item v-for="(_, key) in selectedBinding.spec?.params ?? {}" :key="key" :label="key"><el-input v-model="formValues[key]" :placeholder="key === 'symbols' ? '逗号分隔，最多80只' : '按接口参数填写'" /></el-form-item>
            <el-button type="primary" :loading="readLoading" @click="runRead(items.find((item) => item.key === selectedBinding?.capability)!)">读取</el-button>
          </el-form>
          <el-alert v-if="readError" :title="readError" type="error" show-icon :closable="false" class="section-gap" />
          <template v-if="readResult"><el-descriptions :column="4" border size="small" class="section-gap"><el-descriptions-item label="覆盖">{{ readResult.coverage ?? '-' }}</el-descriptions-item><el-descriptions-item label="行数">{{ readResult.rows.length }}<span v-if="readResult.truncated">（已截断）</span></el-descriptions-item><el-descriptions-item label="可用时间">{{ readResult.available_at_min ?? '-' }}</el-descriptions-item><el-descriptions-item label="决策资格">仅研究证据</el-descriptions-item></el-descriptions><el-alert v-if="readResult.warnings.length" :title="readResult.warnings.join('；')" type="warning" :closable="false" class="section-gap" /><el-table :data="readResult.rows.slice(0, 20)" max-height="260" size="small"><el-table-column v-for="key in Object.keys(readResult.rows[0] ?? {})" :key="key" :prop="key" :label="key" min-width="120" show-overflow-tooltip /></el-table></template>
        </el-card>
      </el-collapse-item>
    </el-collapse>
  </el-card>
</template>

<style scoped>
.card-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.category-count { margin-left: 10px; }
.binding-line { margin: 0 0 8px; line-height: 1.6; }
.binding-source { margin: 0 8px; font-weight: 600; }
.muted { color: var(--el-text-color-secondary); font-size: 12px; }
.read-panel { margin-top: 12px; }
</style>
