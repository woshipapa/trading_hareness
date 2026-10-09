<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { Refresh } from '@element-plus/icons-vue';
import VChart from 'vue-echarts';
import { getJson } from '../../api/http';
import {
  BAND_LABELS, GATE_LABELS, PHASE_LABELS, SEGMENT_LABELS, auctionRows, bandCounts, boardText, breadthText, lineRankText,
  pctText, radarLegend,
  radarOption, radarRows, shanghaiTime, shanghaiToday, stars, tone, yi,
  type CardPick, type LimitDetailDay, type RadarDay, type StrategyCardsDay,
} from '../../research/session-radar';

const tradeDate = ref(shanghaiToday());
const band = ref('2');
const segment = ref('all');
const perLine = ref(5);
const autoRefresh = ref(true);
const loading = ref(false);
const radar = ref<RadarDay | null>(null);
const cards = ref<StrategyCardsDay | null>(null);
const limits = ref<LimitDetailDay | null>(null);
const errors = ref<Record<string, string>>({});
let timer: ReturnType<typeof setInterval> | undefined;

async function read<T>(key: string, path: string): Promise<T | null> {
  try {
    return await getJson<T>(path);
  } catch (error) {
    errors.value = { ...errors.value, [key]: error instanceof Error ? error.message : String(error) };
    return null;
  }
}

async function load() {
  loading.value = true;
  errors.value = {};
  const day = encodeURIComponent(tradeDate.value);
  const [radarDay, cardDay, limitDay] = await Promise.all([
    read<RadarDay>('radar', `/api/research/market/radar?trade_date=${day}`),
    read<StrategyCardsDay>('cards', `/api/research/strategy/cards?trade_date=${day}&per_line=${perLine.value}`),
    read<LimitDetailDay>('limits', `/api/research/market/limit-detail?trade_date=${day}`),
  ]);
  radar.value = radarDay;
  cards.value = cardDay;
  limits.value = limitDay;
  loading.value = false;
}

const rows = computed(() => radarRows(radar.value, band.value, segment.value));
const legend = computed(() => radarLegend(rows.value));
const option = computed(() => radarOption(rows.value, BAND_LABELS[band.value] ?? band.value));
const auction = computed(() => auctionRows(radar.value));
const bands = computed(() => {
  const point = radar.value?.points?.find((item) => item.bands);
  return Object.keys(point?.bands ?? { 2: null, 5: null });
});
const latestFlow = computed(() => radar.value?.main_net?.[radar.value.main_net.length - 1]);
const lastPhase = computed(() => {
  const points = radar.value?.points ?? [];
  const phase = points[points.length - 1]?.phase;
  return phase ? PHASE_LABELS[phase] ?? phase : '-';
});
const gate = computed(() => cards.value?.direction_gate);
const gateType = computed(() => ({ up: 'danger', down: 'success', mixed: 'warning' } as Record<string, string>)[gate.value?.label ?? ''] ?? 'info');

function ticketRow({ row }: { row: CardPick }): string {
  return row.ticket ? 'ticket-row' : '';
}

function eventText(pick: CardPick): string {
  return (pick.events ?? []).map((event) => event.label).join('；');
}

function schedule() {
  if (timer) clearInterval(timer);
  timer = undefined;
  if (autoRefresh.value && tradeDate.value === shanghaiToday()) timer = setInterval(load, 60_000);
}

watch([tradeDate, perLine], () => { void load(); schedule(); });
watch(autoRefresh, schedule);
onMounted(() => { void load(); schedule(); });
onBeforeUnmount(() => { if (timer) clearInterval(timer); });
</script>

