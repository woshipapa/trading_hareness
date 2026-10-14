<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { Refresh } from '@element-plus/icons-vue';
import { getJson } from '../../api/http';
import { shanghaiToday } from '../../research/session-radar';
import {
  SIDE_NAMES, SLOT_NAMES, boardRows, fixed, labelName, labelTag, latestOnly, pairRows, percent, recordRows, summaryCaption, summarySections,
  type RecordRow, type TacticBoard, type TacticCases, type TacticSide, type TacticSummary,
} from '../../research/tactic-trials';

const SIDES: TacticSide[] = ['focus', 'hold', 'avoid'];
const tradeDate = ref(shanghaiToday());
const board = ref<TacticBoard | null>(null);
const summary = ref<TacticSummary | null>(null);
const cases = ref<TacticCases | null>(null);
const casesOpen = ref(false);
const casesLoading = ref(false);
const loading = ref(false);
const error = ref<string | null>(null);
// A slow answer to an earlier date or an earlier row must not replace a later one's.
const beginDay = latestOnly();
const beginCases = latestOnly();

async function loadDay(day: string | undefined, current: () => boolean) {
  // Literal paths: the relay route contract test reads each research path up to its '?'.
  const [boardValue, summaryValue] = await Promise.all(day
    ? [getJson<TacticBoard>(`/api/research/market/tactic-trials/board?date=${encodeURIComponent(day)}`),
       getJson<TacticSummary>(`/api/research/market/tactic-trials/summary?date=${encodeURIComponent(day)}`)]
    : [getJson<TacticBoard>('/api/research/market/tactic-trials/board'),
       getJson<TacticSummary>('/api/research/market/tactic-trials/summary')]);
  if (!current()) return;
  board.value = boardValue;
  summary.value = summaryValue;
  if (!day) tradeDate.value = boardValue.trade_date;
}

async function load(day?: string) {
  const current = beginDay();
  loading.value = true;
  error.value = null;
  try { await loadDay(day, current); }
  catch (cause) {
    if (current()) { board.value = null; summary.value = null; error.value = cause instanceof Error ? cause.message : String(cause); }
  }
  finally { if (current()) loading.value = false; }
}

async function openCases(row: RecordRow) {
  const current = beginCases();
  casesOpen.value = true;
  casesLoading.value = true;
  cases.value = null;
  try {
    const value = await getJson<TacticCases>(`/api/research/market/tactic-trials/cases?tactic=${encodeURIComponent(row.key)}&days=10`);
    if (current()) cases.value = value;
  }
  catch (cause) {
    if (current()) { error.value = cause instanceof Error ? cause.message : String(cause); casesOpen.value = false; }
  }
  finally { if (current()) casesLoading.value = false; }
}

onMounted(() => { void load(); });
watch(tradeDate, (day) => { if (board.value?.trade_date !== day) void load(day); });

const reading = computed(() => summary.value?.reading ?? null);
const sections = computed(() => summarySections(reading.value?.lines ?? []));
const records = computed(() => recordRows(reading.value, board.value?.tactics ?? []));
const combinations = computed(() => records.value.filter((row) => row.combination));
const singles = computed(() => records.value.filter((row) => !row.combination));
const auctionSides = computed(() => (board.value?.auction ? SIDES.map((side) => ({ side, rows: boardRows(board.value!.auction, side, board.value!) })) : []));
const closeSides = computed(() => (board.value?.close ? SIDES.map((side) => ({ side, rows: boardRows(board.value!.close, side, board.value!) })) : []));
</script>

