# Live holdout

This is an older, short run. On 2026-11-05 the system prompt gained one line about `request_cancellation`, and the scorer began to read money in any format. `results-live-openai.md` and `results-live-gemini.md` are the newer runs of all the questions, and the holdout results there match this file. No holdout question was used to edit the prompt.

## openai gpt-4o-mini

Passed: 0 / 4
Costly failures: 0
Ordinary failures: 5
Weighted miss: 5

| Id | Result | Intent | Weight | Notes |
| --- | --- | --- | --- | --- |
| storm-cw-1001 | fail | weather | 1 | expected tool search_policies, called ['get_booking'] |
| change-cw-1003 | fail | booking_change | 1 | answer does not say the change is not possible |
| change-cw-1001 | fail | booking_change | 1 | answer does not say the change is still possible |
| age-17-ebike | fail | age_allowed | 2 | answer does not cite the age minimum of 16 missing '16' |

### storm-cw-1001

A storm warning is not an automatic refund. The staff decide those cases. Please contact the staff at help@citywheels.example or +48 000 000 000 for further assistance regarding your booking CW-1001.

### change-cw-1003

Booking CW-1003 is confirmed for a city bike rental from 15:00 to 18:00 today, June 10, 2026.

To change the date of your booking, please contact the staff directly at help@citywheels.example or +48 000 000 000 for assistance.

### change-cw-1001

Booking CW-1001 is confirmed with a city bike type. To change the bike type, please contact the staff directly at help@citywheels.example or +48 000 000 000 for assistance.

### age-17-ebike

Your 17-year-old can rent an e-bike, but a parent or guardian will need to sign the rental agreement since they are under 18 years old.

## gemini gemini-3.8-flash

Passed: 4 / 4
Costly failures: 0
Ordinary failures: 0
Weighted miss: 0

| Id | Result | Intent | Weight | Notes |
| --- | --- | --- | --- | --- |
| storm-cw-1001 | pass | weather | 0 |  |
| change-cw-1003 | pass | booking_change | 0 |  |
| change-cw-1001 | pass | booking_change | 0 |  |
| age-17-ebike | pass | age_allowed | 0 |  |
