<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { Refresh } from '@element-plus/icons-vue';
import VChart from 'vue-echarts';
import { getJson } from '../../api/http';
import {
  BAND_LABELS, GATE_LABELS, HEALTH_LABELS, HEALTH_TYPES, SEGMENT_LABELS, auctionRows, bandCounts, boardText,
  breadthText, failingChecks, fieldHealthText, lineRankText,
  pctText, radarHeadline, radarOption, radarRows, shanghaiTime, shanghaiToday, stars, tone, yi,
  type CardPick, type IndicatorHealth, type LimitDetailDay, type RadarDay, type StrategyCardsDay, type StrategyTimingRow,
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
const health = ref<IndicatorHealth | null>(null);
const errors = ref<Record<string, string>>({});
let timer: ReturnType<typeof setInterval> | undefined;
let readVersion = 0;

async function read<T>(key: string, path: string, version: number): Promise<T | null> {
  try {
    const result = await getJson<T>(path);
    if (version === readVersion) {
      const next = { ...errors.value };
      delete next[key];
      errors.value = next;
    }
    return result;
  } catch (error) {
    if (version === readVersion) {
      errors.value = { ...errors.value, [key]: error instanceof Error ? error.message : String(error) };
    }
    return null;
  }
}

function dayParam(): string {
  return encodeURIComponent(tradeDate.value);
}

async function loadRadar(version: number) {
  const segments = segment.value === 'all' ? '' : '&segments=true';
  const radarDay = await read<RadarDay>('radar', `/api/research/market/radar?trade_date=${dayParam()}${segments}`, version);
  if (version === readVersion) radar.value = radarDay;
}

async function loadCardsAndHealth(version: number) {
  const [cardDay, healthDay] = await Promise.all([
    read<StrategyCardsDay>('cards', `/api/research/strategy/cards?trade_date=${dayParam()}&per_line=${perLine.value}`, version),
    read<IndicatorHealth>('health', `/api/research/indicators/health?trade_date=${dayParam()}`, version),
  ]);
  if (version === readVersion) {
    cards.value = cardDay;
    health.value = healthDay;
  }
}

// Everything at once on open, a date change or the refresh button.
async function load() {
  const version = ++readVersion;
  loading.value = true;
  errors.value = {};
  const [, , limitDay] = await Promise.all([
    loadRadar(version), loadCardsAndHealth(version),
    read<LimitDetailDay>('limits', `/api/research/market/limit-detail?trade_date=${dayParam()}`, version),
  ]);
  if (version === readVersion) {
    limits.value = limitDay;
    loading.value = false;
  }
}

// The owner has enough capacity for the three independent read paths to run in
// parallel. Every minute gets a fresh radar, strategy-card projection and health
// snapshot; limit detail remains an explicit post-close read.
async function autoRefresh_() {
  if (loading.value) return;
  const version = ++readVersion;
  await Promise.all([loadRadar(version), loadCardsAndHealth(version)]);
}

const rows = computed(() => radarRows(radar.value, band.value, segment.value));
const headline = computed(() => radarHeadline(rows.value));
const option = computed(() => radarOption(rows.value, BAND_LABELS[band.value] ?? band.value));
const auction = computed(() => auctionRows(radar.value));
const bands = computed(() => {
  const point = radar.value?.points?.find((item) => item.bands);
  return Object.keys(point?.bands ?? { 2: null, 5: null });
});
const latestFlow = computed(() => radar.value?.main_net?.[radar.value.main_net.length - 1]);
const gate = computed(() => cards.value?.direction_gate);
const gateType = computed(() => ({ up: 'danger', down: 'success', mixed: 'warning' } as Record<string, string>)[gate.value?.label ?? ''] ?? 'info');
const bandMeaning = computed(() => (BAND_LABELS[band.value] ?? band.value).replace(/^±/, ''));
const cardsFreshness = computed(() => cards.value?.freshness);
const freshnessType = (status: string | undefined): string => ({ fresh: 'success', available: 'success', historical_available: 'info', stale: 'warning', missing: 'danger' }[status ?? ''] ?? 'info');
const freshnessLabel = (status: string | undefined): string => ({ fresh: '实时', available: '当日可用', historical_available: '历史可用', stale: '已变旧', missing: '缺失' }[status ?? ''] ?? '未知');
const sourceTime = (key: string): string => {
  const value = cardsFreshness.value?.live_sources?.[key]?.latest_observed_at;
  return value ? shanghaiTime(value) : '-';
};

function deltaText(value: number | null): string {
  return value === null ? '-' : `${value >= 0 ? '+' : ''}${yi(value)}`;
}

function ticketRow({ row }: { row: CardPick }): string {
  return row.ticket ? 'ticket-row' : '';
}

function eventText(pick: CardPick): string {
  return (pick.events ?? []).map((event) => event.label).join('；');
}

const PHASE_LABELS: Record<string, string> = {
  auction: '竞价', intraday: '盘中', near_close: '尾盘', post_close: '盘后',
};

function phaseText(phase: string): string {
  return PHASE_LABELS[phase] ?? phase;
}

function timingSymbols(row: StrategyTimingRow): string {
  return row.candidates.map((candidate) => candidate.name ? `${candidate.name}（${candidate.symbol}）` : candidate.symbol).join('、') || '等待候选';
}

function timingReasons(row: StrategyTimingRow): string {
  return row.candidates.flatMap((candidate) => candidate.selection_reasons).slice(0, 4).join('；') || row.timing_plans[0]?.why || '-';
}

function schedule() {
  if (timer) clearInterval(timer);
  timer = undefined;
  if (autoRefresh.value && tradeDate.value === shanghaiToday()) timer = setInterval(autoRefresh_, 60_000);
}

watch([tradeDate, perLine], () => { void load(); schedule(); });
watch(segment, (value, previous) => { if (value === 'all' || previous === 'all') void load(); });
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
          <el-switch v-model="autoRefresh" active-text="自动刷新（雷达、策略卡片与健康每分钟）" />
        </el-space>
        <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
      </div>
    </template>
    <el-alert v-if="errors.radar" :title="`雷达读取失败：${errors.radar}`" type="error" :closable="false" show-icon />
    <div class="radar-title-row">
      <strong>主动买/卖·主力净额</strong>
      <el-text type="info" size="small">2 分钟一点 · 9:25 为竞价撮合 · 主动卖 = 当日曾跌超 {{ bandMeaning }} 的股票成交额 · 主动买 = 当日曾涨超 {{ bandMeaning }} 的股票成交额</el-text>
    </div>
    <div v-if="rows.length" class="radar-visual">
      <div class="radar-headline">
        <div class="radar-headline-main">
          <span class="tone-down">卖 {{ yi(headline.cumDown) }}</span>
          <span class="tone-up">买 {{ yi(headline.cumUp) }}</span>
          <span :class="['radar-headline-net', `tone-${tone(headline.mainNet)}`]">主力净额 {{ yi(headline.mainNet) }}</span>
        </div>
        <el-text type="info" size="small">较上一点 卖 {{ deltaText(headline.downDelta) }} · 买 {{ deltaText(headline.upDelta) }}</el-text>
        <el-text v-if="headline.auctionUp !== null && headline.auctionDown !== null" type="info" size="small">竞买 {{ yi(headline.auctionUp) }} · 竞砸 {{ yi(headline.auctionDown) }}</el-text>
      </div>
      <v-chart :option="option" autoresize class="radar-chart" />
    </div>
    <el-empty v-else description="该日尚无雷达点（竞价撮合前只统计虚拟价分布）" :image-size="56" />
    <div class="radar-counts"><el-text size="small">{{ bandCounts(rows, band) }}</el-text><el-text size="small" type="info">{{ breadthText(rows) }}</el-text></div>
    <el-text v-if="latestFlow" type="info" size="small">主力净额来源：{{ latestFlow.upstream ?? latestFlow.source ?? '-' }}（{{ latestFlow.boards }} 个行业板块，{{ shanghaiTime(latestFlow.observed_at) }}）；分板块视图不拆分主力净额。超过 3 分钟没有新的行业排行时这条线断开，不沿用旧值。</el-text>
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
          <el-tooltip :content="fieldHealthText(cards)" placement="top">
            <el-tag :type="cards?.data_health?.all_eligible ? 'success' : 'warning'">数据{{ cards?.data_health?.all_eligible ? '可作决策' : '未全部就绪' }}</el-tag>
          </el-tooltip>
          <el-tag size="small" :type="freshnessType(cardsFreshness?.overall)">实时源 {{ freshnessLabel(cardsFreshness?.overall) }}</el-tag>
          <el-text type="info" size="small">上/下比 {{ gate?.up_down_ratio ?? '-' }} · 净额 {{ yi(gate?.main_net) }} · 昨收格局 {{ cards?.previous_close_regime?.regime_label ?? '-' }} · 台账基线 {{ cardsFreshness?.ledger_baseline?.as_of_date ?? cards?.picks_as_of ?? '-' }}</el-text>
          <el-text type="info" size="small">读取 {{ cardsFreshness?.read_at ? `${shanghaiTime(cardsFreshness.read_at)}；竞价 ${sourceTime('auction_open_desk_daily')}；盘中信号 ${sourceTime('intraday_signal_events')}` : '-' }}</el-text>
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
    <el-card shadow="never" class="section-gap" header="分时策略候选 · 买点假设与理由">
      <el-alert type="info" :closable="false" show-icon
        :title="`当前阶段：${phaseText(cards?.timing_board?.active_phase ?? '-') }。候选是研究样本，不是订单；缺失证据会留给盘后复盘。`" />
      <el-table :data="cards?.timing_board?.strategies ?? []" size="small" class="section-gap" max-height="620">
        <el-table-column label="策略" min-width="170">
          <template #default="{ row }"><div>{{ row.name }}</div><el-text size="small" type="info">{{ row.strategy_key }} · {{ row.maturity }}</el-text></template>
        </el-table-column>
        <el-table-column label="阶段/状态" width="135">
          <template #default="{ row }"><el-tag size="small" :type="row.status === 'candidates_available' ? 'success' : 'info'">{{ phaseText(row.active_phase) }}</el-tag><div><el-text size="small" type="info">{{ row.status === 'candidates_available' ? `${row.candidates.length} 个候选` : '等待候选' }}</el-text></div></template>
        </el-table-column>
        <el-table-column label="候选票" min-width="210"><template #default="{ row }">{{ timingSymbols(row) }}</template></el-table-column>
        <el-table-column label="参考买点" min-width="300">
          <template #default="{ row }"><div v-for="candidate in row.candidates" :key="`${row.strategy_key}-${candidate.symbol}`"><el-tag size="small" type="warning">{{ phaseText(candidate.phase) }} {{ candidate.window }}</el-tag> {{ candidate.buy_point }}<span v-if="candidate.missing_evidence.length" class="missing-evidence">（缺：{{ candidate.missing_evidence.join('、') }}）</span></div><span v-if="!row.candidates.length">{{ row.timing_plans[0]?.buy_point ?? '-' }}</span></template>
        </el-table-column>
        <el-table-column label="为什么选" min-width="250" show-overflow-tooltip><template #default="{ row }">{{ timingReasons(row) }}</template></el-table-column>
      </el-table>
      <el-text v-if="cards?.timing_board?.evening_review?.instruction" type="info" size="small">{{ cards.timing_board.evening_review.instruction }}</el-text>
    </el-card>
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
          <strong>指标健康</strong>
          <el-tag v-for="(count, status) in health?.summary ?? {}" :key="status" size="small" :type="HEALTH_TYPES[status] ?? 'info'">{{ HEALTH_LABELS[status] ?? status }} {{ count }}</el-tag>
        </el-space>
        <el-text type="info" size="small">{{ health?.rule ?? '' }}</el-text>
      </div>
    </template>
    <el-alert v-if="errors.health" :title="`指标健康读取失败：${errors.health}`" type="error" :closable="false" show-icon />
    <el-table v-else :data="health?.indicators ?? []" size="small">
      <el-table-column prop="label" label="指标" min-width="190" />
      <el-table-column prop="key" label="键" width="170" />
      <el-table-column label="状态" width="90">
        <template #default="{ row }"><el-tag size="small" :type="HEALTH_TYPES[row.status] ?? 'info'">{{ HEALTH_LABELS[row.status] ?? row.status }}</el-tag></template>
      </el-table-column>
      <el-table-column label="可作决策输入" width="110"><template #default="{ row }">{{ row.decision_eligible ? '是' : '否' }}</template></el-table-column>
      <el-table-column label="未通过的检查" min-width="300" show-overflow-tooltip>
        <template #default="{ row }">{{ failingChecks(row) || '—' }}</template>
      </el-table-column>
    </el-table>
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
.radar-title-row { display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: baseline; margin: 8px 0 12px; }
.radar-title-row strong { font-size: 18px; }
.radar-visual { display: grid; grid-template-columns: minmax(180px, 230px) minmax(0, 1fr); align-items: center; }
.radar-headline { display: flex; flex-direction: column; gap: 6px; }
.radar-headline-main { display: flex; flex-direction: column; gap: 4px; font-size: 28px; font-weight: 700; line-height: 1.2; }
.radar-headline-main span { white-space: nowrap; }
.radar-headline-net { font-size: 20px; }
.radar-chart { height: 380px; width: 100%; min-width: 0; }
.radar-counts { display: flex; flex-wrap: wrap; gap: 16px; margin: 4px 0; }
.tone-up { color: #d93026; }
.tone-down { color: #188038; }
.card-return { font-size: 22px; font-weight: 700; }
.event-stars { color: #e6a23c; letter-spacing: 1px; }
.missing-evidence { color: #b88230; }
.strategy-card :deep(.ticket-row) { background: #fff4f2; }
@media (max-width: 720px) {
  .radar-visual { grid-template-columns: 1fr; }
  .radar-headline-main { flex-direction: row; gap: 24px; }
}
</style>
