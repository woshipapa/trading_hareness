import { ref } from 'vue';
import { targetGroupsApi, type TargetGroupInvite } from '../api/target-groups';
import { useTargetRegistry } from './useTargetRegistry';

export function useTargetGroupCreate() {
  const registry = useTargetRegistry();
  const name = ref('');
  const keyword = ref('');
  const onlyOwnerPosts = ref(true);
  const submitting = ref(false);
  const error = ref('');
  const invite = ref<TargetGroupInvite | null>(null);
  const inviteTargetName = ref('');

  async function createGroup() {
    const groupName = name.value.trim();
    if (!groupName) {
      error.value = '请输入群名';
      return;
    }
    submitting.value = true;
    error.value = '';
    invite.value = null;
    try {
      const result = await targetGroupsApi.create({
        name: groupName,
        ...(keyword.value.trim() ? { keyword: keyword.value.trim() } : {}),
        only_owner_posts: onlyOwnerPosts.value,
      });
      invite.value = result.invite;
      inviteTargetName.value = result.target.name ?? groupName;
      await registry.load();
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : String(cause);
    } finally {
      submitting.value = false;
    }
  }

  async function regenerateInvite(chatId: string, targetName = '') {
    submitting.value = true;
    error.value = '';
    try {
      invite.value = (await targetGroupsApi.invite(chatId)).invite;
      inviteTargetName.value = targetName;
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : String(cause);
    } finally {
      submitting.value = false;
    }
  }

  return { name, keyword, onlyOwnerPosts, submitting, error, invite, inviteTargetName, createGroup, regenerateInvite };
}
