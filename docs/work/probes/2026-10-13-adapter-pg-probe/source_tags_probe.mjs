import { freshLedger, stepper, mod } from './lib.mjs';
const { createSourceTags } = await mod('source-tags.mjs');
const registry = { default_topic_key: 'general', routes: [{ tag: 'filetag', label: '文件标签', topic_key: 'general', publisher_key: 'filetag', remote_analyst_id: 'filetag', enabled: true }] };
const { ledger } = await freshLedger('probe_src', registry);   // ledger.init -> saveSourceProfile for file routes (3 INSERTs)
const S = stepper();
let tags = createSourceTags({ registry, ledger });
await S.step('load (only file profile stored, no dashboard origin)', async () => (await tags.load()).size);
await S.step('register new tag', async () => tags.register({ tag: '#NewTag', label: '新标签' }));
await S.step('register same tag again', async () => tags.register({ tag: 'newtag' }));
await S.step('disable dashboard tag', async () => tags.setEnabled('newtag', false));
await S.step('register re-enables', async () => tags.register({ tag: 'newtag' }));
await S.step('disable again', async () => tags.setEnabled('newtag', false));
await S.step('stored profile shape', async () => (await ledger.sourceProfiles()).map((r) => [r.source_tag, r.enabled, typeof r.config, r.config?.origin]));
tags = createSourceTags({ registry, ledger });
await S.step('restart: load picks dashboard tag with its enabled=false', async () => { await tags.load(); return tags.list(); });
await S.step('enable after restart', async () => tags.setEnabled('newtag', true));
S.done(); process.exit(S.failed ? 1 : 0);
