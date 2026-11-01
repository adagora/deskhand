# AgentLoop

TLC reached 34 states and depth 10 with MaxRounds = 3 and MaxCalls = 2. The chart draws 7 states and 11 arrows.
The view hides the step where one tool returns or fails, and the end-state step.

## Rules that hold
TypeOK, ModelSeesEveryResult, NeverPastTheLimit, FinishedMeansAnswered, NoCrash, NoToolResultThrownAway, Terminates, NoDeadlock

## Fixed
- NoCrash broke when a tool raised, for example `calculate_refund` on a booking with an invalid `start`. `run_tool` now returns the exception as an error result.
- NoToolResultThrownAway broke when the model asked for tools in the last round. The last round now offers no tools, and tool calls in it are not run.

Letting the model ask for tools in the last round again makes TLC report a deadlock.
