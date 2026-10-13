import gzip, json, os
import numpy as np
import pandas as pd

DATA='/Users/papa/codebase/n8n/state/research/shortterm_tactics_20261013/data'
OUT=os.path.join(os.path.dirname(__file__),'02_development.txt')

def code(x): return str(x).split('.')[0].zfill(6)
def clean_amount(df):
    a=df['amount'].astype(float)*1000
    denom=df['volume'].astype(float)*df['close'].astype(float)/10
    bad=(denom>0)&((df['amount']/denom)>50)
    a.loc[bad]=a.loc[bad]/1000
    return a
def tstat(x):
    x=pd.Series(x).dropna(); n=len(x)
    if n<2 or x.std(ddof=1)==0: return (float(x.mean()) if n else np.nan, np.nan)
    return float(x.mean()), float(x.mean()/(x.std(ddof=1)/np.sqrt(n)))
def stat(df, col, label=''):
    if len(df)==0: return f'{label} n=0'
    d=df.groupby('day')[col].mean(); est,t=tstat(d)
    return f'{label} estimate={est:.8f} t={t:.3f} days={len(d)} stock_days={len(df)}'
def summarize(df, label):
    return [stat(df,c,label+' '+n) for c,n in [('seal_again','seal_again'),('intraday1','open_to_close'),('o2o','o2o'),('ret1','holder_next_close')]]
def ratios(df,label):
    a=df.amount_to_turnover.replace([np.inf,-np.inf],np.nan).dropna(); f=df.amount_to_float.replace([np.inf,-np.inf],np.nan).dropna()
    return f'{label} auction/T_turnover median={a.median():.8f} mean={a.mean():.8f}; auction/float_cap median={f.median():.8f} mean={f.mean():.8f}; rank median={df["rank"].median():.1f} n={len(df)}'

pcols=['symbol','trading_date','di','open','close','pre_close','volume','amount','gap','ret','limit_up_close','open_at_limit_up','intraday1','o2o','ret1','next_open_at_limit_up']
p=pd.read_parquet(os.path.join(DATA,'daily_panel.parquet'),columns=pcols)
p['day']=pd.to_datetime(p.trading_date); p['code']=p.symbol.str[:6]; p['clean_amount']=clean_amount(p); p=p.sort_values('di')
p=p[p.day<=pd.Timestamp('2025-12-31')].copy()
sessions=p[['day','di']].drop_duplicates().sort_values('di'); next_map=dict(zip(sessions.day.iloc[:-1],sessions.day.iloc[1:]))
rows=[]
with gzip.open(os.path.join(DATA,'morning_auction_lists.jsonl.gz'),'rt') as f:
    for line in f:
        z=json.loads(line); day=pd.Timestamp(z['day'])
        for rank,r in enumerate(z.get('rows') or [],1): rows.append({'day':day,'code':code(r[0]),'rank':rank,'auction_amount':float(r[4]),'list_gap':float(r[5]),'float_cap':float(r[12])})
