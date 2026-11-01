---- MODULE AgentLoop ----
\* The tool loop in citywheels/agent.py, function run_agent.
\* One model call per round, at most MaxRounds rounds.
\* A turn either has text and no tool calls, or asks for 1..MaxCalls tools.
\* The last round offers no tools, so its tool calls are never run.
\* run_tool in citywheels/tools.py turns bad arguments, unknown tools, and
\* exceptions inside a tool into an error result that the model reads.

EXTENDS Naturals

CONSTANTS MaxRounds, MaxCalls

VARIABLES phase, round, pending, requested, answered, unread, answer

vars == <<phase, round, pending, requested, answered, unread, answer>>

Ends == {"answered", "staff", "crashed"}

TypeOK ==
  /\ phase \in {"asking", "tools"} \cup Ends
  /\ round \in 0..MaxRounds
  /\ pending \in 0..MaxCalls
  /\ requested \in Nat
  /\ answered \in Nat
  /\ unread \in 0..MaxCalls
  /\ answer \in {"none", "model", "staff"}

Init ==
  /\ phase = "asking"
  /\ round = 0
  /\ pending = 0
  /\ requested = 0
  /\ answered = 0
  /\ unread = 0
  /\ answer = "none"

ModelWritesText ==
  /\ phase = "asking"
  /\ round < MaxRounds
  /\ round' = round + 1
  /\ phase' = "answered"
  /\ answer' = "model"
  /\ unread' = 0
  /\ UNCHANGED <<pending, requested, answered>>

ModelWritesNothing ==
  /\ phase = "asking"
  /\ round < MaxRounds
  /\ round' = round + 1
  /\ phase' = "staff"
  /\ answer' = "staff"
  /\ unread' = 0
  /\ UNCHANGED <<pending, requested, answered>>

ModelAsksForTools ==
  /\ phase = "asking"
  /\ round < MaxRounds - 1
  /\ \E n \in 1..MaxCalls :
       /\ pending' = n
       /\ requested' = requested + n
  /\ round' = round + 1
  /\ phase' = "tools"
  /\ unread' = 0
  /\ UNCHANGED <<answered, answer>>

ToolReturns ==
  /\ phase = "tools"
  /\ pending > 0
  /\ pending' = pending - 1
  /\ answered' = answered + 1
  /\ unread' = unread + 1
  /\ UNCHANGED <<phase, round, requested, answer>>

ModelAsksWithoutTools ==
  /\ phase = "asking"
  /\ round = MaxRounds - 1
  /\ round' = round + 1
  /\ phase' = "staff"
  /\ answer' = "staff"
  /\ unread' = 0
  /\ UNCHANGED <<pending, requested, answered>>

ToolFails ==
  /\ phase = "tools"
  /\ pending > 0
  /\ pending' = pending - 1
  /\ answered' = answered + 1
  /\ unread' = unread + 1
  /\ UNCHANGED <<phase, round, requested, answer>>

RoundEnds ==
  /\ phase = "tools"
  /\ pending = 0
  /\ phase' = "asking"
  /\ UNCHANGED <<round, pending, requested, answered, unread, answer>>

Done ==
  /\ phase \in Ends
  /\ UNCHANGED vars

Next ==
  ModelWritesText \/ ModelWritesNothing \/ ModelAsksForTools
  \/ ModelAsksWithoutTools \/ ToolReturns \/ ToolFails \/ RoundEnds \/ Done

Spec ==
  /\ Init /\ [][Next]_vars
  /\ WF_vars(ModelWritesText \/ ModelWritesNothing \/ ModelAsksForTools \/ ModelAsksWithoutTools \/ ToolReturns \/ ToolFails \/ RoundEnds)

ModelSeesEveryResult == phase = "asking" => answered = requested

NeverPastTheLimit == round <= MaxRounds

FinishedMeansAnswered == phase \in {"answered", "staff"} => answer # "none"

Terminates == <>(phase \in Ends)

NoCrash == phase # "crashed"

NoToolResultThrownAway == phase = "staff" => unread = 0

====
