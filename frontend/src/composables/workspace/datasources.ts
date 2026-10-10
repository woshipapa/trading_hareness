import { ref } from 'vue';
import type { DatasourceCatalog, DatasourceRead } from '../../research/datasource-directory';
import type { ResearchShell } from './shell';

/**
 * 数据源目录: the data-source catalog (every capability with its bindings, statuses, specs and evidence) and the
 * research reads of the TDX package adapters, which are never decision-eligible.
 */
export function useDatasourcesSlice(shell: ResearchShell) {
  const { getJson } = shell.transport;

  const datasourceCatalog = ref<DatasourceCatalog | null>(null);
  const datasourceCatalogLoading = ref(false);
  const datasourceCatalogError = ref('');

  async function loadDatasourceCatalog() {
    datasourceCatalogLoading.value = true;
    datasourceCatalogError.value = '';
    try { datasourceCatalog.value = await getJson<DatasourceCatalog>('/api/research/datasources/catalog'); }
    catch (reason) { datasourceCatalogError.value = reason instanceof Error ? reason.message : String(reason); }
    finally { datasourceCatalogLoading.value = false; }
  }

  function readDatasource(source: string, capability: string, query: string) {
    return getJson<DatasourceRead>(`/api/research/datasources/read/${source}/${capability}${query ? `?${query}` : ''}`);
  }

  return { datasourceCatalog, datasourceCatalogLoading, datasourceCatalogError, loadDatasourceCatalog, readDatasource };
}
