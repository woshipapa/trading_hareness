import gzip, json, os
import numpy as np
import pandas as pd

DATA = '/Users/papa/codebase/n8n/state/research/shortterm_tactics_20261013/data'
OUT = os.path.join(os.path.dirname(__file__), '01_decode.txt')

def norm_code(x):
    return str(x).split('.')[0].zfill(6)

rows = []
days = []
with gzip.open(os.path.join(DATA, 'morning_auction_lists.jsonl.gz'), 'rt') as f:
    for line in f:
        z = json.loads(line)
        days.append((pd.Timestamp(z['day']), len(z.get('rows') or [])))
        for rank, r in enumerate(z.get('rows') or [], 1):
            rows.append({'day': pd.Timestamp(z['day']), 'rank': rank, 'code': norm_code(r[0]), **{f'p{i}': r[i] for i in range(len(r))}})
auc = pd.DataFrame(rows)
panel_cols = ['symbol','trading_date','open','close','pre_close','amount','gap','ret','limit_up_close','open_at_limit_up','o2o','ret1','intraday1','next_open_at_limit_up','limit_pct','streak']
panel = pd.read_parquet(os.path.join(DATA, 'daily_panel.parquet'), columns=panel_cols)
panel['code'] = panel.symbol.str[:6]
panel['trading_date'] = pd.to_datetime(panel.trading_date)
panel = panel.rename(columns={'trading_date':'day'})
lim = pd.read_parquet(os.path.join(DATA, 'limit_up_lists.parquet'))
lim['day'] = pd.to_datetime(lim.trading_date)
lim['code'] = lim.symbol.str[:6]

m = auc.merge(panel, on=['day','code'], how='left')
out = [f'nonempty_days={sum(n>0 for _,n in days)} total_days={len(days)} rows={len(auc)}', 'row_width=17', '', 'POSITION TESTS (agreement means abs error <= tolerance; n is matched rows)']
for pos in range(17):
    s = pd.to_numeric(m.get(f'p{pos}'), errors='coerce')
    if pos == 3:
        target = m.ret*100; ok=s.notna()&target.notna(); agree=(s[ok]-target[ok]).abs()<=0.03
        out.append(f'p3 vs panel_ret_pct: n={int(ok.sum())} agree={int(agree.sum())} share={agree.mean():.6f} median_abs_err={(s[ok]-target[ok]).abs().median():.6g}')
    elif pos == 5:
        target = m.gap*100; ok=s.notna()&target.notna(); agree=(s[ok]-target[ok]).abs()<=0.03
        out.append(f'p5 vs panel_gap_pct: n={int(ok.sum())} agree={int(agree.sum())} share={agree.mean():.6f} median_abs_err={(s[ok]-target[ok]).abs().median():.6g}')
    elif pos == 4:
        target=m.amount*1000; ok=s.notna()&target.notna(); ratio=(s[ok]/target[ok]).replace([np.inf,-np.inf],np.nan).dropna()
        out.append(f'p4 vs panel_amount_cny: n={int(ok.sum())} median_ratio={ratio.median():.6g} q05={ratio.quantile(.05):.6g} q95={ratio.quantile(.95):.6g}')
    else:
        out.append(f'p{pos}: no direct panel identity tested')

lm = m.merge(lim[['day','code','lh_net','lh_buy','lh_sell','lh_turnover','float_cap','turnover_rate','boards','price','pct']], on=['day','code'], how='left', suffixes=('','_lim'))
out += ['', 'LIMIT-LIST FIELD TESTS']
for pos, col in [(6,'lh_net'),(8,'lh_buy'),(9,'lh_sell'),(12,'float_cap')]:
    s=pd.to_numeric(lm[f'p{pos}'],errors='coerce'); t=pd.to_numeric(lm[col],errors='coerce'); ok=s.notna()&t.notna()&(t!=0)
    ratio=(s[ok]/t[ok]).replace([np.inf,-np.inf],np.nan).dropna(); agree=(s[ok]-t[ok]).abs()<=np.maximum(1,t[ok].abs()*0.0001)
    out.append(f'p{pos} vs {col}: n={int(ok.sum())} exactish_share={agree.mean() if len(agree) else np.nan:.6f} median_ratio={ratio.median() if len(ratio) else np.nan:.6g} q05={ratio.quantile(.05) if len(ratio) else np.nan:.6g} q95={ratio.quantile(.95) if len(ratio) else np.nan:.6g}')
for pos,col in [(7,'turnover_rate'),(13,'lh_net'),(14,'lh_buy'),(15,'lh_sell')]:
    s=pd.to_numeric(lm[f'p{pos}'],errors='coerce'); t=pd.to_numeric(lm[col],errors='coerce'); ok=s.notna()&t.notna()&(t!=0)
    if ok.sum():
        ratio=(s[ok]/t[ok]).replace([np.inf,-np.inf],np.nan).dropna()
        out.append(f'p{pos} candidate {col}: n={int(ok.sum())} median_ratio={ratio.median():.6g} corr={s[ok].corr(t[ok]):.6f}')

out += ['', 'LIST ORDER']
perday = auc.groupby('day').agg(n=('rank','size'), p4_desc=('p4', lambda x: x.is_monotonic_decreasing), p3_desc=('p3', lambda x: x.is_monotonic_decreasing))
out.append(f'days_with_rows={len(perday)} p4_desc_share={perday.p4_desc.mean():.6f} p3_desc_share={perday.p3_desc.mean():.6f} median_n={perday.n.median():.1f} q10_n={perday.n.quantile(.1):.1f} q90_n={perday.n.quantile(.9):.1f}')

session_dates=sorted(panel.day.drop_duplicates())
next_day={session_dates[i]:session_dates[i+1] for i in range(len(session_dates)-1)}
prev=lim[['day','code','boards']].copy(); prev['auction_day']=prev.day.map(next_day)
ac=auc[['day','code']].drop_duplicates(); cov=prev.merge(ac,left_on=['auction_day','code'],right_on=['day','code'],how='left',suffixes=('_prev','_auc')); cov['in_list']=cov.day_auc.notna()
out += ['', 'PREVIOUS LIMIT-UP COVERAGE BY BOARD COUNT']
for b,g in cov.groupby('boards'):
    out.append(f'boards={int(b)} prev_lu={len(g)} in_list={int(g.in_list.sum())} share={g.in_list.mean():.6f}')
out.append(f'overall prev_lu={len(cov)} in_list={int(cov.in_list.sum())} share={cov.in_list.mean():.6f}')
with open(OUT,'w') as f: f.write('\n'.join(out)+'\n')
print(OUT)
