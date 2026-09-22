<script setup lang="ts">
import { defineAsyncComponent } from 'vue';
import { DataAnalysis, Refresh, Wallet } from '@element-plus/icons-vue';
import { useDashboardWorkspace } from './composables/useDashboardWorkspace';

const dashboard = useDashboardWorkspace();
const ResearchOverviewTab = defineAsyncComponent(() => import('./views/research/ResearchOverviewTab.vue'));
const MarketSnapshotsTab = defineAsyncComponent(() => import('./views/research/MarketSnapshotsTab.vue'));
const CloseReviewTab = defineAsyncComponent(() => import('./views/research/CloseReviewTab.vue'));
const StrategyTab = defineAsyncComponent(() => import('./views/research/StrategyTab.vue'));
const FactorLabTab = defineAsyncComponent(() => import('./views/research/FactorLabTab.vue'));
const StockStudyTab = defineAsyncComponent(() => import('./views/research/StockStudyTab.vue'));
const AnalystEvidenceTab = defineAsyncComponent(() => import('./views/research/AnalystEvidenceTab.vue'));
const ClaimReviewTab = defineAsyncComponent(() => import('./views/research/ClaimReviewTab.vue'));
const ProviderTab = defineAsyncComponent(() => import('./views/research/ProviderTab.vue'));
const CatalogTab = defineAsyncComponent(() => import('./views/research/CatalogTab.vue'));
const QualityTab = defineAsyncComponent(() => import('./views/research/QualityTab.vue'));
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
            <el-tab-pane label="收盘复盘" name="close-review">
              <CloseReviewTab />
            </el-tab-pane>
            <el-tab-pane label="策略与股票池" name="strategy">
              <StrategyTab />
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
            <el-tab-pane label="接口与原始数据" name="catalog">
              <CatalogTab />
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
  <el-dialog v-model="dashboard.fetchDialogOpen" title="受控数据读取" width="680px" destroy-on-close><el-form label-position="top"><el-row :gutter="14"><el-col :span="12"><el-form-item label="API"><el-input v-model="dashboard.fetchForm.api_name"/></el-form-item></el-col><el-col :span="12"><el-form-item label="来源"><el-select v-model="dashboard.fetchForm.provider" class="full-width"><el-option label="自动回退" value="auto"/><el-option label="主 Tushare 源" value="primary"/><el-option label="Super 聚合兼容路由" value="super"/><el-option label="Super SDK 完整路径" value="super_sdk"/><el-option label="Super GET 已验证路径" value="super_get"/><el-option label="REST 备用源" value="backup"/></el-select></el-form-item></el-col></el-row><el-alert title="完整 ths_member 请选自动、Super 聚合或 Super SDK；Super GET 对大板块会被上游截断。" type="warning" :closable="false" class="section-gap"/><el-form-item label="参数 JSON"><el-input v-model="dashboard.fetchForm.paramsText" type="textarea" :rows="8" class="mono"/></el-form-item><el-row :gutter="14"><el-col :span="16"><el-form-item label="字段"><el-input v-model="dashboard.fetchForm.fields"/></el-form-item></el-col><el-col :span="8"><el-form-item label="最大行数"><el-input-number v-model="dashboard.fetchForm.max_rows" :min="1" :max="10000" class="full-width"/></el-form-item></el-col></el-row></el-form><template #footer><el-button @click="dashboard.fetchDialogOpen = false">取消</el-button><el-button type="primary" :loading="dashboard.actionLoading === 'fetch'" @click="dashboard.executeFetch">读取并保存证据</el-button></template></el-dialog>
  <el-dialog v-model="dashboard.fetchResultOpen" title="读取结果" width="620px"><el-descriptions :column="2" border><el-descriptions-item v-for="(value, key) in dashboard.fetchResult" :key="String(key)" :label="String(key)"><span class="result-value">{{ typeof value === 'object' ? JSON.stringify(value) : value }}</span></el-descriptions-item></el-descriptions></el-dialog>
</template>
