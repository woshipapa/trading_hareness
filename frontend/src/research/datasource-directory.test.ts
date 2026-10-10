import { describe, expect, it } from 'vitest';
import { filterCapabilities, isResearchReadable, queryFromForm, statusLabel } from './datasource-directory';

const capability = {
  key: 'bars.minute', category: 'bars', label: '分钟K', grain: 'intraday', scope: 'per_symbol', fields: [], schema: [],
  time_semantics: '', description: '历史分钟线', evidence_locations: [],
  bindings: [{ source: 'tdx_public', capability: 'bars.minute', priority: 65, status: 'unsupported', store: null, adapter: 'app/datasources/sources/tdx_bars.py:fetch_minute', history: '', limits: '', notes: '', decision_eligible: false, spec: { params: { symbol: 'market+code', count: '1..800' }, field_map: {}, unit_factors: {}, limits: {}, time_semantics: '' } }],
} as any;

describe('datasource directory helpers', () => {
  it('filters by source, status and text', () => {
    expect(filterCapabilities([capability], 'tdx_public', 'unsupported', '分钟')).toHaveLength(1);
    expect(filterCapabilities([capability], 'tdx_mac', '', '')).toHaveLength(0);
  });

  it('identifies readable package adapters and serializes form values', () => {
    expect(isResearchReadable(capability.bindings[0])).toBe(true);
    expect(queryFromForm({ symbol: '600519.SH', count: '80', empty: ' ' })).toBe('symbol=600519.SH&count=80');
    expect(statusLabel('live_verified')).toBe('LIVE_VERIFIED');
  });
});
