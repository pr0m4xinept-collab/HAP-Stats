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

def announcement_panels(hist,out,demo=False):
    """Compact images for the game announcement. Public observations only."""
    from matplotlib.patches import Polygon
    from matplotlib.font_manager import FontProperties
    s=hist[-1]
    now=datetime.fromisoformat(s['time'])
    data=[x for x in hist if datetime.fromisoformat(x['time'])>=now-timedelta(days=30)]
    stamp=local(s['time']).strftime('%d/%m/%Y %H:%M %Z')
    bg='#f3e5c6'; border='#cbb58b'
    def footer(f):
        f.text(.035,.035,'DEMONSTRAÇÃO' if demo else 'PT119 · '+str(len(data))+' consultas',fontsize=7,color=MUTED)
        f.text(.965,.035,'Consulta: '+stamp,ha='right',fontsize=7,color=MUTED)
    def panel(f,rect):
        a=f.add_axes(rect,facecolor=PANEL);a.set_xticks([]);a.set_yticks([])
        for sp in a.spines.values(): sp.set_color(border)
        return a
    def trend(a,key,color):
        times=[local(x['time']) for x in data]; vals=[x.get(key) for x in data]
        if not any(v is not None for v in vals):
            a.text(.5,.5,'Dados indisponíveis',transform=a.transAxes,ha='center',va='center',color=MUTED,fontsize=9)
            return
        a.plot(times,vals,color=color,lw=1.8,marker='o' if len(times)<=12 else None,markersize=3)
        if len(times)==1:
            a.set_xlim(times[0]-timedelta(hours=1),times[0]+timedelta(hours=1));a.set_xticks(times)
        else: a.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=2,maxticks=3,tz=TZ))
        a.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M' if times[-1]-times[0]<timedelta(days=1) else '%d/%m',tz=TZ))
        a.yaxis.set_major_locator(MaxNLocator(integer=True,nbins=3))
        a.yaxis.set_major_formatter(FuncFormatter(lambda x,p:fmt(int(x))))
        a.tick_params(labelsize=7,colors=MUTED,length=2,pad=2)
        a.grid(axis='y',color='#ddd0b3',alpha=.8)
        if key=='rank': a.invert_yaxis()
    def change(key):
        d=delta(hist,key)
        if d is None: return '24h: sem histórico',MUTED
        if key=='rank': return ('24h: '+str(abs(d))+' posições '+('ganhas' if d<0 else 'perdidas')) if d else '24h: sem alteração', GREEN if d<0 else RED if d>0 else MUTED
        return '24h: '+('+' if d>0 else '')+fmt(d), GREEN if d>0 else RED if d<0 else MUTED
    # Original vector heraldic motif, independent of third-party images.
    f=plt.figure(figsize=(6.2,1.55),dpi=120,facecolor=bg)
    a=f.add_axes([.035,.08,.22,.84]);a.set_xlim(0,100);a.set_ylim(0,100);a.set_aspect('equal');a.axis('off')
    a.add_patch(Polygon([(50,97),(89,78),(89,43),(78,20),(50,3),(22,20),(11,43),(11,78)],closed=True,facecolor='#392a25',edgecolor=GOLD,lw=2))
    a.add_patch(Polygon([(50,88),(81,72),(81,45),(71,25),(50,13),(29,25),(19,45),(19,72)],closed=True,facecolor='#762722',edgecolor='#c3a163',lw=1))
    a.plot([32,68],[70,34],color='#ddc692',lw=3);a.plot([68,32],[70,34],color='#ddc692',lw=3)
    a.text(50,51,'HAP',ha='center',va='center',color='#fff1c7',fontsize=13,weight='bold',bbox=dict(facecolor='#762722',edgecolor='none',pad=1))
    f.text(.62,.72,'HUNTERS ANTI PEST',ha='center',weight='bold',fontsize=17,color='#762722')
    f.text(.62,.46,'ESQUADRÃO DE ELITE',ha='center',weight='bold',fontsize=12,color=INK)
    f.text(.62,.22,'LEALDADE  ·  ESTRATÉGIA  ·  UNIÃO  ·  CONQUISTA',ha='center',fontsize=7.5,color=MUTED)
    save(f,out,'hap-brasao.png')
    # Two-by-two economic dashboard with readable axes and compact spacing.
    f=plt.figure(figsize=(6.2,3.55),dpi=120,facecolor=bg)
    for i,(label,key,color) in enumerate([('Pontos','points',GREEN),('Classificação mundial','rank',GOLD),('Aldeias','villages',BLUE),('Membros','members',RED)]):
        x=.035+(i%2)*.48;y=.54-(i//2)*.45
        a=panel(f,[x,y,.45,.41])
        a.text(.055,.89,label,transform=a.transAxes,fontsize=9,weight='bold',color=MUTED)
        a.text(.055,.68,('#' if key=='rank' else '')+fmt(s.get(key)),transform=a.transAxes,fontsize=19,weight='bold',color=INK)
        msg,c=change(key);a.text(.96,.72,msg,transform=a.transAxes,ha='right',fontsize=7,color=c)
        b=f.add_axes([x+.085,y+.07,.34,.19],facecolor=PANEL);trend(b,key,color)
        for sp in b.spines.values(): sp.set_color(border)
    footer(f);save(f,out,'hap-economia.png')
    # Military data: no large empty charts; missing is never interpreted as zero.
    f=plt.figure(figsize=(6.2,1.15),dpi=120,facecolor=bg)
    for i,(label,key,color) in enumerate([('Ataque · ODA','oda',RED),('Defesa · ODD','odd',BLUE),('Total · OD','od',GOLD)]):
        a=panel(f,[.035+i*.32,.22,.29,.72])
        a.text(.07,.79,label,transform=a.transAxes,fontsize=9,weight='bold',color=color)
        a.text(.07,.43,fmt(s.get(key)),transform=a.transAxes,fontsize=18,weight='bold',color=INK)
        msg,c=change(key);a.text(.07,.12,'Indisponível' if s.get(key) is None else msg,transform=a.transAxes,fontsize=8,color=c)
    footer(f);save(f,out,'hap-militar.png')
    # Daily leaders use only comparable public points; new members excluded.
    prior=[x for x in hist if x.get('players') is not None and now-timedelta(hours=27)<=datetime.fromisoformat(x['time'])<=now-timedelta(hours=24)]
    players=s.get('players')
    leaders=[]
    if prior and players is not None:
        prev={p[0]:p for p in prior[-1]['players']}
        leaders=sorted([(p[1],p[2]-prev[p[0]][2]) for p in players if p[0] in prev],key=lambda p:(-p[1],p[0]))[:5]
    f=plt.figure(figsize=(6.2,2.15),dpi=120,facecolor=bg)
    a=panel(f,[.035,.14,.45,.8]);b=panel(f,[.515,.14,.45,.8])
    a.text(.06,.89,'TOP 5 · EVOLUÇÃO 24H',transform=a.transAxes,color=RED,weight='bold',fontsize=9)
    if leaders:
        for i,(name,gain) in enumerate(leaders):
            y=.72-i*.14
            a.text(.06,y,str(i+1)+'. '+name[:21],transform=a.transAxes,fontsize=8,color=INK,parse_math=False)
            a.text(.94,y,('+' if gain>0 else '')+fmt(gain),transform=a.transAxes,ha='right',fontsize=8,weight='bold',color=GREEN if gain>0 else RED if gain<0 else MUTED)
    else:
        a.text(.5,.49,'A aguardar 24h de histórico\ncomparável dos membros.',ha='center',va='center',transform=a.transAxes,fontsize=8,color=MUTED)
    b.text(.06,.89,'TRIBO · CRESCIMENTO 24H',transform=b.transAxes,color=RED,weight='bold',fontsize=9)
    for i,(label,key) in enumerate([('Pontos','points'),('Posição','rank'),('Aldeias','villages'),('Membros','members')]):
        d=delta(hist,key);y=.72-i*.14
        b.text(.06,y,label,transform=b.transAxes,fontsize=8,color=INK)
        v='—' if d is None else ('+' if (d<0 if key=='rank' else d>0) else '')+fmt(-d if key=='rank' else d)
        b.text(.94,y,v,transform=b.transAxes,ha='right',fontsize=9,weight='bold',color=GREEN if d is not None and (d<0 if key=='rank' else d>0) else MUTED)
    d=delta(hist,'points');base=s['points']-d if d is not None else 0
    b.text(.06,.09,'Variação: '+(f'{100*d/base:+.2f}%'.replace('.',',') if base>0 else '—'),transform=b.transAxes,fontsize=8,color=MUTED)
    footer(f);save(f,out,'hap-destaques.png')

def announcement_panel(hist, out, demo=False):
    """One compact, automatically refreshed image matching the HAP announcement."""
    from matplotlib.patches import FancyBboxPatch, Polygon
    s=hist[-1]
    f=plt.figure(figsize=(6.2,14.5),dpi=120,facecolor=BG)
    a=f.add_axes([0,0,1,1]); a.set_xlim(0,620); a.set_ylim(1450,0); a.axis('off')
    border='#c4ab76'; dark='#77562e'; cream='#fff5dc'; red='#8b302b'
    def text(x,y,label,size=10,color=INK,bold=False,align='center'):
        a.text(x,y,str(label),ha=align,va='center',fontsize=size,color=color,
               fontweight='bold' if bold else 'normal',fontfamily='DejaVu Sans')
    def box(x,y,w,h,fill=cream,edge=border):
        a.add_patch(FancyBboxPatch((x,y),w,h,boxstyle='round,pad=0,rounding_size=7',
                    facecolor=fill,edgecolor=edge,linewidth=1.1))
    def line(y):
        a.plot([24,596],[y,y],color=border,lw=1.3)
    box(5,5,610,1440,BG,border)
    # Crisp heraldic shield; no outline on letters.
    for xs,xe,y in [(55,175,108),(445,565,108),(90,205,137),(415,530,137)]:
        a.plot([xs,xe],[y,y],color=border,lw=2)
    shield=[(310,24),(385,54),(385,118),(374,151),(347,181),(310,203),(273,181),(246,151),(235,118),(235,54)]
    a.add_patch(Polygon(shield,closed=True,facecolor='#392a25',edgecolor='#a77b35',linewidth=3))
    inner=[(310+(x-310)*.83,113+(y-113)*.83) for x,y in shield]
    a.add_patch(Polygon(inner,closed=True,facecolor='#762722',edgecolor='#c3a163',linewidth=1.6))
    for p,q in [((271,76),(349,152)),((349,76),(271,152))]:
        a.plot([p[0],q[0]],[p[1],q[1]],color='#ddc692',lw=4,solid_capstyle='round')
    for p,q in [((329,149),(348,130)),((272,130),(291,149))]:
        a.plot([p[0],q[0]],[p[1],q[1]],color='#ddc692',lw=3,solid_capstyle='round')
    a.add_patch(FancyBboxPatch((280,99),60,29,boxstyle='round,pad=1,rounding_size=4',facecolor='#392a25',edgecolor='#c3a163',lw=1))
    text(310,113,'HAP',13,'#fff1c7',True)
    text(310,228,s['name'].upper(),17,red,True)
    text(310,251,'ESQUADRÃO DE ELITE',11,dark,True)
    text(310,274,'Lealdade  •  Estratégia  •  União  •  Conquista',8,MUTED)
    line(295)
    text(310,318,'A NOSSA MISSÃO',11,red,True)
    text(310,343,'Proteger os nossos membros. Fortalecer o nosso território.',9.2,INK)
    text(310,362,'Evoluir como uma equipa.',9.2,INK)
    line(383)
    text(310,406,'CENTRAL DE ESTATÍSTICAS',13,red,True)
    text(310,430,('DEMONSTRAÇÃO — DADOS FICTÍCIOS' if demo else 'Mundo '+s['world'][2:]+'  •  Dados públicos do jogo'),8.5,MUTED)
    cutoff=datetime.fromisoformat(s['time'])-timedelta(days=7)
    recent=[z for z in hist if datetime.fromisoformat(z['time'])>=cutoff]
    fields=[('Pontos','points',GREEN),('Classificação','rank',GOLD),('Aldeias','villages',BLUE),('Membros','members',RED)]
    for i,(label,key,color) in enumerate(fields):
        x=24+290*(i%2); y=447+162*(i//2)
        box(x,y,282,154)
        text(x+141,y+19,label,10,dark,True)
        val=s.get(key)
        text(x+141,y+47,('#' if key=='rank' and val is not None else '')+fmt(val),18,INK,True)
        ax=f.add_axes([(x+18)/620,1-(y+138)/1450,246/620,67/1450],facecolor=cream)
        ts=[local(z['time']) for z in recent]; vs=[z.get(key) for z in recent]
        ax.spines[['top','right']].set_visible(False)
        ax.spines[['left','bottom']].set_color('#d6c39d')
        ax.tick_params(left=False,bottom=False,labelleft=False,labelbottom=False)
        ax.yaxis.set_major_locator(MaxNLocator(nbins=3,integer=True))
        ax.grid(axis='y',color='#ddd0b3',lw=.7)
        if any(v is not None for v in vs):
            ax.plot(ts,vs,color=color,lw=2.2,marker='o' if len(ts)<=8 else None,markersize=3.8)
            if len(ts)==1: ax.set_xlim(ts[0]-timedelta(minutes=30),ts[0]+timedelta(minutes=30))
            if key=='rank': ax.invert_yaxis()
        else: ax.text(.5,.5,'Indisponível',transform=ax.transAxes,ha='center',color=MUTED,fontsize=8)
    text(310,782,'Últimos 7 dias  •  '+str(len(recent))+' observações',8,MUTED)
    line(801)
    text(310,824,'DESEMPENHO MILITAR',11,red,True)
    for i,(label,key) in enumerate([('Ataque','oda'),('Defesa','odd'),('Total OD','od')]):
        x=24+193*i; box(x,847,186,84)
        text(x+93,863,label,9,dark,True)
        val=s.get(key)
        text(x+93,888,fmt(val),14 if len(fmt(val))<10 else 10,INK,True)
        text(x+93,916,'Indisponível' if val is None else 'Pontos de adversários',7.4,MUTED)
    line(948)
    text(310,971,'DESTAQUES DAS ÚLTIMAS 24H',11,red,True)
    box(24,994,572,153,cream,cream)
    target=datetime.fromisoformat(s['time'])-timedelta(days=1)
    eligible=[z for z in hist if z.get('players') is not None and datetime.fromisoformat(z['time'])<=target and target-datetime.fromisoformat(z['time'])<=timedelta(hours=3)]
    best=None
    if eligible and s.get('players') is not None:
        prev={p[0]:p[2] for p in eligible[-1]['players']}
        changes=[(p[2]-prev[p[0]],p[1]) for p in s['players'] if p[0] in prev]
        if changes: best=max(changes,key=lambda p:p[0])
    dp=delta(hist,'points'); dv=delta(hist,'villages')
    def signed(v): return '—' if v is None else ('+' if v>0 else '')+fmt(v)
    if best is None and dp is None and dv is None: text(310,1012,'A aguardar histórico suficiente',8.5,MUTED)
    else: text(310,1012,'Comparação com dados de cerca de 24h atrás',8.5,MUTED)
    best_label=(best[1][:25]+'  '+signed(best[0])+' pts') if best is not None else '—'
    text(310,1037,'Melhor evolução individual  •  '+best_label,9,INK,True)
    text(310,1065,'Crescimento diário da tribo  •  '+signed(dp)+' pts' if dp is not None else 'Crescimento diário da tribo  •  —',9,INK)
    text(310,1093,'Expansão territorial  •  '+signed(dv)+' aldeias' if dv is not None else 'Expansão territorial  •  —',9,INK)
    text(310,1128,'Variação de aldeias, não conquistas confirmadas',7.2,MUTED)
    line(1162)
    text(310,1186,'HISTÓRICO DE EVOLUÇÃO',11,red,True)
    for i,n in enumerate(['7d','15d','30d','60d','90d']):
        x=209+42*i; box(x,1203,35,34,BG,border);text(x+17.5,1220,n,9,dark,True)
    text(310,1256,'Consultar estatísticas completas • Ligações abaixo',8.5,BLUE,True)
    line(1276)
    for y,t in [(1300,'JUNTOS EVOLUÍMOS.'),(1321,'JUNTOS LUTAMOS.'),(1342,'JUNTOS CONQUISTAMOS.')]:
        text(310,y,t,10.5,red,True)
    text(310,1370,'LÍDER & FUNDADOR',9,dark,True)
    text(310,1390,'pr0m4xine111',9,INK,True)
    text(310,1412,'HAP STATS • Mundo '+s['world'][2:],7.8,MUTED)
    text(310,1431,'Consulta: '+local(s['time']).strftime('%d/%m/%Y %H:%M %Z'),7.8,MUTED)
    save(f,out,'painel.png')

def render(hist,out,demo=False):
    out.mkdir(parents=True,exist_ok=True); s=hist[-1]
    summary(hist,out,demo)
    compact_charts(hist,out,demo)
    announcement_panel(hist,out,demo)
    announcement_panels(hist,out,demo)
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
