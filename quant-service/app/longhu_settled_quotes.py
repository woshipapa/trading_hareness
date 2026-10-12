"""Date-addressed licensed OHLC. Live quotes cannot repair historical days."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from math import isfinite


def number(value):
    try:
        result=float(value)
        return result if isfinite(result) else None
    except (ValueError,TypeError):
        return None


def normalized_quote(symbol, day, payload):
    dates=payload.get('x') or []
    values=payload.get('y') or []
    target=day.strftime('%Y%m%d')
    keys=[str(d).replace('-','') for d in dates]
    if target not in keys or len(keys)!=len(values):
        return None
    i=keys.index(target)
    if i<1 or len(values[i])<4:
        return None
    o,c,h,low=[number(x) for x in values[i][:4]]
    p=number(values[i-1][1])
    if any(v is None or v<=0 for v in (o,c,h,low,p)) or not low<=min(o,c)<=max(o,c)<=h:
        return None
    volumes,amounts=payload.get('vol') or [],payload.get('bal') or []
    return {'ts_code':symbol,'trade_date':target,'open':o,'close':c,'high':h,'low':low,'pre_close':p,
            'vol':number(volumes[i]) if i<len(volumes) else None,
            'amount':number(amounts[i]) if i<len(amounts) else None,
            'price_basis':'longhu_requested_Is_FS_0_cross_checked_vendor_close'}


def fetch(source, symbols, day, *, workers=8):
    unique=list(dict.fromkeys(symbols))
    quotes,errors=[],{}
    def one(symbol):
        envelope=source.raw_call({'target':'longhu_history','path':'/w1/api/index.php',
            'params':{'a':'GetKLineDay_W14','c':'StockLineData','apiv':'w40','StockID':symbol.split('.')[0],
                      'Type':'d','Is_FS':'0','st':min(300,max(20,(date.today()-day).days+10)),'Index':0}})
        for page in envelope.get('pages',[]):
            quote=normalized_quote(symbol,day,page.get('payload') or {})
            if quote is not None:
                return quote
        raise ValueError('requested_settled_day_missing_or_invalid_ohlc')
    with ThreadPoolExecutor(max_workers=max(1,min(workers,12))) as pool:
        tasks={pool.submit(one,s):s for s in unique}
        for task in as_completed(tasks):
            s=tasks[task]
            try:
                quotes.append(task.result())
            except Exception as exc:
                errors[s]=f'{type(exc).__name__}: {exc}'
    return quotes,{'provider':'longhuvip:GetKLineDay_W14','trade_date':str(day),'requested':len(unique),
                   'received':len(quotes),'failed':len(errors),'errors':dict(list(errors.items())[:30]),
                   'physical_page_limit':300,'no_live_quote_substitution':True}


def fetch_classified(source, symbols, day, *, workers=6):
    """Fetch dated bars and retain the evidence needed to explain every gap."""
    unique = list(dict.fromkeys(symbols))
    target = day.strftime('%Y%m%d')

    def one(symbol):
        envelope = source.raw_call({'target': 'longhu_history', 'path': '/w1/api/index.php',
            'params': {'a': 'GetKLineDay_W14', 'c': 'StockLineData', 'apiv': 'w40',
                       'StockID': symbol.split('.')[0], 'Type': 'd', 'Is_FS': '0',
                       'st': min(300, max(20, (date.today() - day).days + 10)), 'Index': 0}})
        for page in envelope.get('pages', []):
            payload = page.get('payload') or {}
            dates = payload.get('x') or []
            keys = [str(value).replace('-', '') for value in dates]
            if target not in keys:
                continue
            index = keys.index(target)
            cq = payload.get('CQ')
            if cq is None or len(cq) != len(keys):
                return symbol, 'unverified', None, False, None
            ex_rights_on_day = bool(str(cq[index]).strip())
            if any(str(value).strip() for value in cq[index + 1:]):
                return symbol, 'adjusted_later', None, ex_rights_on_day, None
            quote = normalized_quote(symbol, day, payload)
            if quote is None:
                return symbol, 'invalid', None, ex_rights_on_day, None
            quote['ex_rights_on_day'] = ex_rights_on_day
            return symbol, 'filled', quote, ex_rights_on_day, None
        return symbol, 'not_traded', None, False, None

    def guarded(symbol):
        try:
            return one(symbol)
        except Exception as error:
            return symbol, 'failed', None, False, f'{type(error).__name__}: {error}'

    results = []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 6))) as pool:
        for result in pool.map(guarded, unique):
            results.append(result)
    outcomes = {name: sorted(symbol for symbol, outcome, _quote, _marker, _error in results if outcome == name)
                for name in ('filled', 'not_traded', 'adjusted_later', 'unverified', 'invalid', 'failed')}
    quotes = [quote for _symbol, outcome, quote, _marker, _error in results if outcome == 'filled']
    return quotes, {
        'provider': 'longhuvip:GetKLineDay_W14', 'trade_date': str(day),
        'requested': len(unique), 'received': len(quotes), 'outcomes': outcomes,
        'classified': {symbol: outcome for symbol, outcome, _quote, _marker, _error in results},
        'errors': dict(list((symbol, error) for symbol, outcome, _quote, _marker, error in results
                            if outcome == 'failed')[:30]),
        'ex_rights_on_day': {symbol: marker for symbol, outcome, _quote, marker, _error in results
                             if outcome == 'filled'},
    }
