<script setup lang="ts">
import { provide, ref } from 'vue';
import TargetRegistryPanel from '../components/targets/TargetRegistryPanel.vue';
import TargetGroupCreatePanel from '../components/targets/TargetGroupCreatePanel.vue';
import TargetMembersPanel from '../components/targets/TargetMembersPanel.vue';
import MembershipPanel from '../components/targets/MembershipPanel.vue';
import DeliveryPanel from '../components/targets/DeliveryPanel.vue';
import UrgentAlertsPanel from '../components/targets/UrgentAlertsPanel.vue';
import { targetGroupsContextKey } from '../dashboard-context';
import type { TargetRegistryRow } from '../api/target-registry';

const selectedTarget = ref<TargetRegistryRow | null>(null);
provide(targetGroupsContextKey, { selectedTarget });
</script>

<template>
  <TargetRegistryPanel />
  <TargetGroupCreatePanel />
  <TargetMembersPanel />
  <MembershipPanel />
  <DeliveryPanel />
  <UrgentAlertsPanel />
</template>
