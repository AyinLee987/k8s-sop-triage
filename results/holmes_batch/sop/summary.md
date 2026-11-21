# HolmesGPT batch summary

Grading is provisional (keyword match on the free-text answer); read the answers before trusting it.

| scenario | runs | auto_correct | names noise | tokens (mean / min-max) | model calls | tool calls | wall s |
| --- | --- | --- | --- | --- | --- | --- | --- |
| R01 | 3 (0 failed) | 3/3 | 0/3 | 324532 / 291182-355712 | 14 / 13-16 | 29 / 24-33 | 139 / 115-157 |
| R02 | 3 (0 failed) | 3/3 | 0/3 | 210225 / 136448-266391 | 10 / 8-12 | 21 / 15-26 | 97 / 81-116 |
| R03 | 3 (0 failed) | 3/3 | 0/3 | 329980 / 320595-348389 | 12 / 11-13 | 26 / 22-31 | 165 / 151-183 |
| R04 | 3 (0 failed) | 3/3 | 0/3 | 265450 / 196948-309313 | 11 / 9-12 | 25 / 22-28 | 100 / 94-105 |
| R05 | 3 (0 failed) | 3/3 | 0/3 | 207910 / 159026-253053 | 11 / 9-13 | 19 / 16-21 | 86 / 77-94 |
| R06 | 3 (0 failed) | 3/3 | 0/3 | 279265 / 162753-477396 | 12 / 9-19 | 25 / 15-38 | 87 / 68-120 |
| R07 | 3 (0 failed) | 0/3 | 0/3 | 328275 / 238008-394034 | 12 / 10-14 | 30 / 27-32 | 136 / 113-148 |
| R08 | 3 (0 failed) | 3/3 | 0/3 | 211515 / 201776-227739 | 10 / 9-10 | 26 / 22-28 | 105 / 87-122 |
| R09 | 3 (0 failed) | 3/3 | 0/3 | 190994 / 165441-231459 | 10 / 9-11 | 20 / 17-23 | 81 / 77-86 |

Overall auto_correct: 24/27 successful runs (0 runs failed).
A scenario is stable when all its runs agree (all correct or all wrong).
