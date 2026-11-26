"""HAP Stats: public world data only. No login or game actions."""
import argparse, csv, gzip, html, io, json, re, unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote_plus
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import MaxNLocator, FuncFormatter

ROOT = Path(__file__).resolve().parent
TZ = ZoneInfo('Europe/Lisbon')
BG, PANEL, INK, MUTED, GOLD, RED, GREEN, BLUE = '#f3e5c6','#fff8e9','#35291f','#71614d','#a77b35','#8b302b','#3b7049','#355c82'

def num(s):
    v = int(s)
    if v < 0: raise ValueError('Valor negativo inesperado')
    return v

def norm(s):
    return re.sub(r'[^a-z0-9]','',unicodedata.normalize('NFKD',s).casefold())

def rows(text):
    return [r for r in csv.reader(io.StringIO(text)) if r and any(r)]

def fetch(world, name):
    url = f'https://{world}.tribalwars.com.pt/map/{name}.txt.gz'
    with urlopen(Request(url,headers={'User-Agent':'HAPStats/1.0 public-world-statistics','Accept':'*/*'}),timeout=35) as r:
        raw = r.read(30_000_001)
        if len(raw)>30_000_000: raise ValueError('Resposta demasiado grande')
    if raw[:2] == b'\x1f\x8b': raw = gzip.decompress(raw)
    if len(raw)>100_000_000: raise ValueError('Dados demasiado grandes')
    return rows(raw.decode('utf-8-sig'))

def parse_tribes(data):
    out=[]
    for r in data:
        if len(r)!=8: raise ValueError('Formato ally.txt inesperado')
        out.append(dict(id=num(r[0]), name=unquote_plus(r[1]),tag=unquote_plus(r[2]),members=num(r[3]),villages=num(r[4]),points=num(r[5]),all_points=num(r[6]),rank=num(r[7])))
    if not out: raise ValueError('Lista de tribos vazia')
    if len({x['id'] for x in out}) != len(out): raise ValueError('IDs de tribo duplicados')
    return out

def choose(tribes, cfg, previous):
    tid = cfg.get('tribe_id') or (previous[-1]['id'] if previous else None)
    match=[t for t in tribes if t['id']==tid] if tid is not None else [t for t in tribes if norm(t['name'])==norm(cfg['tribe_name'])]
    if len(match)!=1: raise ValueError('Tribo não identificada de forma única. Define tribe_id em config.json; não será publicado um resultado errado.')
    if previous and previous[-1]['id']!=match[0]['id']: raise ValueError('Histórico pertence a outra tribo; utiliza uma instalação nova.')
    return match[0]

def parse_military(data,tid):
    found=[]
    for r in data:
        if len(r)!=3: raise ValueError('Formato de adversários derrotados inesperado')
        rank, pid, score=map(num,r)
        if pid==tid: found.append(score)
    if len(found)>1: raise ValueError('ID repetido')
    return found[0] if found else None

def collect(cfg,previous,loader=fetch,now=None):
    world=cfg['world']
    if not re.fullmatch(r'pt(?:p|c)?[0-9]+',world): raise ValueError('Mundo português inválido')
    if previous and previous[-1]['world']!=world: raise ValueError('Histórico pertence a outro mundo')
    tribes=parse_tribes(loader(world,'ally'))
    s=choose(tribes,cfg,previous).copy()
    s['rivals']=[{k:t[k] for k in ('id','name','rank','points')} for t in sorted(tribes,key=lambda t:t['rank']) if t['id']!=s['id'] and abs(t['rank']-s['rank'])<=3]
    try:
        players=[]
        for r in loader(world,'player'):
            if len(r)!=6: raise ValueError('Formato player.txt inesperado')
            if num(r[2])==s['id']: players.append([num(r[0]),unquote_plus(r[1]),num(r[4]),num(r[5]),num(r[3])])
        if len({x[0] for x in players})!=len(players): raise ValueError('Jogadores duplicados')
        # A mismatch may mean files were generated at different times. Avoid false membership events.
        if len(players)!=s['members']: raise ValueError('Número de membros divergente entre os ficheiros')
        s['players']=players
    except Exception as e:
        print('AVISO: lista individual indisponível:',e);s['players']=None
    s.update(world=world,time=(now or datetime.now(timezone.utc)).isoformat())
    for field,source in [('oda','kill_att_tribe'),('odd','kill_def_tribe'),('od','kill_all_tribe')]:
        try: s[field]=parse_military(loader(world,source),s['id'])
        except Exception as e:
            print(f'AVISO: {field} indisponível: {e}'); s[field]=None
    return s

