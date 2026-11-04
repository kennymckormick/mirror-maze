"""Publication figures from the frozen English evidence exports; no model calls."""
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter

ROOT=Path(__file__).resolve().parent
COLORS=['#547c90','#b47a54','#85739a','#5c8b75']
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False,'axes.edgecolor':'#cbd5da','axes.labelcolor':'#6a7c86','text.color':'#354b57','xtick.color':'#7e909a','ytick.color':'#7e909a','grid.color':'#e6ecef','grid.linewidth':.7,'svg.fonttype':'none','savefig.facecolor':'white'})

def save(fig,name):
    folder=ROOT/'figures';folder.mkdir(exist_ok=True)
    fig.savefig(folder/(name+'.png'),dpi=220,bbox_inches='tight');fig.savefig(folder/(name+'.svg'),bbox_inches='tight');plt.close(fig)

def main():
    study=json.loads((ROOT/'data/study.json').read_text());cases={c['id']:c for c in study['cases']}
    fig,ax=plt.subplots(figsize=(10,3.8),layout='constrained')
    for i,(id,label)in enumerate([('astra-codex/hex-MIRROR-042','Hexagon 042: success at246 decisions'),('astra-codex/hex-MIRROR-043','Hexagon 043: capped at500 decisions')]):
        c=cases[id];ax.plot([p['round']for p in c['curve']],[p['visited']/c['cells']for p in c['curve']],color=COLORS[i],lw=2,label=label.replace('at246','at 246').replace('at500','at 500'))
        p=c['curve'][-1];ax.scatter([p['round']],[p['visited']/c['cells']],color=COLORS[i],s=28,zorder=3)
    ax.axvspan(121,161,color='#b5c0c7',alpha=.22,lw=0);ax.text(170,.17,'41 decisions at38 visited cells'.replace('at38','at 38'),color='#81909a',fontsize=9)
    ax.set(xlim=(0,505),ylim=(0,1),xlabel='Planning decisions',ylabel='Visited cells /331'.replace('/331','/ 331'));ax.yaxis.set_major_formatter(PercentFormatter(1));ax.grid(axis='y');ax.legend(loc='upper left',frameon=False,fontsize=9)
    save(fig,'coverage')
    random=json.loads((ROOT/'data/random-policy-results.json').read_text())
    fig,axes=plt.subplots(1,2,figsize=(11,4),sharey=True,layout='constrained')
    keys=['square-MIRROR-042','square-MIRROR-043','hex-MIRROR-042','hex-MIRROR-043']
    for ax,difficulty in zip(axes,['easy','medium']):
        for i,key in enumerate(keys):
            rows=[r for r in random['aggregates']if r['map']==difficulty+'/'+key]
            ax.plot([r['budget']for r in rows],[r['solve_fraction']for r in rows],marker='o',ms=4,lw=1.9,color=COLORS[i],label=('Square 'if key.startswith('square')else'Hexagon ')+key[-3:])
        ax.set_xscale('log');ax.set_xticks([1000,10000,100000],['1,000','10,000','100,000']);ax.set(title=difficulty.title()+' maps',ylim=(0,1.03),xlabel='Motor-action limit in one plan');ax.grid(axis='y');ax.yaxis.set_major_formatter(PercentFormatter(1))
    axes[0].set_ylabel('Solved trials /500'.replace('/500','/ 500'));axes[1].legend(frameon=False,loc='upper left',fontsize=8)
    save(fig,'blind-policy')
    fig,ax=plt.subplots(figsize=(9,3.1),layout='constrained')
    labels=['Square 042','Square 043','Hexagon 042','Hexagon 043']
    selected=[cases['api/gpt-'+key]for key in keys]
    total=[c['collisions']for c in selected]
    last=[c['curve'][-1]['collisions']-c['curve'][-2]['collisions']for c in selected]
    prefix=[a-b for a,b in zip(total,last)]
    ax.barh(labels,prefix,color=COLORS[0],label='All earlier decisions');ax.barh(labels,last,left=prefix,color=COLORS[1],label='Final decision')
    ax.invert_yaxis();ax.set(xlabel='Collisions in GPT-6.1 Sol direct-API runs');ax.legend(frameon=False,loc='lower right',fontsize=8);ax.grid(axis='x');ax.set_axisbelow(True)
    save(fig,'terminal-collisions')
    print('FIGURES_EXPORTED')

if __name__=='__main__':main()
