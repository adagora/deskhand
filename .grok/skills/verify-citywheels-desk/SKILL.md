---
name: verify-citywheels-desk
description: "Check the CityWheels cancel quote and the 24-hour change window. Use when editing refunds.py, schedule.py, or cases.py, or when a change could add the booking fee, refund a booking that cannot be cancelled, or allow a late change."
---

# Verify the CityWheels desk

A wrong cancel quote or a late change costs the shop money. Unit tests name a few bookings. This check walks every booking status and every refund band.

## What is promised

A confirmed booking quotes the full rental plus the deposit at 48 hours or more, half the rental plus the deposit at 24 hours or more and under 48, and the deposit only under 24 hours. The booking fee is never in that quote. Any other status quotes nothing, deposit included. A change is allowed while the booking is confirmed and at least 24 hours remain, and refused otherwise.

## What it covers

- `citywheels/refunds.py`, function `refund_for_booking`
- `citywheels/schedule.py`, function `change_allowed`
- `citywheels/cases.py`, function `calculate_refund`, which only loads the booking and then calls `refund_for_booking`

Last confirmed against the working tree that contains those functions. The git commit at that confirmation was `d687a42`. Move that hash forward only after you run the check again and it still describes the code.

The model leaves out prices, vouchers, the agent loop, and storm wording. Those are not this check.

## Run

From `help-desk-agent/help-desk-agent`:

```sh
python3 verify/check_desk.py
```

Exit 0 means every status and band pair matches the promise. Any other exit is a broken quote or a broken change window.

The same promise is the TLA+ model in `verify/Desk.tla`, with the chart words in `verify/desk.view.ts`. Read that result with the pstack `/statechart` skill. It draws the states a reader can see and, when a rule breaks, the shortest path to that break. Do not copy its command lines from another machine. They change between releases.

## When it fails

The message names the status and the band. `far` is 48 hours out, `mid` is exactly 24 hours out, and `near` is one second under 24 hours. Open `refund_for_booking` or `change_allowed` at that case. If you meant the new behavior, change the promise in this skill and in `verify/Desk.tla` together, then run the check again.

A failure you have not seen on a broken copy proves nothing. Before you trust a green run, break `refund_for_booking` so a confirmed booking 48 hours out adds `booking_fee_eur` into `refund_eur`, run the command, and see it exit 1. Put the function back and see exit 0. Do not leave the break in the tree.
