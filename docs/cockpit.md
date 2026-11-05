# The Deskhand cockpit

One page where a person manages the cancellation cases: what needs a decision, what happened in each case, which conversations never became a case, who may act without a person, what rules are in force, how the agent did in evaluation, and whether the parts that run the cases work. It is part of this application. Auditline (the audit gateway, repository `ai-audit`) and IntentGauge stay the detail views it links to ([ADR 0068 of the gateway](https://github.com/adagora/auditline/blob/main/docs/adr/0068-correlation-and-the-application-cockpit.md)).

```sh
python3 -m venv .venv && .venv/bin/pip install -r orchestration/requirements.txt
.venv/bin/python -m orchestration seed          # once; prints one token per role
.venv/bin/python -m orchestration worker        # cases only move while a worker runs
CASEWORK_TOKEN=<supervisor> .venv/bin/python -m cockpit \
  --audit-log ../ai-audit/live/audit.jsonl --audit-cli ../ai-audit/dist/src/cli.js \
  --console-url "http://127.0.0.1:8793/#token=<console token>" \
  --proxy-url http://127.0.0.1:8787 --evals-dir ../evalLOL/runs \
  --evaluation-url http://127.0.0.1:4317
```

It listens on `127.0.0.1:8799` only. With `CASEWORK_TOKEN` set it prints a URL that signs you in; without it the page asks for a token. The token stays in the browser tab, and a token in the address bar is accepted at any time. Every flag except `--home` and `--address` is optional, and a screen whose source is missing says so.

## Related tools

The sidebar has a group **Related tools** with two links: **Audit console** and **Evaluations**. Each link opens in a new tab. A link shows only when its address is set. `--console-url` sets the console link, and that link carries the console token. `--evaluation-url` sets the link to the IntentGauge dashboard. Both flags accept only http and https addresses. Only the operator, reviewer and deployer roles get the links. A customer token gets `staff_only`, as on every other call.

## One command

`python -m cockpit.stack` starts the operator side with one command: a Temporal development server, the worker, the cockpit, the Auditline console and, when you give an IntentGauge folder, the dashboard. The cockpit then has both sidebar links without more flags.

```sh
.venv/bin/python -m cockpit.stack --auditline ../ai-audit --intentgauge ../evalLOL
```

Build both folders first with `npm run build`. The first start seeds the case home and prints one token per role. Press Ctrl-C to stop everything. The console starts read-only. The ports are 8799 for the cockpit, 8793 for the console and 4317 for the dashboard.

Add `--proxy-config gateway.json` to start the Auditline proxy too. It needs `OPENAI_API_KEY` in the environment and writes to the same audit log. Add `--address HOST:PORT` to use a Temporal server that already runs, and `--runs` for another evaluation folder. The command is for one machine. It is not a production deployment.

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
- The sidebar links are built only from web addresses that you set on the command line, and the page opens them in a new tab with `noopener noreferrer`.
- The console link carries the console token. Start the audit console without `--reviewer`, so it stays read-only. The workflow takes a review only from its own signal, so a verdict written in that console would not release a case.

## Limits

- The case list reads Temporal on every refresh. It is meant for hundreds of cases, not millions.
- Queries need a worker. Without one a running case shows "Not answering (is a worker running?)", and Health says no worker is polling.
- The audit log is read in full on each case page. Rotate it.
- The identity directory is the fake one from `seed`.
- The rules shown are the defaults of this build. A worker started with other settings may differ.