<template>
  <el-alert type="info" :closable="false" show-icon
    title="盘面雷达复原自“情绪·系统雷达”：曾触及 +x%/-x% 的股票按首次方向只进不出，累计其当日成交额；中间带为全池轧差。战法卡片的每一路是我们台账里的一条策略线。全部为研究证据，不生成订单。" />
  <el-card shadow="never" class="section-gap">
    <template #header>
      <div class="card-header">
        <el-space wrap>
          <el-date-picker v-model="tradeDate" type="date" value-format="YYYY-MM-DD" :clearable="false" style="width: 150px" />
          <el-radio-group v-model="band" size="small">
            <el-radio-button v-for="key in bands" :key="key" :value="key">{{ BAND_LABELS[key] ?? key }}</el-radio-button>
          </el-radio-group>
          <el-select v-model="segment" size="small" style="width: 110px">
            <el-option v-for="(label, key) in SEGMENT_LABELS" :key="key" :label="label" :value="key" />
          </el-select>
          <el-switch v-model="autoRefresh" active-text="每分钟刷新" />
        </el-space>
        <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
      </div>
    </template>
    <el-alert v-if="errors.radar" :title="`雷达读取失败：${errors.radar}`" type="error" :closable="false" show-icon />
    <div class="radar-legend">
      <span class="tone-down">曾≤-{{ BAND_LABELS[band] }} {{ legend.cumDown ?? '-' }}</span>
      <span class="tone-up">曾≥+{{ BAND_LABELS[band] }} {{ legend.cumUp ?? '-' }}</span>
      <span>中间带 {{ legend.middle ?? '-' }}</span>
      <span>全池 {{ legend.pool ?? '-' }}</span>
      <span :class="`tone-${tone(latestFlow?.main_net)}`">主力净额 {{ yi(latestFlow?.main_net) }}</span>
      <el-text type="info" size="small">此刻在上方 {{ legend.nowUp ?? '-' }} / 下方 {{ legend.nowDown ?? '-' }} · {{ legend.time ?? '-' }} · {{ lastPhase }}</el-text>
    </div>
    <div class="radar-counts"><el-text size="small">{{ bandCounts(rows, band) }}</el-text><el-text size="small" type="info">{{ breadthText(rows) }}</el-text></div>
    <el-text v-if="latestFlow" type="info" size="small">主力净额来源：{{ latestFlow.upstream ?? latestFlow.source ?? '-' }}（{{ latestFlow.boards }} 个行业板块，{{ shanghaiTime(latestFlow.observed_at) }}）；分板块视图不拆分主力净额。</el-text>
    <v-chart v-if="rows.length" :option="option" autoresize class="radar-chart" />
    <el-empty v-else description="该日尚无雷达点（竞价撮合前只统计虚拟价分布）" :image-size="56" />
    <el-table v-if="auction.length" :data="auction" size="small" max-height="220" class="section-gap">
      <el-table-column prop="time" label="竞价时刻" width="90" />
      <el-table-column prop="phase" label="阶段" width="120" />
      <el-table-column prop="up2" label="虚拟价≥+2%" width="105" />
      <el-table-column prop="up5" label="≥+5%" width="80" />
      <el-table-column prop="down2" label="≤-2%" width="80" />
      <el-table-column prop="down5" label="≤-5%" width="80" />
      <el-table-column prop="priced" label="有价股票" />
    </el-table>
  </el-card>

  <el-card shadow="never" class="section-gap">
    <template #header>
      <div class="card-header">
        <el-space wrap>
          <strong>战法卡片</strong>
          <el-tag :type="gateType">总方向 {{ GATE_LABELS[gate?.label ?? 'unknown'] }}</el-tag>
          <el-text type="info" size="small">上/下比 {{ gate?.up_down_ratio ?? '-' }} · 净额 {{ yi(gate?.main_net) }} · 昨收格局 {{ cards?.previous_close_regime?.regime_label ?? '-' }} · 选股日 {{ cards?.picks_as_of ?? '-' }}</el-text>
        </el-space>
        <el-space>
          <el-text size="small">每路显示</el-text>
          <el-input-number v-model="perLine" :min="1" :max="30" size="small" controls-position="right" style="width: 90px" />
        </el-space>
      </div>
    </template>
    <el-alert v-if="errors.cards" :title="`战法卡片读取失败：${errors.cards}`" type="error" :closable="false" show-icon />
    <el-alert v-else-if="cards && cards.status !== 'completed'" :title="cards.reason ?? '该日没有台账候选'" type="warning" :closable="false" show-icon />
    <el-row :gutter="12">
      <el-col v-for="card in cards?.cards ?? []" :key="card.strategy_key" :xl="8" :lg="12" :xs="24" class="section-gap">
        <el-card shadow="hover" class="strategy-card">
          <template #header>
            <div class="card-header">
              <el-space wrap>
                <strong>{{ card.name }}</strong>
                <el-tag size="small">{{ card.style }}</el-tag>
                <el-tag v-if="card.direction === 'short'" size="small" type="success">回避方向</el-tag>
                <el-tag size="small" type="info">{{ card.maturity }}</el-tag>
              </el-space>
              <span :class="['card-return', `tone-${tone(card.summary.mean_since_open_pct)}`]">{{ pctText(card.summary.mean_since_open_pct) }}</span>
            </div>
          </template>
          <el-text size="small" type="info">
            开盘起均值（{{ card.summary.scored }}/{{ card.summary.picks }}）· 上涨占比 {{ card.summary.rose_share === null ? '-' : `${Math.round(card.summary.rose_share * 100)}%` }}
            · 出票 {{ card.summary.tickets }} · 近5日 {{ lineRankText(card.leaderboard['5']) }} · 近20日 {{ lineRankText(card.leaderboard['20']) }}
          </el-text>
          <el-table :data="card.picks" size="small" class="section-gap" :row-class-name="ticketRow">
            <el-table-column label="股票" min-width="96">
              <template #default="{ row }"><div>{{ row.name ?? row.symbol }}</div><el-text size="small" type="info">{{ row.symbol }}</el-text></template>
            </el-table-column>
            <el-table-column label="开盘起" width="84">
              <template #default="{ row }"><strong :class="`tone-${tone(row.since_open_pct)}`">{{ pctText(row.since_open_pct) }}</strong><el-tag v-if="row.opened_at_limit_up" size="small" type="info">一字</el-tag></template>
            </el-table-column>
            <el-table-column label="竞价/开/现" width="128">
              <template #default="{ row }">
                <span :class="`tone-${tone(row.auction?.pct_change)}`">{{ pctText(row.auction?.pct_change, 1) }}</span> /
                <span :class="`tone-${tone(row.open_pct)}`">{{ pctText(row.open_pct, 1) }}</span> /
                <span :class="`tone-${tone(row.latest_pct)}`">{{ pctText(row.latest_pct, 1) }}</span>
                <div><el-text size="small" type="info">竞价额 {{ yi(row.auction?.turnover, 2) }}</el-text></div>
              </template>
            </el-table-column>
            <el-table-column label="题材 · 事件" min-width="130">
              <template #default="{ row }">
                <el-space wrap :size="2">
                  <el-tag v-for="theme in row.themes ?? []" :key="theme.code" size="small" effect="plain">{{ theme.label }}</el-tag>
                </el-space>
                <el-tooltip v-if="row.event_count" :content="eventText(row)" placement="top">
                  <div class="event-stars">{{ stars(row.event_count) }}</div>
                </el-tooltip>
              </template>
            </el-table-column>
            <el-table-column label="共振/出票" width="86">
              <template #default="{ row }">
                <el-tooltip :content="(row.resonance_lines ?? []).join('、')" placement="top"><el-tag size="small" :type="(row.resonance ?? 0) > 1 ? 'danger' : 'info'">共振×{{ row.resonance ?? 0 }}</el-tag></el-tooltip>
                <el-tag v-if="row.ticket" size="small" type="danger" effect="dark">出票</el-tag>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
    </el-row>
    <el-collapse v-if="cards?.definitions" class="section-gap">
      <el-collapse-item title="口径说明" name="definitions">
        <el-descriptions :column="1" size="small" border>
          <el-descriptions-item v-for="(text, key) in cards.definitions" :key="key" :label="String(key)">{{ text }}</el-descriptions-item>
          <el-descriptions-item label="direction_gate">{{ gate?.definition ?? '-' }}</el-descriptions-item>
        </el-descriptions>
      </el-collapse-item>
    </el-collapse>
  </el-card>

  <el-card shadow="never" class="section-gap">
    <template #header>
      <div class="card-header">
        <el-space wrap>
          <strong>涨停明细（开盘啦复盘为主，选股宝对照）</strong>
          <el-text type="info" size="small">开盘啦 {{ limits?.coverage?.longhu_review ?? 0 }} · 选股宝 {{ limits?.coverage?.xuangubao_limit_up ?? 0 }} · 两边都有 {{ limits?.coverage?.both ?? 0 }} · 炸板 {{ limits?.broken?.length ?? 0 }} · 跌停 {{ limits?.limit_down?.length ?? 0 }}</el-text>
        </el-space>
      </div>
    </template>
    <el-alert v-if="errors.limits" :title="`涨停明细读取失败：${errors.limits}`" type="error" :closable="false" show-icon />
    <el-empty v-else-if="!limits?.stocks?.length" description="该日涨停复盘尚未归档（收盘后补充采集写入）" :image-size="56" />
    <el-table v-else :data="limits.stocks" size="small" max-height="420">
      <el-table-column label="股票" min-width="100" fixed>
        <template #default="{ row }"><div>{{ row.name ?? row.symbol }}</div><el-text size="small" type="info">{{ row.symbol }}</el-text></template>
      </el-table-column>
      <el-table-column label="首封/末封" width="120">
        <template #default="{ row }">{{ row.first_limit_up_at ? shanghaiTime(row.first_limit_up_at) : '-' }} / {{ row.last_limit_up_at ? shanghaiTime(row.last_limit_up_at) : '-' }}</template>
      </el-table-column>
      <el-table-column label="开板" width="60"><template #default="{ row }">{{ row.break_times ?? '-' }}</template></el-table-column>
      <el-table-column label="一字" width="60"><template #default="{ row }">{{ row.one_word === null || row.one_word === undefined ? '-' : row.one_word ? '是' : '否' }}</template></el-table-column>
      <el-table-column label="连板" width="80"><template #default="{ row }">{{ boardText(row) }}</template></el-table-column>
      <el-table-column label="封单额" width="90"><template #default="{ row }">{{ yi(row.seal_amount, 2) }}</template></el-table-column>
      <el-table-column label="主力净额" width="90"><template #default="{ row }"><span :class="`tone-${tone(row.main_net)}`">{{ yi(row.main_net, 2) }}</span></template></el-table-column>
      <el-table-column label="成交额" width="90"><template #default="{ row }">{{ yi(row.turnover, 2) }}</template></el-table-column>
      <el-table-column prop="theme" label="题材" width="110" show-overflow-tooltip />
      <el-table-column prop="reason" label="涨停原因" min-width="220" show-overflow-tooltip />
      <el-table-column label="来源核对" width="120">
        <template #default="{ row }">
          <el-tag size="small" :type="row.sources.length === 2 ? 'success' : 'warning'">{{ row.sources.length === 2 ? '双源' : row.sources[0] }}</el-tag>
          <el-text v-if="row.agreement?.first_seal_gap_seconds" size="small" type="info"> 差{{ row.agreement.first_seal_gap_seconds }}s</el-text>
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>

<style scoped>
.radar-legend { display: flex; flex-wrap: wrap; gap: 16px; align-items: baseline; margin: 6px 0; font-weight: 600; }
.radar-chart { height: 380px; width: 100%; }
.radar-counts { display: flex; flex-wrap: wrap; gap: 16px; margin-bottom: 4px; }
.tone-up { color: #d93026; }
.tone-down { color: #188038; }
.card-return { font-size: 22px; font-weight: 700; }
.event-stars { color: #e6a23c; letter-spacing: 1px; }
.strategy-card :deep(.ticket-row) { background: #fff4f2; }
</style>
