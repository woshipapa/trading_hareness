import { describe, expect, it } from 'vitest';

import {
  annotationStorageKey,
  buildManualAnnotationSeries,
  buildTonghuashunDeepLink,
  buildTonghuashunWebLink,
} from './analyst-chart';

describe('analyst chart integration helpers', () => {
  it('builds a desktop Tonghuashun deep link only for exchange-qualified symbols', () => {
    expect(buildTonghuashunDeepLink('600000.sh')).toBe('hexinstock://?stockcode=600000&market=SH');
    expect(buildTonghuashunDeepLink('600000')).toBeNull();
    expect(buildTonghuashunDeepLink(' 000001.SZ ')).toBe('hexinstock://?stockcode=000001&market=SZ');
    expect(buildTonghuashunWebLink(' 000001.SZ ')).toBe('https://stockpage.10jqka.com.cn/000001/');
  });

  it('projects valid manual points, lines and areas into ECharts data', () => {
    const result = buildManualAnnotationSeries([
      { id: 'p', kind: 'point', label: '观察点', start_index: 2, price: 10 },
      { id: 'l', kind: 'line', label: '止损复核', start_index: 0, price: 9.5 },
      { id: 'a', kind: 'area', label: '研究区间', start_index: 1, end_index: 4, lower: 9, upper: 11 },
      { id: 'bad', kind: 'area', label: '无效', start_index: 1, end_index: 8, lower: 11, upper: 9 },
    ], 5);

    expect(result.points).toHaveLength(1);
    expect(result.lines).toEqual([{ name: '止损复核', yAxis: 9.5 }]);
    expect(result.areas).toHaveLength(1);
    expect(result.areas[0][0].coord).toEqual([1, 9]);
    expect(annotationStorageKey('600000.SH', '2026-09-22', '2026-09-22'))
      .toContain('quant-analyst-chart-annotations:600000.SH:2026-09-22');
  });
});
