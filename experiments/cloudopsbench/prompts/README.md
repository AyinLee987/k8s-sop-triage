holmes_system_prompt.md replaces the Cloud-OpsBench reference agent's harness/system_prompt.md (run.py --system-prompt).
It is HolmesGPT's own system prompt (holmes/plugins/prompts/generic_ask.jinja2 at commit 27c623e), sections copied verbatim:
Investigation guidelines, Impact and blast radius, If investigating Kubernetes problems, Special cases.
Left out because they cannot apply to this harness: parallel tool calls (the harness allows one action per step), TodoWrite,
multi-cluster and namespace-scope notes, toolset instructions, permission-error links, fetch_finding_by_id, the impact
write-up format and the style guide (the answer here is a fixed JSON object).
Kept from the benchmark because the harness parses them: "Critical syntax rules" and "Output Protocol", and the task fact
that a scenario contains one primary fault. The benchmark's own instructions (minimum steps, a few concise sentences of
reasoning, decide only the next step) are gone. The "Final Diagnosis Output Requirement" section, which the agent code
appends separately, is unchanged - it still says to finalize once Rank 1 is supported.
After a first try (hp-base: 28 of 93 cases ran out of the 20 steps without submitting, because HolmesGPT's prompt says to
keep investigating and the harness allows one tool call per step) one protocol line was added: submit before the step
budget runs out. Results with that line are tagged hp2-*.
