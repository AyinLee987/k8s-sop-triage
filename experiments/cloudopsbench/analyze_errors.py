"""Why wrong answers are wrong: classify every wrong run of the 26 changeable cases (sample_disc31.json minus canaries).

  python experiments/cloudopsbench/analyze_errors.py

Uses the benchmark process labels (process-label/.../milestone.json): a milestone counts as covered when the agent made
one of its admissible tool calls. Categories: A no Submit; B a milestone was never covered (key evidence not looked at);
C1-C4 all milestones covered but the answer is wrong (see cls()). Also counts invalid steps: empty model outputs
(output_tokens == max_tokens: the reasoning used up the whole budget) and format errors.
"""
import json,glob,collections,re,sys
B = '/home/claude/research/bench/Cloud-OpsBench/'  # dataset checkout
s=json.load(open('experiments/cloudopsbench/sample_disc31.json')); can=set(s['canaries'])
cases={c['id']:c for c in s['cases'] if c['id'] not in can}
def norm(v): return str(v).strip().lower()
def match(step,use):
    if step.get('action_name')!=use['tool_name']: return False
    ai=step.get('action_input') or {}
    if not isinstance(ai,dict): return False
    for k,v in (use.get('arguments') or {}).items():
        a=norm(ai.get(k,''))
        vs=[norm(x) for x in v] if isinstance(v,list) else [norm(v)]
        if a not in vs and not any(x in a for x in vs if len(x)>3): return False
    return True
def load(tag):
    out={}
    sc={r['id']:r for r in json.load(open(f'results/cloudopsbench/{tag}/scores.json'))['rows']}
    for f in glob.glob(f'results/cloudopsbench/{tag}/*/*/*/*/[0-9]*.json'):
        p=f.split('/'); cid=f"{p[3]}/{p[5]}/{p[6]}"
        if cid not in cases: continue
        d=json.load(open(f)); r=sc[cid]
        ms=json.load(open(f"{B}process-label/{p[3]}/{p[5]}/{p[6]}/milestone.json"))
        cov={}
        for m in ms['milestones']:
            cov[m['id']+':'+m['role']]=any(match(st,u) for st in d['steps'] for u in m['admissible_tool_uses'])
        inv_empty=sum(1 for st in d['steps'] if st.get('action_type')=='invalid' and not (st.get('raw_model_output') or '').strip())
        inv_fmt=sum(1 for st in d['steps'] if st.get('action_type')=='invalid' and (st.get('raw_model_output') or '').strip())
        if not r.get('finished',True) or d.get('stop_reason')!='submit': oc='没交答案'
        elif r.get('top1'): oc='答对'
        elif r.get('object') and not r.get('cause'): oc='对象对、原因错'
        elif r.get('cause') and not r.get('object'): oc='原因对、对象错'
        else: oc='都错'
        out[cid]=dict(oc=oc,cov=cov,allcov=all(cov.values()),ncov=sum(cov.values()),nms=len(cov),steps=len(d['steps']),inv_empty=inv_empty,inv_fmt=inv_fmt,
                      pred=(r.get('pred') or [{}])[0] if isinstance(r.get('pred'),list) else r.get('pred'),truth=r['truth'],reads=[re.search(r'(\d+)',str(st.get('action_input'))).group(1) for st in d['steps'] if st.get('action_name')=='ReadExperience' and re.search(r'(\d+)',str(st.get('action_input')))])
    return out


def cls(r):
    if r['oc'] == '答对':
        return None
    if r['oc'] == '没交答案':
        return 'A no answer (out of steps)'
    if not r['allcov']:
        return 'B key evidence never looked at'
    p, t = r['pred'] or {}, r['truth']
    if t['root_cause'] == 'pod_network_delay':
        if p.get('fault_object') == t['fault_object']:
            return 'C1 right object, mechanism not separated'
        if p.get('root_cause') == 'pod_cpu_overload' and 'ts-order-service' in str(p.get('fault_object')):
            return 'C2 followed a real but unrelated alert'
        return 'C3 wrong scope or object'
    return 'C4 confused with a similar cause'


if __name__ == '__main__':
    conds = {'base': ['base', 'base-r2', 'base-r3'], 'd2': ['skill-general-d2', 'skill-general-d2-r2', 'skill-general-d2-r3'],
             'd16': ['skill-general-d16', 'skill-general-d16-r2', 'skill-general-d16-r3']}
    runs = []
    for cn, tags in conds.items():
        for t in tags:
            for cid, r in load(t).items():
                r.update(cond=cn, cid=cid)
                runs.append(r)
    tab = collections.defaultdict(collections.Counter)
    for r in runs:
        if cls(r):
            tab[cls(r)][r['cond']] += 1
    for c in sorted(tab):
        print(c, dict(tab[c]), sum(tab[c].values()))
    print('runs', len(runs), 'steps', sum(r['steps'] for r in runs), 'empty outputs', sum(r['inv_empty'] for r in runs),
          'format errors', sum(r['inv_fmt'] for r in runs))
