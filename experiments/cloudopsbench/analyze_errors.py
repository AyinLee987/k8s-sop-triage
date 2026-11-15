"""Why wrong answers are wrong: classify every wrong run of the 26 changeable cases (sample_disc31.json minus canaries).

  python experiments/cloudopsbench/analyze_errors.py

Uses the benchmark process labels (process-label/.../milestone.json): a milestone counts as covered when the agent made
one of its admissible tool calls. Categories: A no Submit; B a milestone was never covered (key evidence not looked at);
C1-C4 all milestones covered but the answer is wrong (see cls()). Also counts invalid steps: empty model outputs
(output_tokens == max_tokens: the reasoning used up the whole budget) and format errors.
"""


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
