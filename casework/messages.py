EXPLANATIONS = {
    "set_CASEWORK_TOKEN": ("No access token was given.", "Set CASEWORK_TOKEN to the token that `python -m orchestration seed` printed for your role."),
    "home_not_seeded": ("There is no case home in this folder.", "Run `python -m orchestration seed` once, or pass --home with the folder that holds identities.json."),
    "temporal_unreachable": ("The Temporal server did not answer.", "Start it with `temporal server start-dev`, then start a worker with `python -m orchestration worker`. Use --address when it runs elsewhere."),
    "unauthenticated": ("The token is not known.", "Use the token printed for your role by `seed`. Tokens are shown once."),
    "forbidden": ("This role may not do that.", "Use a token with the needed role: customer, reviewer, operator or deployer."),
    "case_not_accessible": ("This case belongs to another customer.", "Check the case id."),
    "booking_not_accessible": ("The booking is not found for this customer.", "Check the booking id. A booking of another customer looks the same."),
    "invalid_scope": ("The tenant, customer or booking id is not a valid name.", "Use the id exactly as it appears on the booking."),
    "invalid_proposal": ("The quote cannot be built from this request.", "Check the case id and the intent name."),
    "not_cancellable": ("The booking cannot be cancelled in its current status.", "Nothing is refunded for a cancelled, completed or running booking."),
    "staff_required_for_claim": ("A claim such as a storm warning is a decision for the staff.", "Open the staff item and decide by hand. Nothing was written."),
    "staff_required_for_payment_route": ("The booking was not paid by card or voucher.", "Decide by hand how it is refunded. Nothing was written."),
    "consent_required": ("The customer has not confirmed this exact quote.", "Ask the customer to confirm the quote with its digest."),
    "operation_approval_required": ("A reviewer has not approved this quote.", "A reviewer must approve it. The refund is above the automatic limit."),
    "operation_rejected": ("A reviewer rejected the quote.", "Nothing was written. A new request gets a new quote."),
    "proposal_expired": ("The quote expired before it was confirmed.", "Nothing was written. Ask the customer to request the cancellation again."),
    "quote_expired": ("The quote expired before it was confirmed.", "Nothing was written. Ask the customer to request the cancellation again."),
    "expired": ("The grant has expired.", "A deployer must approve and activate a new grant."),
    "clock_regressed": ("The system clock went backwards.", "Check the clock of the machine that runs the worker."),
    "booking_changed": ("The booking changed after the quote was made.", "Nothing was written. A new request gets a new quote."),
    "amount_changed": ("The refund amount changed after the quote was made.", "Nothing was written. A new request gets a new quote."),
    "refund_method_changed": ("The payment method changed after the quote was made.", "Nothing was written. A new request gets a new quote."),
    "policy_changed": ("The refund rules changed after the quote was made.", "Nothing was written. A new request gets a new quote."),
    "release_changed": ("The application release changed after the grant was approved.", "A deployer must approve and activate a grant for the new release."),
    "original_facts_unverified": ("The facts at the time of the quote cannot be checked any more.", "Close the item by hand."),
    "rule_disagreement": ("The money rule and the audited QDL rule gave different amounts.", "Nothing was written. Compare policies/refund.json with citywheels/refunds.py."),
    "audit_unavailable": ("The audit log or the ai-audit command is not available.", "Start the cockpit with --audit-log and --audit-cli."),
    "receipt_unavailable": ("No receipt can be made for this decision.", "A receipt exists only for a completed decision whose log is consistent. Replay it first."),
    "decision_not_in_case": ("That decision does not belong to this case.", "Open the decision from the case page."),
    "acknowledge_needed": ("A transact grant needs your confirmation.", "Tick the box that says you know nothing has been qualified, then try again."),
    "grant_limit_invalid": ("The limit is not a usable amount.", "Use a whole number of cents above zero and not above the cap."),
    "quote_changed": ("The quote changed while you were looking at it.", "Reload the case and check the new quote before you decide."),
    "invalid_grant": ("The grant is not valid for this action.", "Check the grant in the Authority screen."),
    "not_active": ("There is no active grant for this operation.", "A deployer must activate a grant."),
    "revoked": ("The grant was revoked.", "A deployer must approve and activate a new grant."),
    "limits": ("The refund is above the grant limit.", "Send it to a person, or approve a grant with a higher limit."),
    "unknown_grant": ("That grant does not exist.", "Check the grant id."),
    "tier_cannot_transact": ("The operation is not at the transact tier.", "Raise the tier in the Authority screen only after the evidence supports it."),
    "idempotency_conflict": ("A refund with the same operation id exists with different details.", "Stop. Do not retry. Compare the ledger entry with the quote."),
    "readback_mismatch": ("The ledger differs from the quote.", "The case cannot be completed. Check the ledger and close the item."),
    "cancellation_not_observed": ("The cancellation was not seen before the refund.", "Nothing was refunded. Check the booking status."),
    "no_committed_cancellation": ("There is no committed cancellation to complete.", "Close the item if nothing was cancelled."),
    "resolution_forbidden": ("Only a supervisor may complete this refund.", "Use a token with the reviewer and operator roles."),
    "backend_evidence_unavailable": ("The booking system did not answer, so no success is claimed.", "Check the booking system. The case waits for a person."),
    "cancelled_refund_unsettled": ("The booking is cancelled but the refund did not settle.", "A supervisor can complete the refund after a recheck. There is no rollback."),
    "evidence_unavailable": ("The audit evidence could not be kept.", "Check AI_AUDIT_CLI and AI_AUDIT_LOG. Nothing was written."),
    "completed": ("The refund is settled and was read back.", "Nothing to do."),
    "rejected": ("A reviewer rejected the quote.", "Nothing was written."),
    "refused": ("The desk refused the request.", "See the reason on the case."),
    "closed_by_staff": ("A person closed the case.", "Nothing to do."),
    "already_settled": ("The refund was already settled.", "Nothing was written again."),
}

PHASES = {
    "starting": "Preparing the quote",
    "waiting-for-consent": "Waiting for the customer to confirm the quote",
    "waiting-for-review": "Waiting for a reviewer",
    "rechecking": "Checking the quote against current facts",
    "cancelling": "Cancelling the booking",
    "refunding": "Issuing the refund",
    "reading-back": "Reading the result back",
    "owned-exception": "Waiting for a person to resolve an exception",
    "completed": "Done",
    "refused": "Refused",
    "rejected": "Rejected by a reviewer",
    "quote_expired": "Quote expired",
    "closed_by_staff": "Closed by staff",
    "already_settled": "Already settled",
}

NEEDS_A_PERSON = ("waiting-for-review", "owned-exception")


def explain(code: str) -> dict:
    message, next_step = EXPLANATIONS.get(code, ("Something went wrong.", "Report the code to the maintainer."))
    return {"code": code, "message": message, "next": next_step}


def phase_label(phase: str) -> str:
    return PHASES.get(phase, phase)
