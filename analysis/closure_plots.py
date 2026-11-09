"""Plot solver-declared computational closure with independently checked witnesses."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from plot_style import arm_order, arm_style


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--summary',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(); report=json.loads(a.summary.read_text())
    if report['provisional']: raise ValueError('Wait for all scheduled tasks')
    groups=defaultdict(list)
    for c in report['cells']:
        if c['arm']=='kayros-heuristics': continue
        key=c['arm']+(f" / prefix {c['prefix_s']:g} s" if c.get('prefix_s') is not None else '')
        groups[key].append(c)
    fig,ax=plt.subplots(figsize=(10,5.6)); data={}
    for key,rows in sorted(groups.items(),key=lambda item: arm_order(item[1][0]['arm'],item[1][0].get('prefix_s'))):
        horizon=rows[0]['horizon_s']
        times=sorted({0.,horizon}|{c['reported_closure_s'] for c in rows if c['reported_closure_s'] is not None})
        fractions=[]
        for t in times:
            byinstance=defaultdict(list)
            for c in rows:
                byinstance[c['instance_name']].append(float(c['reported_closure_s'] is not None and c['reported_closure_s']<=t))
            fractions.append(sum(sum(v)/len(v) for v in byinstance.values())/len(byinstance))
        label=key.replace('kayros-bpca-cplex','Cold CPLEX').replace('kayros-bpca-highs','Cold HiGHS').replace('kayros-full-cplex','Full CPLEX').replace('lera-exact','Lera CPLEX')
        warned=sum(c.get('reported_closure_with_native_guard_warning',False) for c in rows)
        if warned: label+=f' [{warned} guard warnings]'
        ax.step(times,fractions,where='post',label=label,**arm_style(rows[0]['arm'],rows[0].get('prefix_s')))
        data[key]={'elapsed_s':times,'reported_closure_fraction':fractions,'reported_closures_with_native_guard_warning':warned}
    ax.set(xlabel='Elapsed time from process launch (s)',ylabel='Fraction with reported closure and checked witness',xlim=(0,horizon),ylim=(-.02,1.02))
    ax.grid(alpha=.25)
    ax.set_title(report['wave_name']+' | computational closure under declared conventions')
    fig.legend(*ax.get_legend_handles_labels(),loc='lower center',bbox_to_anchor=(.5,.04),ncol=3,frameon=False)
    fig.text(.5,.012,'All scheduled cells retained. Flagged native guard warnings qualify closure claims. Missing guard evidence is not zero.',ha='center',fontsize=9)
    fig.tight_layout(rect=(0,.23,1,1))
    a.output.mkdir(parents=True,exist_ok=True)
    for ext in ('png','pdf'): fig.savefig(a.output/f'closure-overview.{ext}',dpi=180)
    (a.output/'closure-plot-data.json').write_text(json.dumps(data,indent=2)+'\n')

if __name__=='__main__': main()
