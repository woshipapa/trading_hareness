<script setup lang="ts">
import { defineAsyncComponent } from 'vue';
import { DataAnalysis, Refresh, Wallet } from '@element-plus/icons-vue';
import { useDashboardWorkspace } from './composables/useDashboardWorkspace';

const dashboard = useDashboardWorkspace();
const ResearchOverviewTab = defineAsyncComponent(() => import('./views/research/ResearchOverviewTab.vue'));
const MarketSnapshotsTab = defineAsyncComponent(() => import('./views/research/MarketSnapshotsTab.vue'));
const CloseReviewTab = defineAsyncComponent(() => import('./views/research/CloseReviewTab.vue'));
const SessionRadarTab = defineAsyncComponent(() => import('./views/research/SessionRadarTab.vue'));
const SentimentTemperatureTab = defineAsyncComponent(() => import('./views/research/SentimentTemperatureTab.vue'));
const StrategyTab = defineAsyncComponent(() => import('./views/research/StrategyTab.vue'));
const FactorLabTab = defineAsyncComponent(() => import('./views/research/FactorLabTab.vue'));
const StockStudyTab = defineAsyncComponent(() => import('./views/research/StockStudyTab.vue'));
const AnalystEvidenceTab = defineAsyncComponent(() => import('./views/research/AnalystEvidenceTab.vue'));
const ClaimReviewTab = defineAsyncComponent(() => import('./views/research/ClaimReviewTab.vue'));
const ProviderTab = defineAsyncComponent(() => import('./views/research/ProviderTab.vue'));
const QualityTab = defineAsyncComponent(() => import('./views/research/QualityTab.vue'));
const RemoteRealtimeTab = defineAsyncComponent(() => import('./views/research/RemoteRealtimeTab.vue'));
const PersonalDecisionView = defineAsyncComponent(() => import('./views/PersonalDecisionView.vue'));
</script>

<template>
  <el-container class="app-shell">
    <el-aside width="236px" class="side-nav">
      <div class="brand"><el-icon><DataAnalysis /></el-icon><div><strong>Quant Research</strong><span>投研与市场数据</span></div></div>
      <el-menu :default-active="dashboard.activeSection" class="menu" @select="dashboard.selectActiveSection">
        <el-menu-item index="research"><el-icon><DataAnalysis /></el-icon><span>量化研究台</span></el-menu-item>
        <el-menu-item index="personal"><el-icon><Wallet /></el-icon><span>个人决策</span></el-menu-item>
      </el-menu>
      <div class="side-state"><el-tag type="info" effect="plain">研究服务</el-tag></div>
    </el-aside>
    <el-container>
      <el-header class="topbar"><div><h1>{{ dashboard.activeSection === 'research' ? '量化研究台' : '个人决策' }}</h1><span>{{ dashboard.activeSection === 'research' ? '分析师证据、市场数据与研究候选池' : '实际持仓、市场判断与可执行的新买计划' }}</span></div><el-button v-if="dashboard.activeSection !== 'personal'" :icon="Refresh" :loading="dashboard.loading" @click="dashboard.loadResearch()">刷新数据</el-button></el-header>
      <el-main class="content">
        <template v-if="dashboard.activeSection === 'research'">
          <el-alert v-if="dashboard.researchError" :title="dashboard.researchError" type="error" show-icon :closable="false" class="section-gap" />
          <el-tabs v-model="dashboard.activeResearchTab" class="research-tabs">
            <el-tab-pane label="研究概览" name="overview">
              <ResearchOverviewTab />
            </el-tab-pane>
            <el-tab-pane label="全市场快照" name="market-snapshots">
              <MarketSnapshotsTab />
            </el-tab-pane>
            <el-tab-pane label="盘面雷达与战法" name="session-radar" lazy>
              <SessionRadarTab />
            </el-tab-pane>
            <el-tab-pane label="情绪温度" name="sentiment-temperature" lazy>
              <SentimentTemperatureTab />
            </el-tab-pane>
            <el-tab-pane label="收盘复盘" name="close-review">
              <CloseReviewTab />
            </el-tab-pane>
            <el-tab-pane label="策略与股票池" name="strategy">
              <StrategyTab />
            </el-tab-pane>
            <el-tab-pane label="远端实时" name="realtime">
              <RemoteRealtimeTab />
            </el-tab-pane>
            <el-tab-pane label="因子与回测" name="factor-lab">
              <FactorLabTab />
            </el-tab-pane>
            <el-tab-pane label="个股研究" name="stock-study">
              <StockStudyTab />
            </el-tab-pane>
            <el-tab-pane label="分析师证据" name="evidence">
              <AnalystEvidenceTab />
            </el-tab-pane>
            <el-tab-pane label="观点复核" name="claim-review">
              <ClaimReviewTab />
            </el-tab-pane>
            <el-tab-pane label="数据源 Doctor" name="providers">
              <ProviderTab />
            </el-tab-pane>
            <el-tab-pane label="质量与分钟数据" name="quality">
              <QualityTab />
            </el-tab-pane>
          </el-tabs>
        </template>
        <PersonalDecisionView v-else-if="dashboard.activeSection === 'personal'" />
      </el-main>
    </el-container>
  </el-container>
</template>
