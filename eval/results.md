# CityWheels evaluation

Model: fake deterministic tool-calling stand-in. No API key and no network.

Passed: 23 / 23

Costly failures: 0
Ordinary failures: 0
Weighted miss: 0

A question still counts once in the pass line above. A costly miss is a wrong amount, a fee added back, a refund on a booking that cannot be cancelled, an invented booking, an automatic storm refund, a change inside 24 hours, or an age rule that lets the wrong rider through. Each costly miss weighs 5. Each ordinary miss weighs 1.

| Id | Result | Intent | Holdout | Weight | Question | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| saturday-hours | pass | hours |  | 0 | What time do you open on Saturday? |  |
| ebike-day | pass | price |  | 0 | How much is an e-bike for a whole day? |  |
| cancel-cw-1002 | pass | refund_confirmed |  | 0 | I want to cancel booking CW-1002. How much will I get back? |  |
| age-14-ebike | pass | age_denied |  | 0 | Can my 14-year-old rent an e-bike? |  |
| australia | pass | refuse |  | 0 | What is the capital of Australia? |  |
| cancel-cw-1001 | pass | refund_confirmed |  | 0 | I want to cancel booking CW-1001. How much will I get back? |  |
| cancel-cw-1004 | pass | refund_confirmed |  | 0 | I want to cancel booking CW-1004. How much will I get back? |  |
| tokyo | pass | refuse |  | 0 | What is the population of Tokyo? |  |
| where | pass | location |  | 0 | Where are you? |  |
| cancel-cw-1008 | pass | refund_blocked |  | 0 | I want to cancel booking CW-1008. How much will I get back? |  |
| missing-cw-9999 | pass | booking_missing |  | 0 | I want to cancel booking CW-9999. How much will I get back? |  |
| kids-deposit | pass | deposit |  | 0 | How much is the deposit for a kids bike? |  |
| late-return | pass | late_fee |  | 0 | What happens if I return a bike late? |  |
| cancel-cw-1007 | pass | refund_confirmed |  | 0 | I want to cancel booking CW-1007. How much will I get back? |  |
| helmet | pass | safety |  | 0 | Do I have to wear a helmet? |  |
| ocean-poem | pass | refuse |  | 0 | Write a poem about the ocean. |  |
| storm-cw-1001 | pass | weather | yes | 0 | A storm warning was issued for the day of booking CW-1001. Do I automatically get a full refund? |  |
| change-cw-1003 | pass | booking_change | yes | 0 | Can I change the date of booking CW-1003? |  |
| change-cw-1001 | pass | booking_change | yes | 0 | Can I change the bike type of booking CW-1001? |  |
| age-17-ebike | pass | age_allowed | yes | 0 | Can my 17-year-old rent an e-bike? |  |
| request-cancel-cw-1002 | pass | cancel_request |  | 0 | Please cancel my booking CW-1002. |  |
| request-cancel-go-ahead-cw-1002 | pass | cancel_request |  | 0 | Go ahead and cancel CW-1002 for me. |  |
| price-only-cw-1002 | pass | price_only |  | 0 | How much would I get back if I cancelled booking CW-1002? |  |

## By intent

| Intent | Passed | Questions | Costly misses |
| --- | --- | --- | --- |
| age_allowed | 1 | 1 | 0 |
| age_denied | 1 | 1 | 0 |
| booking_change | 2 | 2 | 0 |
| booking_missing | 1 | 1 | 0 |
| cancel_request | 2 | 2 | 0 |
| deposit | 1 | 1 | 0 |
| hours | 1 | 1 | 0 |
| late_fee | 1 | 1 | 0 |
| location | 1 | 1 | 0 |
| price | 1 | 1 | 0 |
| price_only | 1 | 1 | 0 |
| refund_blocked | 1 | 1 | 0 |
| refund_confirmed | 4 | 4 | 0 |
| refuse | 3 | 3 | 0 |
| safety | 1 | 1 | 0 |
| weather | 1 | 1 | 0 |

## Holdout

Holdout passed: 4 / 4. These questions were not used to edit the system prompt.

## Failures

No question failed in this run.
