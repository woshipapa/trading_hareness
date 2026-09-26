import { describe, expect, it } from 'vitest';
import { buildStrategyEvidenceMatrix } from './strategy-evidence';

describe('buildStrategyEvidenceMatrix', () => {
  it('combines strategy sources and keeps matured outcome evidence separate', () => {
    const [row] = buildStrategyEvidenceMatrix({
      recommendations: [{ symbol: '000001.SZ', score: 0.8, risk_flags: ['波动'] }],
      postCloseCandidates: [{ symbol: '000001.SZ', name: '平安银行', candidate_type: 'base_ready_30d', score: 0.7, risk_flags: ['待确认'] }],
      dailyCandidates: [{ symbol: '000001.SZ', name: '平安银行', score: 0.6 }],
      outcomes: [
        { symbol: '000001.SZ', horizon_key: '30m', status: 'matured', raw_return: 0.012, observed_at: '2026-09-22T02:00:00Z' },
        { symbol: '000001.SZ', horizon_key: '30m', status: 'pending', raw_return: null, observed_at: '2026-09-22T01:00:00Z' },
      ],
    });

    expect(row.alignment).toBe('交叉支持');
    expect(row.coverage).toEqual(['方向推荐', '盘后候选', '日终摘要', '盘中结果']);
    expect(row.matured_30m_count).toBe(1);
    expect(row.pending_30m_count).toBe(1);
    expect(row.last_30m_return).toBe(0.012);
    expect(row.risk_flags).toEqual(['波动', '待确认']);
  });

  it('does not create rows or scores for malformed symbols', () => {
    const rows = buildStrategyEvidenceMatrix({
      recommendations: [{ symbol: 'invalid', score: 99 }],
      postCloseCandidates: [{ symbol: '000002.SZ', score: 0.4 }],
    });

    expect(rows).toHaveLength(1);
    expect(rows[0].symbol).toBe('000002.SZ');
    expect(rows[0].recommendation).toBeUndefined();
    expect(rows[0].alignment).toBe('盘后待确认');
  });
});
