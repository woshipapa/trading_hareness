<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { Refresh } from '@element-plus/icons-vue';
import VChart from 'vue-echarts';
import { getJson } from '../../api/http';
import { shanghaiTime, shanghaiToday } from '../../research/session-radar';
import {
  BAND_TAGS, TIMING_LABELS, componentRows, dailyOption, intradayOption, latestReading, percentText,
  type TemperatureDaily, type TemperatureIntraday,
} from '../../research/sentiment-temperature';

const days = ref(120);
const tradeDate = ref(shanghaiToday());
const daily = ref<TemperatureDaily | null>(null);
const intraday = ref<TemperatureIntraday | null>(null);
const errors = ref<Record<string, string>>({});
const loading = ref(false);
let timer: ReturnType<typeof setInterval> | undefined;

async function read<T>(key: string, path: string): Promise<T | null> {
  try {
    return await getJson<T>(path);
  } catch (error) {
    errors.value = { ...errors.value, [key]: error instanceof Error ? error.message : String(error) };
    return null;
  }
}

async function loadDaily() {
  const day = await read<TemperatureDaily>('日线', `/api/research/market/temperature/daily?days=${days.value}`);
  if (day) daily.value = day;
}

async function loadIntraday() {
  const day = await read<TemperatureIntraday>('分时', `/api/research/market/temperature/intraday?trade_date=${encodeURIComponent(tradeDate.value)}`);
  intraday.value = day;
}

async function load() {
  loading.value = true;
  errors.value = {};
  await Promise.all([loadDaily(), loadIntraday()]);
  loading.value = false;
}

// A finished session is stored; only today's is recomputed, once per five-minute sample.
onMounted(() => {
  void load();
  timer = setInterval(() => { if (tradeDate.value === shanghaiToday()) void loadIntraday(); }, 5 * 60 * 1000);
});
onBeforeUnmount(() => { if (timer) clearInterval(timer); });
watch(days, () => { void loadDaily(); });
watch(tradeDate, () => { void loadIntraday(); });

const latest = computed(() => latestReading(daily.value));
const components = computed(() => daily.value?.components ?? []);
const dailyChart = computed(() => (daily.value?.readings.length ? dailyOption(daily.value) : null));
const intradayChart = computed(() => (intraday.value ? intradayOption(intraday.value, daily.value?.thresholds) : null));
const lastSample = computed(() => intraday.value?.samples.at(-1) ?? null);
const dailyRows = computed(() => componentRows(latest.value, components.value));
const sampleRows = computed(() => componentRows(lastSample.value, components.value));
const markerEvidence = computed(() => daily.value?.marker_evidence ?? null);
const timingEvidence = computed(() => daily.value?.timing_evidence ?? null);
</script>