<template>
  <section class="trials">
    <div class="toolbar">
      <el-date-picker v-model="tradeDate" type="date" value-format="YYYY-MM-DD" size="small" :clearable="false" />
      <el-button :icon="Refresh" :loading="loading" size="small" @click="load(tradeDate)">刷新</el-button>
      <el-tag type="warning" effect="plain">研究用途，不构成操作建议；战法是证据观察，不是策略</el-tag>
      <span class="caption">{{ board?.version ?? '-' }} · 标签截至 {{ board?.labels_as_of ?? '尚无总结' }}</span>
    </div>
    <el-alert v-if="error" :title="error" type="warning" show-icon :closable="false" class="gap" />

    <el-card shadow="never" class="gap">
      <template #header>
        {{ board?.trade_date ?? tradeDate }} 竞价看板
        <span v-if="board?.auction" class="caption"> · 定格于 {{ board.auction.decided_at.slice(11, 19) }} · 竞价池 {{ board.auction.rows }} 只</span>
      </template>
      <template v-if="board?.auction">
        <div v-for="group in auctionSides" :key="group.side" class="side">
          <div class="side-title">{{ SIDE_NAMES[group.side] }}（{{ group.rows.length }}）</div>
          <el-table v-if="group.rows.length" :data="group.rows" size="small" stripe>
            <el-table-column label="名称" min-width="96"><template #default="{ row }">{{ row.name ?? row.symbol }}</template></el-table-column>
            <el-table-column prop="boards" label="连板" width="64" />
            <el-table-column label="高开" width="84"><template #default="{ row }">{{ percent(row.gap) }}</template></el-table-column>
            <el-table-column label="竞价额排名" width="96"><template #default="{ row }">{{ row.auction_amount_rank ?? '—' }}</template></el-table-column>
            <el-table-column label="风险排名" width="84"><template #default="{ row }">{{ fixed(row.risk_rank, 1) }}</template></el-table-column>
            <el-table-column label="选中它的战法" min-width="220">
              <template #default="{ row }">
                <el-tag v-for="item in row.picked_by" :key="item.key" :type="labelTag(item.label)" size="small" class="tag">{{ item.name }} · {{ labelName(item.label) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="满足的条件" min-width="220"><template #default="{ row }"><span class="caption">{{ row.conditions.join('、') || '—' }}</span></template></el-table-column>
          </el-table>
          <div v-else class="caption">没有战法选中</div>
        </div>
      </template>
      <el-empty v-else description="该日期没有竞价读数（09:25 后生成）" />
    </el-card>

    <el-card v-if="board?.close" shadow="never" class="gap">
      <template #header>{{ board.close.trade_date }} 收盘选出（给持有者参考）<span class="caption"> · 涨停 {{ board.close.rows }} 只</span></template>
      <div v-for="group in closeSides" :key="group.side" class="side">
        <div class="side-title">{{ SIDE_NAMES[group.side] }}（{{ group.rows.length }}）</div>
        <el-table v-if="group.rows.length" :data="group.rows" size="small" stripe>
          <el-table-column label="名称" min-width="96"><template #default="{ row }">{{ row.name ?? row.symbol }}</template></el-table-column>
          <el-table-column prop="sector" label="题材" min-width="110" />
          <el-table-column prop="boards" label="连板" width="64" />
          <el-table-column prop="first_seal_time" label="首次封板" width="92" />
          <el-table-column label="风险排名" width="84"><template #default="{ row }">{{ fixed(row.risk_rank, 1) }}</template></el-table-column>
          <el-table-column label="选中它的战法" min-width="220">
            <template #default="{ row }">
              <el-tag v-for="item in row.picked_by" :key="item.key" :type="labelTag(item.label)" size="small" class="tag">{{ item.name }} · {{ labelName(item.label) }}</el-tag>
            </template>
          </el-table-column>
        </el-table>
        <div v-else class="caption">没有战法选中</div>
      </div>
    </el-card>

    <el-card shadow="never" class="gap">
      <template #header>{{ reading?.trade_date ?? tradeDate }} 每日总结<span class="caption"> · 固定模板生成，没有模型参与撰写</span></template>
      <div v-if="reading" class="caption">“当日”各节是最新有结果的选股日：{{ summaryCaption(reading) }}</div>
      <el-collapse v-if="sections.length">
        <el-collapse-item v-for="section in sections" :key="section.title" :title="section.title">
          <div v-for="(line, index) in section.lines" :key="index" class="line">{{ line }}</div>
        </el-collapse-item>
      </el-collapse>
      <el-empty v-else description="该日期及之前没有总结（收盘后生成）" />
    </el-card>

    <el-card v-if="reading" shadow="never" class="gap">
      <template #header>战法战绩<span class="caption"> · “回放”是样本内数字，只有“前向”决定标签；点一行看最近的选股日</span></template>
      <el-table :data="combinations" size="small" stripe @row-click="openCases">
        <el-table-column prop="name" label="战法" min-width="120" />
        <el-table-column label="时点" width="64"><template #default="{ row }">{{ SLOT_NAMES[row.slot as 'close' | 'auction'] }}</template></el-table-column>
        <el-table-column label="方向" width="84"><template #default="{ row }">{{ row.side ? SIDE_NAMES[row.side as TacticSide] : '—' }}</template></el-table-column>
        <el-table-column prop="primary_name" label="主结果" min-width="130" />
        <el-table-column label="回放（样本内）" min-width="170"><template #default="{ row }">{{ row.replay.days_with_picks }} 日 · 差 {{ percent(row.replay.mean_daily_difference) }} · t {{ fixed(row.replay.t) }}</template></el-table-column>
        <el-table-column label="前向" min-width="170"><template #default="{ row }">{{ row.forward.days_with_picks }} 日 / {{ row.forward.picks }} 选 · 差 {{ percent(row.forward.mean_daily_difference) }} · t {{ fixed(row.forward.t) }}</template></el-table-column>
        <el-table-column label="标签" width="96"><template #default="{ row }"><el-tag :type="labelTag(row.label)" size="small">{{ labelName(row.label) }}</el-tag></template></el-table-column>
        <el-table-column label="q" width="72"><template #default="{ row }">{{ fixed(row.q, 3) }}</template></el-table-column>
      </el-table>
    </el-card>

    <el-card v-if="reading" shadow="never" class="gap">
      <template #header>单个条件<span class="caption"> · 当日：选中与其余的差；战绩同上</span></template>
      <el-table :data="reading.conditions" size="small" stripe max-height="420">
        <el-table-column prop="name" label="条件" min-width="130" />
        <el-table-column prop="picks" label="当日选中" width="88" />
        <el-table-column label="选中均值" width="96"><template #default="{ row }">{{ percent(row.pick_mean) }}</template></el-table-column>
        <el-table-column label="其余均值" width="96"><template #default="{ row }">{{ percent(row.comparison_mean) }}</template></el-table-column>
        <el-table-column label="差" width="96"><template #default="{ row }">{{ percent(row.difference) }}</template></el-table-column>
        <el-table-column label="主张方向" width="88"><template #default="{ row }">{{ row.expects > 0 ? '更高' : '更低' }}</template></el-table-column>
      </el-table>
      <el-table :data="singles" size="small" stripe max-height="420" class="gap" @row-click="openCases">
        <el-table-column prop="name" label="条件（战绩）" min-width="130" />
        <el-table-column label="时点" width="64"><template #default="{ row }">{{ SLOT_NAMES[row.slot as 'close' | 'auction'] }}</template></el-table-column>
        <el-table-column prop="primary_name" label="主结果" min-width="130" />
        <el-table-column label="回放（样本内）" min-width="170"><template #default="{ row }">{{ row.replay.days_with_picks }} 日 · 差 {{ percent(row.replay.mean_daily_difference) }} · t {{ fixed(row.replay.t) }}</template></el-table-column>
        <el-table-column label="前向" min-width="170"><template #default="{ row }">{{ row.forward.days_with_picks }} 日 / {{ row.forward.picks }} 选 · 差 {{ percent(row.forward.mean_daily_difference) }} · t {{ fixed(row.forward.t) }}</template></el-table-column>
        <el-table-column label="标签" width="96"><template #default="{ row }"><el-tag :type="labelTag(row.label)" size="small">{{ labelName(row.label) }}</el-tag></template></el-table-column>
      </el-table>
    </el-card>

    <el-card v-if="reading" shadow="never" class="gap">
      <template #header>两两组合（挖掘所得，最近 {{ reading.pair_sessions }} 个交易日）<span class="caption"> · 多重比较下的描述性结果，不是战法</span></template>
      <div v-for="slot in (['close', 'auction'] as const)" :key="slot" class="side">
        <div class="side-title">{{ SLOT_NAMES[slot] }}：检验 {{ reading.pairs[slot].pairs_examined }} 对，达到 20 日门槛 {{ reading.pairs[slot].pairs_reaching_minimum }} 对</div>
        <el-table :data="pairRows(reading.pairs[slot])" size="small" stripe>
          <el-table-column prop="direction" label="方向" width="64" />
          <el-table-column label="组合" min-width="220"><template #default="{ row }">{{ row.names.join(' + ') }}</template></el-table-column>
          <el-table-column prop="days_with_picks" label="日数" width="72" />
          <el-table-column prop="picks" label="选中" width="72" />
          <el-table-column label="日均差" width="96"><template #default="{ row }">{{ percent(row.mean_daily_difference) }}</template></el-table-column>
          <el-table-column label="t" width="72"><template #default="{ row }">{{ fixed(row.t) }}</template></el-table-column>
        </el-table>
      </div>
    </el-card>

    <el-drawer v-model="casesOpen" :title="cases ? `${cases.tactic.name} · 最近 ${cases.sessions} 个交易日的选股日` : '选股日'" size="46%">
      <div v-loading="casesLoading">
        <template v-if="cases">
          <div class="caption">主结果：{{ cases.tactic.primary_name }} · {{ SLOT_NAMES[cases.tactic.slot] }} · {{ SIDE_NAMES[cases.tactic.side] }}</div>
          <el-empty v-if="!cases.days.length" description="这段时间没有选中任何股票" />
          <div v-for="day in cases.days" :key="day.trade_date" class="case">
            <div class="side-title">
              {{ day.trade_date }} <el-tag v-if="!day.settled" type="info" size="small">未结算完</el-tag>
              <span class="caption"> 选中均值 {{ percent(day.pick_mean) }} · 其余 {{ percent(day.comparison_mean) }} · 差 {{ percent(day.difference) }}</span>
            </div>
            <div class="line">
              <span v-for="pick in day.picks" :key="pick.symbol" class="pick">{{ pick.name ?? pick.symbol }}（{{ pick.boards ?? '—' }}板）{{ percent(pick.primary) }}</span>
            </div>
          </div>
        </template>
      </div>
    </el-drawer>
  </section>
</template>

<style scoped>
.toolbar { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; margin-bottom: 12px; }
.caption { color: #8a8f98; font-size: 12px; }
.gap { margin-top: 12px; }
.side { margin-bottom: 14px; }
.side-title { font-weight: 600; margin-bottom: 6px; }
.tag { margin: 0 6px 4px 0; }
.line { line-height: 1.9; font-size: 13px; }
.case { margin-top: 14px; }
.pick { display: inline-block; margin-right: 14px; }
</style>
