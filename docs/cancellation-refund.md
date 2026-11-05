# Cancellation and refund

`citywheels/` answers one question and prints a quote. `orchestration/` runs one cancellation on Temporal against a fake booking store and a fake ledger, both on disk. [The architecture](architecture.md) explains why.

## Run it once

```sh
python3 -m venv .venv
.venv/bin/pip install -r orchestration/requirements.txt
.venv/bin/python -m orchestration demo
```

`demo` starts a throwaway Temporal server and one worker, asks the fake model to cancel CW-1002, confirms the quote as the customer, approves it as a reviewer and prints the result. It exits 0 when the case is `completed`, the ledger holds one refund and the result was claimed after read-back. The first run downloads the Temporal dev server.

## Run it by hand

Start a server with the [Temporal CLI](https://docs.temporal.io/cli), then seed the home directory and start a worker in a second shell.

```sh
temporal server start-dev
```

```sh
.venv/bin/python -m orchestration seed
.venv/bin/python -m orchestration worker
```

`seed` prints three tokens once and stores only their hashes. Each later command reads its token from `CASEWORK_TOKEN`.

```sh
CASEWORK_TOKEN=<customer> .venv/bin/python -m orchestration ask --provider fake "Please cancel booking CW-1002."
CASEWORK_TOKEN=<customer> .venv/bin/python -m orchestration consent --case <case_id> --digest <digest>
CASEWORK_TOKEN=<reviewer> .venv/bin/python -m orchestration review --case <case_id> --digest <digest> --verdict approved
CASEWORK_TOKEN=<customer> .venv/bin/python -m orchestration status --case <case_id>
```

`ask` prints the answer and, when the customer clearly asked to cancel, the case id and the exact quote. Without `--provider fake`, `ask` uses the provider in `.env`, and those calls are billable. Stop the worker with `kill -9` during the wait and start it again. The case continues.

Staff commands use the supervisor token:

```sh
CASEWORK_TOKEN=<supervisor> .venv/bin/python -m orchestration queue
CASEWORK_TOKEN=<supervisor> .venv/bin/python -m orchestration resolve --case <case_id> --kind complete-refund
CASEWORK_TOKEN=<supervisor> .venv/bin/python -m orchestration resolve --case <case_id> --kind close
```

## The rule

The amount is `refund_for_booking` in `citywheels/refunds.py`, the rule `verify/check_desk.py` and `verify/Desk.tla` check. `casework/rule.py` converts it to integer minor units. The shop clock is `shop_now`, and a request cannot set it. Proposal expiry and grant expiry use the system clock.

For CW-1002 at the shop clock the quote is 34000 minor units: half of the 8000 rental plus the 30000 deposit. The 200 booking fee stays out.

A refund above 100.00 EUR needs staff review as well as customer consent. `Desk(auto_approve_minor=...)` sets the limit.

`policies/refund.json` is the same rule as a QDL program, for ai-audit `decide`. It is not on the money path. When evidence is on, a disagreement between the two stops the case before any write.

## Tests

```sh
python3 -m unittest discover -s tests
.venv/bin/python -m unittest orchestration.tests.test_workflow
.venv/bin/python -m unittest orchestration.tests.test_audit_link
```

The first suite stays on the standard library, needs no network and covers `casework/`, including three real processes racing on one operation. The second runs against a real local Temporal server, including a killed worker process and a worker that dies after the cancel write. The third needs a built ai-audit checkout, at `../../../ai-audit` or `AI_AUDIT_ROOT`, and skips without it. It runs the real proxy and the real `decide`, `approve`, `effect`, `replay` and `verify` commands.