a=pd.DataFrame(rows)
a=a[a.day<=pd.Timestamp('2025-12-31')].copy()
cur=p[['day','code','gap','ret','limit_up_close','open_at_limit_up','intraday1','o2o','ret1','next_open_at_limit_up','di']].rename(columns={'gap':'panel_gap','ret':'panel_ret'})
a=a.merge(cur,on=['day','code'],how='left')
lu=pd.read_parquet(os.path.join(DATA,'limit_up_lists.parquet'),columns=['symbol','trading_date','boards','lh_net','lh_buy','lh_sell','float_cap','turnover_rate'])
lu['T']=pd.to_datetime(lu.trading_date); lu['code']=lu.symbol.str[:6]; lu=lu[lu['T']<=pd.Timestamp('2025-12-31')].copy(); lu['day']=lu['T'].map(next_map); lu=lu.rename(columns={'boards':'boards_T','float_cap':'float_cap_T'}); lu['is_lu_T']=True
x=a.merge(lu[['day','code','T','boards_T','lh_net','lh_buy','lh_sell','float_cap_T','turnover_rate','is_lu_T']],on=['day','code'],how='left')
prev=p[['day','code','clean_amount']].copy(); prev['day']=prev.day.map(next_map); prev=prev.rename(columns={'clean_amount':'turnover_T'})
x=x.merge(prev[['day','code','turnover_T']],on=['day','code'],how='left'); x['is_prev_lu']=x.is_lu_T.fillna(False); x=x[x.panel_gap.notna()].copy()
x['amount_to_turnover']=x.auction_amount/x.turnover_T; x['amount_to_float']=x.auction_amount/x.float_cap_T
x['gap_bucket']=pd.cut(x.panel_gap,bins=[-np.inf,-.02,0,.02,.05,.09,np.inf],labels=['<−2%','−2–0%','0–2%','2–5%','5–9%','≥9%'],right=False)
x['board_bucket']=pd.cut(x.boards_T,bins=[0.5,1.5,2.5,3.5,np.inf],labels=['1','2','3','4+'],right=False); x['seal_again']=x.limit_up_close.astype(float)
s=x[x.is_prev_lu & x.auction_amount.notna()].copy(); dev=s[(s.day>=pd.Timestamp('2023-09-01'))&(s.day<=pd.Timestamp('2025-12-31'))].copy()
q75=float(dev.amount_to_turnover.replace([np.inf,-np.inf],np.nan).dropna().quantile(.75)); board_median=dev.groupby('boards_T').panel_gap.median().to_dict()
s['rule_boom']=(s.amount_to_turnover>=q75)&s.panel_gap.between(.02,.07,inclusive='left'); s['rule_under']=(s.boards_T>=2)&(s.panel_gap < s.boards_T.map(board_median)); s['rule_top10']=s['rank']<=10
out=[f'data_rows={len(x)} list_rows_with_panel={len(a.merge(cur,on=["day","code"],how="inner"))} prev_limit_up_in_list={len(s)}',f'development_days={dev.day.nunique()} threshold_amount_to_turnover_q75={q75:.8f}','board_median_gap='+','.join(f'{int(k)}:{v:.8f}' for k,v in sorted(board_median.items())),'','BASELINE CELLS (yesterday limit-up stocks in list)']
for (gb,bb),g in s.groupby(['gap_bucket','board_bucket'],observed=True): out += [ratios(g,f'gap={gb} boards={bb}')] + summarize(g,f'gap={gb} boards={bb}')
out += ['', 'RULE RESULTS (development; day-clustered)', f'boom definition=amount_to_turnover >= {q75:.8f} and gap in [0.02,0.07)']
for name,mask in [('boom',s.rule_boom),('under_expectation',s.rule_under),('top10',s.rule_top10)]:
    g=s[mask]; out.append(f'RULE {name} n={len(g)} days={g.day.nunique()}'); out += summarize(g,'  all')
    for outcome in ['seal_again','intraday1','o2o','ret1']:
        q=s.assign(treated=mask).groupby(['day','gap_bucket','board_bucket','treated'],observed=True)[outcome].mean().unstack('treated').dropna()
        if len(q):
            d=(q[True]-q[False]).groupby(level=0).mean(); est,t=tstat(d); out.append(f'  within_cell {outcome} diff_estimate={est:.8f} t={t:.3f} days={len(d)} cells={len(q)}')
        else: out.append(f'  within_cell {outcome} no paired cells')
out += ['', 'UNTESTABLE REGISTERED RULES', '抢筹：未验证的 09:25 主力净买入字段；p13-p15 是收盘后 limit-list 复制字段，不能使用。', '一字薄厚：未验证买卖盘量字段；位置6-11、16 无可靠映射，不能使用。']
n=x[~x.is_prev_lu & x.auction_amount.notna()].copy(); ndev=n[n.day<=pd.Timestamp('2025-12-31')]
out += ['', 'NON-PREVIOUS-LIMIT-UP LIST STOCKS', f'development_n={len(ndev)} days={ndev.day.nunique()}']
for mask,name in [(ndev['rank']<=10,'top10'),(ndev['rank']>10,'rank>10')]: out += summarize(ndev[mask],name)
q=ndev.assign(treated=ndev['rank']<=10).groupby(['day','treated']).o2o.mean().unstack('treated').dropna(); d=q[True]-q[False] if len(q) else pd.Series(dtype=float); est,t=tstat(d); out.append(f'within_day o2o top10-minus-rest estimate={est:.8f} t={t:.3f} days={len(d)} cells={len(q)}')
with open(OUT,'w') as f: f.write('\n'.join(out)+'\n')
print(OUT)
