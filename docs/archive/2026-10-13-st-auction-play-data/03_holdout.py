import gzip, json, os
import numpy as np
import pandas as pd

DATA='/Users/papa/codebase/n8n/state/research/shortterm_tactics_20261013/data'; OUT=os.path.join(os.path.dirname(__file__),'03_holdout.txt')
Q75=0.21684634
BOARD_MEDIAN={1:0.02349870,2:0.05431310,3:0.08602102,4:0.09654471,5:0.09848485,6:0.09842940,7:0.09968586,8:0.09982789,9:0.09984399,10:0.09996215,11:0.03912320,12:0.10000000,13:0.10000000,14:0.10001977}
def code(x): return str(x).split('.')[0].zfill(6)
def clean_amount(df):
    a=df.amount.astype(float)*1000; denom=df.volume.astype(float)*df.close.astype(float)/10; bad=(denom>0)&((df.amount/denom)>50); a.loc[bad]=a.loc[bad]/1000; return a
def tstat(v):
    v=pd.Series(v).dropna(); return (float(v.mean()), float(v.mean()/(v.std(ddof=1)/np.sqrt(len(v)))) if len(v)>1 and v.std(ddof=1)>0 else np.nan)
def stat(df,c,label):
    if not len(df): return f'{label} n=0'
    d=df.groupby('day')[c].mean(); e,t=tstat(d); return f'{label} estimate={e:.8f} t={t:.3f} days={len(d)} stock_days={len(df)}'
def sums(df,label): return [stat(df,c,label+' '+n) for c,n in [('seal_again','seal_again'),('intraday1','open_to_close'),('o2o','o2o'),('ret1','holder_next_close')]]
def ratios(df,label):
    a=df.amount_to_turnover.replace([np.inf,-np.inf],np.nan).dropna(); f=df.amount_to_float.replace([np.inf,-np.inf],np.nan).dropna()
    return f'{label} auction/T_turnover median={a.median():.8f} mean={a.mean():.8f}; auction/float_cap median={f.median():.8f} mean={f.mean():.8f}; rank median={df["rank"].median():.1f} n={len(df)}'

pcols=['symbol','trading_date','di','open','close','pre_close','volume','amount','gap','ret','limit_up_close','open_at_limit_up','intraday1','o2o','ret1','next_open_at_limit_up']
p=pd.read_parquet(os.path.join(DATA,'daily_panel.parquet'),columns=pcols); p['day']=pd.to_datetime(p.trading_date); p['code']=p.symbol.str[:6]; p['clean_amount']=clean_amount(p)
p=p[(p.day>=pd.Timestamp('2025-12-31'))&(p.day<=pd.Timestamp('2026-09-18'))].sort_values('di')
ss=p[['day','di']].drop_duplicates().sort_values('di'); next_map=dict(zip(ss.day.iloc[:-1],ss.day.iloc[1:]))
rows=[]
with gzip.open(os.path.join(DATA,'morning_auction_lists.jsonl.gz'),'rt') as f:
    for line in f:
        z=json.loads(line); day=pd.Timestamp(z['day'])
        if day<pd.Timestamp('2026-01-01') or day>pd.Timestamp('2026-09-18'): continue
        for rank,r in enumerate(z.get('rows') or [],1): rows.append({'day':day,'code':code(r[0]),'rank':rank,'auction_amount':float(r[4]),'list_gap':float(r[5]),'float_cap':float(r[12])})