def append_history(history,s,days):
    now=datetime.fromisoformat(s['time']); cutoff=now-timedelta(days=days)
    out=[x for x in history if cutoff<=datetime.fromisoformat(x['time'])<now and datetime.fromisoformat(x['time']).replace(minute=0,second=0,microsecond=0)!=now.replace(minute=0,second=0,microsecond=0)]
    return sorted(out+[s],key=lambda x:x['time'])

def fmt(n): return '—' if n is None else f'{n:,}'.replace(',',' ')

def local(t): return datetime.fromisoformat(t).astimezone(TZ)

def delta(hist,field):
    s=hist[-1]; target=datetime.fromisoformat(s['time'])-timedelta(days=1)
    old=[x for x in hist if datetime.fromisoformat(x['time'])<=target]
    if not old or s.get(field) is None or old[-1].get(field) is None or target-datetime.fromisoformat(old[-1]['time'])>timedelta(hours=3): return None
    return s[field]-old[-1][field]

def fig_base(s,title,subtitle, demo=False, height=5):
    f=plt.figure(figsize=(8.4,height),dpi=120,facecolor=BG)
    f.text(.045,.955,s['name'].upper(),fontsize=18,weight='bold',color=INK,va='top')
    f.text(.955,.95,('DEMONSTRAÇÃO' if demo else s['world'].upper()),ha='right',va='top',color=RED,weight='bold',fontsize=10)
    f.text(.045,.875,title,fontsize=13,weight='bold',color=RED,va='top')
    f.text(.045,.82,subtitle,fontsize=9,color=MUTED,va='top')
    f.text(.045,.035,'Fonte: dados públicos do jogo' if not demo else 'DADOS FICTÍCIOS — apenas demonstração visual',color=MUTED,fontsize=8)
    f.text(.955,.035,'Consulta: '+local(s['time']).strftime('%d/%m/%Y %H:%M %Z'),ha='right',color=MUTED,fontsize=8)
    return f

def save(f,out,name):
    f.savefig(out/name,dpi=120,facecolor=f.get_facecolor()); plt.close(f)

