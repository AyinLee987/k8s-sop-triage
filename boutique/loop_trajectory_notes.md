# Where the judgement goes wrong: reading the loop-pilot trajectories (2026-11-17)

Source: `results/holmes_loop/loop-pilot/<id>-round<n>.json` on the laptop (12 scenarios, 32 rounds). Each LLM call
carries the model's `reasoning_content`; counts below are keyword counts over round 1, quotes are verbatim.

| scenario | LLM calls | tool calls | CPU first raised at call | a network fault first raised at call | "injected / lab / scenario" mentions | "can't verify" remarks |
| --- | --- | --- | --- | --- | --- | --- |
| BH01 | 26 | 43 | 2 | 16 | 42 | 14 |
| BH02 | 20 | 42 | 3 | 7 | 81 | 15 |
| BH03 | 14 | 28 | 2 | 4 | 58 | 11 |
| BH04 | 17 | 31 | 2 | 3 | 101 | 21 |
| BH05 | 15 | 33 | 2 | 5 | 66 | 13 |
| BH06 | 14 | 26 | 2 | 6 | 39 | 12 |
| BH08 | 24 | 40 | 2 | 3 | 77 | 9 |
| BH11 | 16 | 33 | 2 | 3 | 62 | 9 |
| BH12 | 15 | 31 | 3 | 3 | 73 | 11 |
| BH13 | 20 | 37 | 3 | 4 | 77 | 10 |
| BH14 | 15 | 28 | 2 | 7 | 55 | 10 |
| BH15 | 11 | 23 | 5 | 5 | 8 | 1 |

1. **The CPU theory comes before the evidence.** In 11 of 12 runs it appears at call 2 or 3, right after the
   Deployment specs (with their CPU limits) are read and before any latency has been measured.
2. **The agent reasons about what a test would inject.** "A classic scenario for this kind of benchmark:
   recommendationservice has a CPU limit too low" (BH04); "in this lab-like scenario, the typical injected fault is a low
   CPU limit" (BH13); "I think the intended injected fault is the adservice scale-to-zero" (BH06). The prompt never says
   it is a test; the node name (`tiktok-k8s-lab-control-plane`) and the demo application do.
3. **Neither theory can be checked read-only, and it says so** ("check whether currencyservice pod's CPU is throttled —
   can't"). The choice is then made by resemblance: "This smells like CPU throttling: cgroup period is 100ms" (BH05).
   Our 200 ms delay happens to be a multiple of the 100 ms CFS period.
4. **The right observation is made and then overruled.** BH05 call 8: "the delay is not in service processing → it's in
   delivery — network path or DNS"; call 13 lists CPU, network and a code-level delay and picks CPU. BH06 measures the
   ad call at 0.2 ms and writes "the adservice outage isn't the main latency driver", then reverses on a before/after
   comparison: pages were fast before 15:53:39 (the scale-down) and slow after. The real fault was applied about 25 s
   after the decoy, inside the same gap.
5. **What is certain and fixable wins over what explains the symptom.** BH14: "the *definite* injected fault is
   recommendationservice at 0 replicas"; BH06: "This is an allowed op and matches the confirmed deliberate scale-down".
6. **When a hard discriminator exists it is used.** BH13 round 1: "an i/o timeout means dropped packets → network
   problem on the redis pod's link, not CPU" (the kernel answers a SYN even when the process is throttled).
7. **The two runs that stayed wrong.** BH04 refuted CPU for three services one after another and ended with a list of
   "Unexplained" items instead of a new theory; its last call alone reasons for 52,000 characters. BH15 read a
   24% → 14% change in a noisy share as a partial success.

Consequences for the set-up: the decoy should not precede the fault by a fixed, short interval; the delay should not be
a multiple of 100 ms; and results describe an agent that suspects it is being tested.
