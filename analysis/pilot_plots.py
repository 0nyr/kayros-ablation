"""Plot complete pilot waves with all scheduled cells in the denominator."""
import argparse
import json
from pathlib import Path
from collections import defaultdict
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--summary',type=Path,required=True)
    p.add_argument('--wave',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    report=json.loads(a.summary.read_text())
    if report['provisional']:
        raise ValueError('Finish the scheduled wave before comparative plots')
    groups=defaultdict(list)
    refs={x['instance_name']:x['reference_cost'] for x in report['cells']}
    for c in report['cells']:
        key=c['arm']+(f" / prefix {c['prefix_s']} s" if c.get('prefix_s') is not None else '')
        r=json.loads((a.wave/'cells'/c['run_id']/'result.json').read_text()) if c['run_id'] else {}
        groups[key].append((c,r.get('validation',{}).get('checked_envelope',[])))
    horizons={c['horizon_s'] for c in report['cells']}
    if len(horizons)!=1:
        raise ValueError('Plot requires common horizon')
    horizon=horizons.pop()
    fig,axes=plt.subplots(1,2,figsize=(12,4.8))
    plotdata={}
    for name,items in sorted(groups.items()):
        times=sorted({0.,horizon}|{x['elapsed_s'] for _,env in items for x in env})
        gaps,attained=[],[]
        for t in times:
            instance_gaps=defaultdict(list); instance_attain=defaultdict(list)
            for c,env in items:
                best=None
                for point in env:
                    if point['elapsed_s']<=t: best=point['checked_cost']
                    else: break
                ref=refs[c['instance_name']]
                instance_gaps[c['instance_name']].append(1. if best is None else (best-ref)/(best+ref))
                instance_attain[c['instance_name']].append(float(best is not None and best<=1.01*ref))
            gaps.append(sum(sum(v)/len(v) for v in instance_gaps.values())/len(instance_gaps))
            attained.append(sum(sum(v)/len(v) for v in instance_attain.values())/len(instance_attain))
        label=name.replace('kayros-bpca-','Cold ').replace('kayros-full-cplex','Full CPLEX').replace('kayros-heuristics','ILS').replace('lera-exact','Lera CPLEX')
        axes[0].step(times,gaps,where='post',label=label)
        axes[1].step(times,attained,where='post',label=label)
        plotdata[name]={'elapsed_s':times,'mean_squeezed_gap':gaps,'raw_gap_1pct_attainment':attained}
    axes[0].set_ylabel('Mean squeezed gap to frozen v0.11 reference')
    axes[1].set_ylabel('Fraction attaining raw reference gap <= 1%')
    for ax in axes:
        ax.set_xlabel('Elapsed time from process launch (s)'); ax.set_xlim(0,horizon)
        ax.grid(alpha=.25)
    axes[1].set_ylim(-.02,1.02)
    fig.suptitle(report['wave_name']+' | incumbent-quality pilot',fontsize=12)
    handles,labels=axes[0].get_legend_handles_labels()
    fig.legend(handles,labels,loc='lower center',bbox_to_anchor=(.5,.045),ncol=2,frameon=False)
    fig.text(.5,.012,'All scheduled cells retained. Equal instance weights. Frozen primal references are not proof certificates.',ha='center',fontsize=9)
    fig.tight_layout(rect=(0,.21,1,.94))
    a.output.mkdir(parents=True,exist_ok=True)
    for ext in ['png','pdf']: fig.savefig(a.output/f'pilot-overview.{ext}',dpi=180)
    (a.output/'plot-data.json').write_text(json.dumps(plotdata,indent=2)+'\n')

if __name__=='__main__': main()