def summary(hist,out,demo):
    s=hist[-1]; f=fig_base(s,'Esquadrão de Elite | Painel da tribo','Classificação, território e desempenho militar',demo,3.65)
    metrics=[('Pontos', 'points'),('Classificação','rank'),('Aldeias','villages'),('Membros','members'),('Ataque · ODA','oda'),('Defesa · ODD','odd'),('Total · OD','od'),('Pontos de todos','all_points')]
    for i,(label,key) in enumerate(metrics):
        x=.045+(i%4)*.231;y=.43-(i//4)*.30
        a=f.add_axes([x,y,.218,.255]);a.set_facecolor(PANEL);a.set_xticks([]);a.set_yticks([])
        for spine in a.spines.values(): spine.set_color('#d6c39d')
        a.text(.07,.80,label,transform=a.transAxes,color=MUTED,fontsize=9,va='center')
        a.text(.07,.49,('#' if key=='rank' and s.get(key) is not None else '')+fmt(s.get(key)),transform=a.transAxes,color=INK,fontsize=17 if len(fmt(s.get(key)))<10 else 12,weight='bold',va='center')
        d=delta(hist,key); labeld='24h: —' if d is None else '24h: '+('+' if d>0 else '')+str(d).replace('-', '−')
        a.text(.07,.17,labeld,transform=a.transAxes,color=MUTED if d in (None,0) else (GREEN if (d<0 if key=='rank' else d>0) else RED),fontsize=8,va='center')
    save(f,out,'resumo.png')

def chart(hist,out,title,fields,name,demo,days):
    s=hist[-1]; cutoff=datetime.fromisoformat(s['time'])-timedelta(days=days)
    data=[x for x in hist if datetime.fromisoformat(x['time'])>=cutoff]
    f=fig_base(s,title,f'Últimos {days} dias · {len(data)} observações guardadas',demo,5.2)
    for i,(label,key,color) in enumerate(fields):
        a=f.add_subplot(2,2,i+1);a.set_facecolor(PANEL);a.set_title(label,loc='left',color=INK,fontsize=10,weight='bold')
        times=[local(x['time']) for x in data]; vals=[x.get(key) for x in data]
        if not any(v is not None for v in vals):
            a.text(.5,.5,'Sem dados disponíveis',ha='center',va='center',transform=a.transAxes,color=MUTED);a.set_xticks([]);a.set_yticks([])
        else:
            a.plot(times,vals,color=color,linewidth=1.9,marker='o' if len(times)<=12 else None,markersize=3)
            if len(times)==1: a.scatter(times,[v if v is not None else float('nan') for v in vals],color=color);a.set_xlim(times[0]-timedelta(hours=12),times[0]+timedelta(hours=12))
            a.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=2,maxticks=4,tz=TZ));a.xaxis.set_major_formatter(mdates.DateFormatter('%d/%m',tz=TZ))
            a.yaxis.set_major_locator(MaxNLocator(integer=True,nbins=4));a.yaxis.set_major_formatter(FuncFormatter(lambda x,p:fmt(int(x))))
            if key=='rank': a.invert_yaxis()
            a.grid(axis='y',color='#ddd0b3',alpha=.8);a.tick_params(labelsize=8,colors=MUTED)
        for spine in a.spines.values(): spine.set_color('#d6c39d')
    f.subplots_adjust(left=.11,right=.955,bottom=.13,top=.74,hspace=.58,wspace=.36)
    save(f,out,name)

def member_html(hist):
    s=hist[-1]; players=s.get('players')
    if players is None: return '<p>Lista de membros indisponível nesta consulta.</p>'
    target=datetime.fromisoformat(s['time'])-timedelta(days=1)
    old=[x for x in hist if x.get('players') is not None and datetime.fromisoformat(x['time'])<=target and target-datetime.fromisoformat(x['time'])<=timedelta(hours=3)]
    prev={x[0]:x for x in old[-1]['players']} if old else {}
    table=''
    for pid,name,points,rank,villages in sorted(players,key=lambda x:-x[2]):
        d=points-prev[pid][2] if pid in prev else None
        table+=f'<tr><td>{html.escape(name)}</td><td>{fmt(points)}</td><td>{fmt(d)}</td><td>{fmt(rank)}</td><td>{fmt(villages)}</td></tr>'
    before=[x for x in hist[:-1] if x.get('players') is not None]
    events='Sem observação anterior comparável.'
    if before:
        a={x[0]:x[1] for x in before[-1]['players']};b={x[0]:x[1] for x in players}
        events='Entradas observadas: '+(', '.join(html.escape(b[i]) for i in sorted(b.keys()-a.keys())) or 'nenhuma')+' · Saídas observadas: '+(', '.join(html.escape(a[i]) for i in sorted(a.keys()-b.keys())) or 'nenhuma')
        events+=' (comparação com '+local(before[-1]['time']).strftime('%d/%m %H:%M')+')'
    rivals=''.join(f"<tr><td>{html.escape(t['name'])}</td><td>{t['rank']}</td><td>{fmt(t['points'])}</td></tr>" for t in s.get('rivals',[]))
    return '<h2>Membros — dados públicos</h2><p>'+events+'</p><table><thead><tr><th>Jogador</th><th>Pontos</th><th>Δ 24h</th><th>Posição</th><th>Aldeias</th></tr></thead><tbody>'+table+'</tbody></table><h2>Tribos próximas no ranking</h2><table><tr><th>Tribo</th><th>Posição</th><th>Pontos</th></tr>'+rivals+'</table><p>Variações de membros e aldeias não equivalem necessariamente a conquistas ou perdas militares.</p>'