a=pd.DataFrame(rows)
cur=p[['day','code','gap','ret','limit_up_close','open_at_limit_up','intraday1','o2o','ret1','next_open_at_limit_up']].rename(columns={'gap':'panel_gap','ret':'panel_ret'}); a=a.merge(cur,on=['day','code'],how='left')
lu=pd.read_parquet(os.path.join(DATA,'limit_up_lists.parquet'),columns=['symbol','trading_date','boards','lh_net','lh_buy','lh_sell','float_cap','turnover_rate']); lu['T']=pd.to_datetime(lu.trading_date); lu['code']=lu.symbol.str[:6]; lu=lu[(lu['T']>=pd.Timestamp('2025-12-31'))&(lu['T']<pd.Timestamp('2026-09-18'))].copy(); lu['day']=lu['T'].map(next_map); lu=lu.rename(columns={'boards':'boards_T','float_cap':'float_cap_T'}); lu['is_lu_T']=True
x=a.merge(lu[['day','code','T','boards_T','lh_net','lh_buy','lh_sell','float_cap_T','turnover_rate','is_lu_T']],on=['day','code'],how='left')
prev=p[['day','code','clean_amount']].copy(); prev['day']=prev.day.map(next_map); prev=prev.rename(columns={'clean_amount':'turnover_T'}); x=x.merge(prev[['day','code','turnover_T']],on=['day','code'],how='left'); x['is_prev_lu']=x.is_lu_T.fillna(False); x=x[x.panel_gap.notna()].copy()
x['amount_to_turnover']=x.auction_amount/x.turnover_T; x['amount_to_float']=x.auction_amount/x.float_cap_T; x['gap_bucket']=pd.cut(x.panel_gap,bins=[-np.inf,-.02,0,.02,.05,.09,np.inf],labels=['<−2%','−2–0%','0–2%','2–5%','5–9%','≥9%'],right=False); x['board_bucket']=pd.cut(x.boards_T,bins=[0.5,1.5,2.5,3.5,np.inf],labels=['1','2','3','4+'],right=False); x['seal_again']=x.limit_up_close.astype(float)
s=x[x.is_prev_lu & x.auction_amount.notna()].copy(); s['boom']=(s.amount_to_turnover>=Q75)&s.panel_gap.between(.02,.07,inclusive='left'); s['under']=(s.boards_T>=2)&(s.panel_gap < s.boards_T.map(BOARD_MEDIAN)); s['top10']=s['rank']<=10
out=[f'holdout_read_once=true',f'holdout_days={s.day.nunique()} prev_limit_up_in_list={len(s)}',f'fixed_threshold_amount_to_turnover_q75={Q75:.8f}','','BASELINE CELLS (holdout)']
for (gb,bb),g in s.groupby(['gap_bucket','board_bucket'],observed=True): out += [ratios(g,f'gap={gb} boards={bb}')] + sums(g,f'gap={gb} boards={bb}')
out += ['', 'RULE RESULTS (holdout; day-clustered)']
for name in ['boom','under','top10']:
    g=s[s[name]]; out.append(f'RULE {name} n={len(g)} days={g.day.nunique()}'); out+=sums(g,'  all')
    for c in ['seal_again','intraday1','o2o','ret1']:
        q=s.groupby(['day','gap_bucket','board_bucket',name],observed=True)[c].mean().unstack(name).dropna()
        if len(q):
            d=(q[True]-q[False]).groupby(level=0).mean(); e,t=tstat(d); out.append(f'  within_cell {c} diff_estimate={e:.8f} t={t:.3f} days={len(d)} cells={len(q)}')
        else: out.append(f'  within_cell {c} no paired cells')
out += ['', 'UNTENABLE REGISTERED RULES','抢筹：未验证 09:25 主力净买入字段，未测试。','一字薄厚：未验证买卖盘量字段，未测试。']
n=x[~x.is_prev_lu & x.auction_amount.notna()].copy(); n['top10']=n['rank']<=10; out += ['', 'NON-PREVIOUS-LIMIT-UP LIST STOCKS']
for z,label in [(n[n.top10],'top10'),(n[~n.top10],'rank>10')]: out+=sums(z,label)
q=n.groupby(['day','top10']).o2o.mean().unstack('top10').dropna(); d=q[True]-q[False] if len(q) else pd.Series(dtype=float); e,t=tstat(d); out.append(f'within_day o2o top10-minus-rest estimate={e:.8f} t={t:.3f} days={len(d)} cells={len(q)}')
with open(OUT,'w') as f:f.write('\n'.join(out)+'\n')
print(OUT)
