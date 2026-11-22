# HolmesGPT batch summary

Grading is provisional (keyword match on the free-text answer); read the answers before trusting it.

| scenario | runs | auto_correct | names noise | tokens (mean / min-max) | model calls | tool calls | wall s |
| --- | --- | --- | --- | --- | --- | --- | --- |
| CV01 | 3 (0 failed) | 3/3 | 0/3 | 1210453 / 703943-1669113 | 15 / 12-17 | 30 / 27-36 | 459 / 404-551 |
| CT01 | 3 (0 failed) | 3/3 | 0/3 | 1320005 / 867423-1708765 | 20 / 17-23 | 38 / 31-43 | 485 / 389-630 |
| CT04 | 3 (0 failed) | 3/3 | 0/3 | 2546312 / 1562868-4214335 | 27 / 19-44 | 44 / 37-58 | 699 / 599-868 |

Runs: 51 finished, 0 failed. Auto-grade (keyword match, needs a human read): 49/51 correct.
A scenario is stable when all its runs agree (all correct or all wrong).