def compact_charts(hist, out, demo=False):
    """Seven independent images for the in-game announcement."""
    s=hist[-1]
    cutoff=datetime.fromisoformat(s['time'])-timedelta(days=30)
    data=[x for x in hist if datetime.fromisoformat(x['time'])>=cutoff]
    fields=[('points','Pontos',GREEN),('rank','Classificação mundial',GOLD),
            ('villages','Aldeias',BLUE),('members','Membros',RED),
            ('oda','Ataque (ODA)',RED),('odd','Defesa (ODD)',BLUE),
            ('od','Adversários derrotados (OD)',GOLD)]
    for key,label,color in fields:
        f=plt.figure(figsize=(6.2,1.65),dpi=100,facecolor=BG)
        a=f.add_axes([.36,.24,.60,.65],facecolor=PANEL)
        for spine in a.spines.values(): spine.set_color('#d6c39d')
        value=s.get(key)
        f.text(.04,.78,label,fontsize=9.5,color=MUTED,weight='bold')
        f.text(.04,.49,('#' if key=='rank' and value is not None else '')+fmt(value),
               fontsize=19 if len(fmt(value))<9 else 15,color=INK,weight='bold')
        d=delta(hist,key)
        ds='24h: sem histórico' if d is None else '24h: '+('+' if d>0 else '')+fmt(d)
        f.text(.04,.28,ds,fontsize=8,color=MUTED if d in (None,0) else (GREEN if (d<0 if key=='rank' else d>0) else RED))
        times=[local(x['time']) for x in data]
        vals=[x.get(key) for x in data]
        if not any(v is not None for v in vals):
            a.text(.5,.5,'Dados ainda indisponíveis',ha='center',va='center',transform=a.transAxes,color=MUTED,fontsize=9)
            a.set_xticks([]);a.set_yticks([])
        else:
            a.plot(times,vals,color=color,linewidth=2,marker='o' if len(data)<=12 else None,markersize=3.5)
            if len(times)==1:
                a.set_xlim(times[0]-timedelta(minutes=60),times[0]+timedelta(minutes=60))
                a.set_xticks(times)
            else:
                a.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=2,maxticks=4,tz=TZ))
            span=times[-1]-times[0]
            a.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M' if span<timedelta(days=1) else '%d/%m',tz=TZ))
            a.yaxis.set_major_locator(MaxNLocator(integer=True,nbins=3))
            a.yaxis.set_major_formatter(FuncFormatter(lambda x,p:fmt(int(x))))
            a.tick_params(labelsize=7,colors=MUTED,length=2,pad=2)
            a.grid(axis='y',color='#ddd0b3',alpha=.8)
            if key=='rank': a.invert_yaxis()
        f.text(.04,.065,'DEMONSTRAÇÃO' if demo else 'PT119 | '+str(len(data))+' observações',color=MUTED,fontsize=7.5)
        f.text(.96,.065,'Consulta: '+local(s['time']).strftime('%d/%m %H:%M %Z'),ha='right',color=MUTED,fontsize=7.5)
        save(f,out,'grafico-'+key+'.png')

