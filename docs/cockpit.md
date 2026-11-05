# The operator cockpit

One page where a person manages the cancellation cases: what needs a decision, what happened in each case, which conversations never became a case, who may act without a person, what rules are in force, how the agent did in evaluation, and whether the parts that run the cases work. It is part of this application. The audit gateway and IntentGauge stay the detail views it links to ([ADR 0068 of the gateway](https://github.com/adagora/ai-audit/blob/main/docs/adr/0068-correlation-and-the-application-cockpit.md)).

```sh
python3 -m venv .venv && .venv/bin/pip install -r orchestration/requirements.txt
.venv/bin/python -m orchestration seed          # once; prints one token per role
.venv/bin/python -m orchestration worker        # cases only move while a worker runs
CASEWORK_TOKEN=<supervisor> .venv/bin/python -m cockpit \
  --audit-log ../ai-audit/live/audit.jsonl --audit-cli ../ai-audit/dist/src/cli.js \
  --console-url "http://127.0.0.1:8793/#token=<console token>" \
  --proxy-url http://127.0.0.1:8787 --evals-dir ../evalLOL/runs
```

It listens on `127.0.0.1:8799` only. With `CASEWORK_TOKEN` set it prints a URL that signs you in; without it the page asks for a token. The token stays in the browser tab, and a token in the address bar is accepted at any time. Every flag except `--home` and `--address` is optional, and a screen whose source is missing says so.

## Screens

| Screen | What it answers | Source |
|---|---|---|
| To do | What needs a person now: reviews waiting (soonest quote expiry first), exceptions, cases that stopped moving, grants about to expire, parts that are down | Temporal queries, staff queue, grants |
| Cases | Every case, most urgent first, in words, and the exceptions that were closed | Temporal visibility, staff queue |
| A case | The quote, what happened in order, each model call (model, tools asked for, tokens, what the inspection did), the decision with its reviews and outcome, and the buttons | Temporal history, the audit log joined by the correlation id |
| Conversations | Questions that never opened a case: when, how many model calls, which tools, what the inspection did. The log keeps no customer text | the audit log |
| Authority | Grants with tier, limit, expiry and history; approve a new one, activate, replace, revoke | the grant store |
| Rules | What this build enforces: the review limit, quote lifetime, refund rule, what always goes to a person, timing, evidence sources. Read-only | build defaults and the policy file |
| Quality | The latest IntentGauge runs for this application, the cases of each run with what failed, and the full report one click away | IntentGauge `runs/` |
| Health | Temporal, worker, audit chain, anchor, proxy, latest evaluation, open staff items | live checks |

Quality always says **Not qualified**: the runs use development cases and no human-scored evidence.

## What a person can do here

- **Approve or reject** a refund that needs review. The page sends the digest of the quote it showed. If the quote changed in the meantime the request is refused with `quote_changed`. A review may be given before the customer confirms; the refund still waits for the customer.
- **Close an exception** or, for a cancelled booking whose refund did not settle, **complete the refund** as a supervisor.
- **Replay a decision** from the log alone, and **download its receipt** as SVG, JSON or full HTML. Only decisions that belong to the case can be asked for.
- **Approve a grant** (reviewer), **activate** it (deployer) and **replace** or **revoke** one. A transact grant needs a ticked confirmation that nothing has been qualified. The limit is capped at 5000 EUR per refund and a grant lasts at most a year. A grant is bound to the current release.
- **Open** a decision or a model call in the audit console, straight on its page.

Consent is the customer's act and has no button here.

## Safety

- Loopback only. A request must carry the right `Host` and, when present, `Origin`.
- Every call needs `Authorization: Bearer <token>`. A token without the operator, reviewer or deployer role gets `forbidden`. A POST must be small JSON.
- The page builds no markup from data and sends a strict content policy. Evaluation reports open in a page with no network access, under a cookie that only reaches `/evals/`.
- Reads of the audit log never write. Case writes go through the gateway, the same path as the command line. Grants are written to the grant store, which has no other interface yet.
- A receipt carries the exact rule and inputs. SVG and JSON omit reviewer names and notes; the full HTML keeps them. The scanner is not anonymisation, so read what you share.
- The console link carries the console token. Start the audit console without `--reviewer`, so it stays read-only. The workflow takes a review only from its own signal, so a verdict written in that console would not release a case.

## Limits

- The case list reads Temporal on every refresh. It is meant for hundreds of cases, not millions.
- Queries need a worker. Without one a running case shows "Not answering (is a worker running?)", and Health says no worker is polling.
- The audit log is read in full on each case page. Rotate it.
- The identity directory is the fake one from `seed`.
- The rules shown are the defaults of this build. A worker started with other settings may differ.
