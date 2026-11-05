# The operator cockpit

One page where a person manages the cancellation cases: what needs a decision, what happened in each case, who may act without a person, how the agent did in evaluation, and whether the parts that run the cases work. It is part of this application. ai-audit and IntentGauge stay the detail views it links to ([ai-audit ADR 0068](https://github.com/adagora/ai-audit/blob/main/docs/adr/0068-correlation-and-the-application-cockpit.md)).

```sh
python3 -m venv .venv && .venv/bin/pip install -r orchestration/requirements.txt
.venv/bin/python -m orchestration seed          # once; prints one token per role
.venv/bin/python -m orchestration worker        # cases only move while a worker runs
CASEWORK_TOKEN=<supervisor> .venv/bin/python -m cockpit \
  --audit-log ../ai-audit/live/audit.jsonl --audit-cli ../ai-audit/dist/src/cli.js \
  --proxy-url http://127.0.0.1:8787 --evals-dir ../evalLOL/runs
```

It listens on `127.0.0.1:8799` only. With `CASEWORK_TOKEN` set it prints a URL that signs you in; without it the page asks for a token. The token stays in the browser tab. Every flag except `--home` and `--address` is optional, and a screen whose source is missing says so.

## Screens

| Screen | What it answers | Source |
|---|---|---|
| To do | What needs a person now: reviews waiting (soonest quote expiry first), exceptions, cases that stopped moving, grants about to expire, parts that are down | Temporal queries, staff queue, grants |
| Cases | Every case, most urgent first, in words | Temporal visibility |
| A case | The quote, what happened in order, the model calls, decision, review and outcome of this case, and the buttons | Temporal history, the ai-audit log joined by the correlation id |
| Authority | Which operation may run without a person, its tier, limit, expiry and who approved it | the grant store |
| Quality | The latest IntentGauge runs for this application: trials passed, flaky and failing cases, with the report one click away | IntentGauge `runs/` |
| Health | Temporal, worker, audit chain, anchor, proxy, latest evaluation, open staff items | live checks |

Quality always says **Not qualified**: the runs use development cases and no human-scored evidence.

## What a person can do here

- **Approve or reject** a refund that needs review. The page sends the digest of the quote it showed. If the quote changed in the meantime the request is refused with `quote_changed`. A review may be given before the customer confirms; the refund still waits for the customer.
- **Close an exception** or, for a cancelled booking whose refund did not settle, **complete the refund** as a supervisor.
- **Revoke a grant**, with a reason.

Consent is the customer's act and has no button here. Approving and activating a grant, and changing a tier, are not here yet; they need the reviewed activation that `AuthorityStore` will offer.

## Safety

- Loopback only. A request must carry the right `Host` and, when present, `Origin`.
- Every call needs `Authorization: Bearer <token>`. A token without the operator, reviewer or deployer role gets `forbidden`. A POST must be small JSON.
- The page builds no markup from data and sends a strict content policy. Evaluation reports open in a page with no network access, under a cookie that only reaches `/evals/`.
- Reads of the audit log never write. Writes go through the gateway, the same path as the command line.

## Limits

- The case list reads Temporal on every refresh. It is meant for hundreds of cases, not millions.
- Queries need a worker. Without one a running case shows "Not answering (is a worker running?)", and Health says no worker is polling.
- The audit log is read in full on each case page. Rotate it.
- The identity directory is the fake one from `seed`.