def render(hist,out,demo=False):
    out.mkdir(parents=True,exist_ok=True); s=hist[-1]
    summary(hist,out,demo)
    compact_charts(hist,out,demo)
    chart(hist,out,'Evolução económica e territorial',[('Pontos','points',GREEN),('Classificação (menor é melhor)','rank',GOLD),('Aldeias','villages',BLUE),('Membros','members',RED)],'evolucao.png',demo,30)
    chart(hist,out,'Desempenho militar',[('Ataque · ODA','oda',RED),('Defesa · ODD','odd',BLUE),('Total · OD','od',GOLD),('Pontos de todos','all_points',GREEN)],'militar.png',demo,30)
    # Additional historical views at stable addresses.
    for days in [7,15,60,90]:
        chart(hist,out,'Evolução económica e territorial',[('Pontos','points',GREEN),('Classificação','rank',GOLD),('Aldeias','villages',BLUE),('Membros','members',RED)],f'evolucao-{days}d.png',demo,days)
    name=html.escape(s['name']); world=html.escape(s['world']); stamp=html.escape(local(s['time']).strftime('%d/%m/%Y %H:%M %Z'))
    (out/'index.html').write_text(f'''<!doctype html><html lang="pt-PT"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{name} — Estatísticas</title><style>body{{background:#f3e5c6;color:#35291f;font:16px system-ui;margin:24px auto;max-width:1008px;padding:0 12px}}img{{width:100%;height:auto;display:block;margin:12px 0}}a{{color:#8b302b}}code{{overflow-wrap:anywhere}}table{{width:100%;border-collapse:collapse;background:#fff8e9}}th,td{{padding:8px;border:1px solid #d6c39d;text-align:left}}</style><h1>{name} — {world}</h1><p>{'DEMONSTRAÇÃO • Dados fictícios' if demo else 'Dados públicos do jogo'} · Última consulta: {stamp}. Não equivale à hora de atualização do servidor.</p><img src="resumo.png" alt="Resumo"><img src="evolucao.png" alt="Evolução"><img src="militar.png" alt="Estatísticas militares"><p>Outros períodos: <a href="evolucao-7d.png">7 dias</a> · <a href="evolucao-15d.png">15 dias</a> · <a href="evolucao-60d.png">60 dias</a> · <a href="evolucao-90d.png">90 dias</a>.</p><p>Sem dados militares privados, horas online ou farm. Histórico inicia na instalação. Em caso de falha, os gráficos anteriores mantêm a data da última consulta bem-sucedida.</p>{member_html(hist)}<h2>BBCode para o anúncio</h2><p><code id="bb"></code></p><script>document.getElementById('bb').textContent=['resumo.png','evolucao.png','militar.png'].map(x=>'[img]'+new URL(x,location.href).href+'[/img]').join('  ')</script></html>''',encoding='utf-8')

def demo_history():
    now=datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0)
    return [dict(id=999,world='pt119',name='Hunters Anti Pest',tag='HAP',time=(now-timedelta(days=9-i)).isoformat(),points=1300+i*330,all_points=1300+i*330,rank=53-i*3,villages=5+i//2,members=5+i//2,oda=100+i*120,odd=50+i*35,od=150+i*155) for i in range(10)]

def main():
    p=argparse.ArgumentParser();p.add_argument('--demo',action='store_true');p.add_argument('--output',type=Path);args=p.parse_args()
    if args.demo:
        render(demo_history(),args.output or ROOT/'demo',True);return
    cfg=json.loads((ROOT/'config.json').read_text(encoding='utf-8')); history_path=ROOT/'data'/'history.json'
    history=json.loads(history_path.read_text(encoding='utf-8')) if history_path.exists() else []
    s=collect(cfg,history);history=append_history(history,s,int(cfg.get('history_days',90)))
    render(history,args.output or ROOT/'public')
    history_path.parent.mkdir(exist_ok=True);tmp=history_path.with_suffix('.tmp');tmp.write_text(json.dumps(history,ensure_ascii=False,separators=(',',':'))+'\n',encoding='utf-8');tmp.replace(history_path)
    print('Consulta guardada:',s['name'],s['id'],s['time'])
if __name__=='__main__': main()
