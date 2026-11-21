# HolmesGPT batch summary

Grading is provisional (keyword match on the free-text answer); read the answers before trusting it.

| scenario | runs | auto_correct | names noise | tokens (mean / min-max) | model calls | tool calls | wall s |
| --- | --- | --- | --- | --- | --- | --- | --- |
| R01 | 3 (0 failed) | 3/3 | 0/3 | 369686 / 236103-635553 | 14 / 11-20 | 27 / 23-36 | 148 / 110-221 |
| R02 | 3 (0 failed) | 1/3 | 0/3 | 219079 / 186900-254313 | 11 / 10-12 | 21 / 18-24 | 89 / 82-100 |
| R03 | 3 (0 failed) | 3/3 | 0/3 | 336800 / 305676-368707 | 12 / 11-13 | 27 / 26-29 | 172 / 155-194 |
| R04 | 3 (0 failed) | 3/3 | 0/3 | 259627 / 217184-319907 | 11 / 10-12 | 21 / 19-24 | 100 / 97-104 |
| R05 | 3 (0 failed) | 3/3 | 0/3 | 231650 / 160400-280041 | 11 / 9-12 | 21 / 16-25 | 92 / 76-106 |
| R06 | 3 (0 failed) | 3/3 | 0/3 | 142439 / 119219-170346 | 8 / 7-9 | 14 / 13-16 | 67 / 63-73 |
| R07 | 3 (0 failed) | 0/3 | 0/3 | 243063 / 225293-261384 | 11 / 11-12 | 26 / 25-26 | 90 / 77-103 |
| R08 | 3 (0 failed) | 3/3 | 0/3 | 222117 / 178766-255377 | 10 / 9-11 | 24 / 22-26 | 94 / 87-101 |
| R09 | 3 (0 failed) | 3/3 | 0/3 | 253678 / 150023-378581 | 11 / 8-15 | 23 / 19-29 | 90 / 78-113 |

Overall auto_correct: 22/27 successful runs (0 runs failed).
A scenario is stable when all its runs agree (all correct or all wrong).
