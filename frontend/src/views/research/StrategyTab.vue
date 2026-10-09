<script lang="ts">
import { defineComponent, inject } from 'vue';
import { Refresh, WarningFilled } from '@element-plus/icons-vue';
import VChart from 'vue-echarts';
import { dashboardContextKey } from '../../dashboard-context';
import StrategyBoardPanel from '../../components/StrategyBoardPanel.vue';

export default defineComponent({
  name: 'StrategyTab',
  components: { Refresh, StrategyBoardPanel, VChart, WarningFilled },
  setup() {
    const dashboard = inject(dashboardContextKey);
    if (!dashboard) throw new Error('research tab requires the dashboard shell context');
    return dashboard as Record<string, any>;
  },
});
</script>

<template>

  <StrategyBoardPanel />
  <el-row :gutter="14">
    <el-col :md="9" :xs="24"><el-card shadow="never" header="核心股票池"><el-form label-position="top"><el-form-item label="股票代码"><el-input v-model="universeText" type="textarea" :rows="4" placeholder="000636.SZ, 603580.SH"/></el-form-item><el-form-item label="优先级"><el-input-number v-model="universePriority" :min="1" :max="10000"/></el-form-item><el-button type="primary" :loading="actionLoading === '更新核心股票池'" @click="saveUniverse">保存股票池</el-button></el-form><el-table :data="universe" size="small" max-height="250" class="section-gap"><el-table-column prop="symbol" label="代码"/><el-table-column prop="name" label="名称"/><el-table-column prop="priority" label="优先级" width="82"/></el-table></el-card></el-col>
    <el-col :md="15" :xs="24"><el-card shadow="never"><template #header><div class="card-header"><span>方向推荐</span><el-space><el-button :loading="actionLoading === '构建多源特征'" @click="runAction('构建多源特征','/api/research/features/build',{ universe_key: 'core' })">构建特征</el-button><el-button type="primary" :loading="actionLoading === '生成方向推荐'" @click="runAction('生成方向推荐','/api/research/recommendations/generate',{ universe_key: 'core', horizon_days: 20 })">生成推荐</el-button></el-space></div></template><el-alert title="推荐基于已落库的多源证据、技术趋势、资金流与已审核分析师观点；仅供研究，不自动下单。" type="info" :closable="false" show-icon/><el-table :data="recommendations" max-height="420" class="section-gap"><el-table-column prop="rank" label="#" width="52"/><el-table-column prop="symbol" label="标的"/><el-table-column label="方向" width="82"><template #default="{ row }"><el-tag :type="recommendationType(row.direction)">{{ recommendationDirection(row.direction) }}</el-tag></template></el-table-column><el-table-column prop="score" label="评分" width="75"/><el-table-column label="置信度" width="92"><template #default="{ row }">{{ row.confidence === undefined ? '-' : `${Math.round(row.confidence * 100)}%` }}</template></el-table-column><el-table-column prop="horizon_days" label="周期" width="72"/><el-table-column label="状态" width="110"><template #default="{ row }"><el-tag :type="row.decision === 'research_candidate' ? 'success' : row.decision === 'no_trade' ? 'danger' : 'info'">{{ row.decision }}</el-tag></template></el-table-column><el-table-column label="风险" min-width="160"><template #default="{ row }"><el-space wrap><el-tag v-for="flag in row.risk_flags ?? []" :key="flag" size="small" type="warning">{{ flag }}</el-tag></el-space></template></el-table-column><el-table-column label="联动" width="110" fixed="right"><template #default="{ row }"><el-button link type="primary" @click="openAnalystStockInTonghuashun(row.symbol)">同花顺K线</el-button></template></el-table-column></el-table></el-card></el-col>
  </el-row>
  <el-card shadow="never" class="section-gap" header="策略 × 日终复盘对照">
    <el-alert title="方向推荐是候选层，收盘复盘描述市场结构，日终摘要汇总盘中信号、成熟结果和验证门禁。交叉支持只表示同一标的有更多已落库证据，不会自动生成交易指令。" type="info" :closable="false" show-icon/>
    <el-descriptions :column="mobileLayout ? 1 : 5" border size="small" class="section-gap">
      <el-descriptions-item label="交易日">{{ strategyReviewContext.exchange_date }}</el-descriptions-item>
      <el-descriptions-item label="日终摘要投递">{{ strategyReviewContext.daily_delivery_status }}</el-descriptions-item>
      <el-descriptions-item label="收盘市场状态">{{ strategyReviewContext.market_state }}</el-descriptions-item>
      <el-descriptions-item label="盘后策略">{{ strategyReviewContext.post_close_status }}</el-descriptions-item>
      <el-descriptions-item label="学习门禁">{{ strategyReviewContext.validation_gate }}</el-descriptions-item>
    </el-descriptions>
    <el-text v-if="strategyReviewContext.post_close_reason" type="info">盘后说明：{{ strategyReviewContext.post_close_reason }}</el-text>
    <el-space v-if="strategyReviewContext.review_risk_flags.length" wrap class="section-gap"><el-text type="warning">复盘风险：</el-text><el-tag v-for="flag in strategyReviewContext.review_risk_flags" :key="flag" size="small" type="warning">{{ flag }}</el-tag></el-space>
    <el-table :data="strategyEvidenceMatrix" max-height="460" size="small" class="section-gap">
      <el-table-column prop="symbol" label="标的" width="108" fixed="left"/>
      <el-table-column prop="name" label="名称" width="100" show-overflow-tooltip/>
      <el-table-column label="证据覆盖" min-width="190"><template #default="{ row }"><el-space wrap><el-tag v-for="item in row.coverage" :key="item" size="small" type="info">{{ item }}</el-tag></el-space></template></el-table-column>
      <el-table-column label="组合结论" width="100"><template #default="{ row }"><el-tag :type="strategyEvidenceAlignmentType(row.alignment)">{{ row.alignment }}</el-tag></template></el-table-column>
      <el-table-column label="方向推荐" width="112"><template #default="{ row }">{{ row.recommendation ? `${row.recommendation.score ?? '-'} · ${recommendationDirection(row.recommendation.direction)}` : '-' }}</template></el-table-column>
      <el-table-column label="盘后候选" width="145"><template #default="{ row }">{{ row.post_close ? `${row.post_close.candidate_type ?? '候选'} · ${row.post_close.score ?? '-'}` : '-' }}</template></el-table-column>
      <el-table-column label="日终候选" width="105"><template #default="{ row }">{{ row.daily_candidate?.score ?? '-' }}</template></el-table-column>
      <el-table-column label="30m结果" width="150"><template #default="{ row }">{{ row.matured_30m_count }} 成熟 / {{ row.pending_30m_count }} 待定 · {{ outcomePercent(row.last_30m_return) }}</template></el-table-column>
      <el-table-column label="风险" min-width="150"><template #default="{ row }"><el-space wrap><el-tag v-for="flag in row.risk_flags" :key="flag" size="small" type="warning">{{ flag }}</el-tag><span v-if="!row.risk_flags.length">-</span></el-space></template></el-table-column>
      <el-table-column label="联动" width="160" fixed="right"><template #default="{ row }"><el-button link type="primary" @click="openStrategyEvidence(row.symbol)">看证据K线</el-button><el-button link type="primary" @click="openAnalystStockInTonghuashun(row.symbol)">同花顺</el-button></template></el-table-column>
    </el-table>
    <el-empty v-if="!strategyEvidenceMatrix.length" description="暂无可交叉对照的策略或日终证据" :image-size="64" />
  </el-card>
  <el-card shadow="never" header="特征证据"><el-table :data="featureItems" max-height="360"><el-table-column prop="symbol" label="标的" width="120"/><el-table-column prop="name" label="名称" width="120"/><el-table-column label="收盘" width="95"><template #default="{ row }">{{ displayValue(row.features.close) }}</template></el-table-column><el-table-column label="5日收益" width="110"><template #default="{ row }">{{ displayValue(row.features.return_5) }}</template></el-table-column><el-table-column label="20日收益" width="110"><template #default="{ row }">{{ displayValue(row.features.return_20) }}</template></el-table-column><el-table-column label="东财主力占比" width="130"><template #default="{ row }">{{ displayValue(featureRecord(row,'moneyflow_dc').net_amount_rate) }}</template></el-table-column><el-table-column label="分析师共识" width="125"><template #default="{ row }">{{ displayValue(featureRecord(row,'analyst').consensus) }}</template></el-table-column><el-table-column label="质量标记" min-width="180"><template #default="{ row }"><el-space wrap><el-tag v-for="flag in row.quality_flags" :key="flag" size="small" type="warning">{{ flag }}</el-tag></el-space></template></el-table-column></el-table></el-card>

</template>