<template>
  <section class="temperature">
    <div class="toolbar">
      <el-radio-group v-model="days" size="small">
        <el-radio-button :value="60">60 日</el-radio-button>
        <el-radio-button :value="120">120 日</el-radio-button>
        <el-radio-button :value="250">250 日</el-radio-button>
      </el-radio-group>
      <el-date-picker v-model="tradeDate" type="date" value-format="YYYY-MM-DD" size="small" :clearable="false" />
      <el-button :icon="Refresh" :loading="loading" size="small" @click="load">刷新</el-button>
      <el-tag type="info" effect="plain">研究用 · 不构成交易指令</el-tag>
    </div>
    <el-alert v-for="(message, key) in errors" :key="key" :title="`${key}：${message}`" type="warning" show-icon :closable="false" class="gap" />

    <div v-if="latest" class="summary">
      <el-card shadow="never">
        <div class="caption">{{ latest.trade_date }} 情绪温度</div>
        <div class="big">{{ latest.temperature }}<el-tag v-if="latest.band" :type="BAND_TAGS[latest.band]" class="band">{{ latest.band }}</el-tag></div>
      </el-card>
      <el-card shadow="never">
        <div class="caption">宽基 ETF 放量比（12 只，÷ 前 20 日均值）</div>
        <div class="big">{{ latest.etf_flow?.ratio?.toFixed(2) ?? '-' }}<span class="unit">×</span>
          <el-tag v-if="(latest.etf_flow?.ratio ?? 0) >= (daily?.thresholds.etf_surge_ratio ?? 1.5)" type="danger" class="band">放量</el-tag></div>
      </el-card>
      <el-card shadow="never">
        <div class="caption">金 / 银指（2560，上证）</div>
        <div class="big timing" :class="latest.timing?.state">{{ latest.timing?.state ? TIMING_LABELS[latest.timing.state] : '-' }}</div>
      </el-card>
      <el-card shadow="never">
        <div class="caption">冰点资金共振</div>
        <div class="big marker">{{ latest.marker ? `★ ${latest.marker.label}` : '无' }}</div>
      </el-card>
    </div>

    <el-card shadow="never" class="gap">
      <template #header>日线情绪温度 · 上证指数 · 金银指背景（红=金指 / 绿=银指）</template>
      <v-chart v-if="dailyChart" :option="dailyChart" autoresize class="chart" />
      <el-empty v-else description="还没有落库的日线温度" />
    </el-card>

    <el-card shadow="never" class="gap">
      <template #header>
        分时情绪温度 · {{ intraday?.trade_date ?? tradeDate }}
        <el-tag v-if="intraday?.source" size="small" effect="plain">{{ intraday.source === 'stored' ? '已存' : '现算' }}</el-tag>
        <span v-if="lastSample" class="caption"> 最新 {{ lastSample.time }}（{{ lastSample.observed_at ? shanghaiTime(lastSample.observed_at) : '-' }}）</span>
      </template>
      <v-chart v-if="intradayChart && intraday?.samples.length" :option="intradayChart" autoresize class="chart" />
      <el-empty v-else description="该交易日还没有分钟截面" />
    </el-card>

    <el-row :gutter="12" class="gap">
      <el-col :md="12" :span="24">
        <el-card shadow="never">
          <template #header>日线分项（{{ latest?.trade_date ?? '-' }}）</template>
          <el-table :data="dailyRows" size="small">
            <el-table-column prop="label" label="分项" />
            <el-table-column prop="value" label="数值" />
            <el-table-column label="分位得分">
              <template #default="{ row }">{{ row.score ?? '-' }}<span v-if="row.colder" class="caption">（越多越冷）</span></template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
      <el-col :md="12" :span="24">
        <el-card shadow="never">
          <template #header>分时分项（{{ lastSample?.time ?? '-' }}）</template>
          <el-table :data="sampleRows" size="small">
            <el-table-column prop="label" label="分项" />
            <el-table-column prop="value" label="数值" />
            <el-table-column prop="score" label="分位得分" />
          </el-table>
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never" class="gap notes">
      <template #header>口径与回测（决策 0013）</template>
      <p>温度：8 个分项各按前 250 个交易日的分位换成 0–100 后取平均；≤20 冰点、≥80 沸点。分时用同一把尺子，按“此刻收盘”计算。</p>
      <p v-if="markerEvidence">
        冰点资金共振（温度 ≤40 且宽基 ETF 放量比 ≥1.5，指数同日下跌为“逆势放量”）：{{ markerEvidence.window }} 共 {{ markerEvidence.cold_with_surge.days }} 天，
        之后 1 / 3 / 5 日上涨 {{ percentText(markerEvidence.cold_with_surge.up_1d) }} / {{ percentText(markerEvidence.cold_with_surge.up_3d) }} / {{ percentText(markerEvidence.cold_with_surge.up_5d) }}；
        所有冷日是 {{ percentText(markerEvidence.cold_all.up_1d) }} / {{ percentText(markerEvidence.cold_all.up_3d) }} / {{ percentText(markerEvidence.cold_all.up_5d) }}。
        注意：只有四五次独立事件；指数调仓、新基金上市也会放大 ETF 成交额。
      </p>
      <p v-if="timingEvidence">
        金 / 银指：{{ timingEvidence.window }}，只在金指时持有，最大回撤从 {{ percentText(timingEvidence.max_drawdown.buy_and_hold) }} 降到
        {{ percentText(timingEvidence.max_drawdown.golden_only) }}；2015 年以来对短期涨跌几乎没有预测力，只作趋势背景，不与共振标记叠加。
      </p>
    </el-card>
  </section>
</template>

<style scoped>
.toolbar { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; margin-bottom: 12px; }
.summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }
.caption { color: #8a8f98; font-size: 12px; }
.big { font-size: 26px; font-weight: 700; display: flex; align-items: center; gap: 8px; }
.unit { font-size: 14px; color: #8a8f98; }
.band { font-size: 13px; }
.timing.golden { color: #d93026; }
.timing.silver { color: #188038; }
.marker { color: #c99a06; }
.chart { height: 380px; width: 100%; }
.gap { margin-top: 12px; }
.notes p { margin: 4px 0; line-height: 1.6; }
</style>
