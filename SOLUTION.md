# CityWheels help desk

## Install and run

Python 3.11 or newer. The agent uses the standard library only.

Run the commands from `help-desk-agent/help-desk-agent`.

```sh
python3 -m unittest discover -s tests -v
python3 -m citywheels --provider fake "I want to cancel booking CW-1002. How much will I get back?"
python3 -m citywheels --provider fake "I want to cancel booking CW-9999. How much will I get back?"
python3 -m citywheels --provider fake "What is the capital of Australia?"
python3 eval/run_eval.py
```

The test command is the first one. It needs no API key and no network.

`--provider fake` is the deterministic stand-in. It ignores any API key. With no `--provider` and no key, the CLI uses that same stand-in. `python3 -m citywheels` with no arguments prints the agent triage. `capabilities`, `robot-docs guide`, `--robot-triage`, and `--json` are the machine-readable surfaces. A flag or provider value one edit from a single known spelling is corrected on stderr.

The env file is plain `KEY=value`. The names are `CITYWHEELS_PROVIDER`, `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_BASE_URL`, `GEMINI_API_KEY`, and `GEMINI_MODEL`. `.env.example` lists them with the keys empty. The CLI loads `.env` from the current directory and from parent directories, including the repository root. If `CITYWHEELS_PROVIDER` is omitted and `OPENAI_API_KEY` is set, the provider is openai. `--provider gemini` uses `GEMINI_API_KEY`.

## Model and provider

Tests, evaluation, and the offline CLI use `FakeModel` in `citywheels/fake_model.py`. It has to run here with no key and no network, and the refund figure has to be the one `calculate_refund` returned.

The live OpenAI client is `OpenAIModel` in `citywheels/openai_model.py`. It posts to `/chat/completions` on `OPENAI_BASE_URL`, default `https://api.openai.com/v1`, model `OPENAI_MODEL`, default `gpt-4o-mini`, temperature 0. Gemini is `GeminiModel`, model `GEMINI_MODEL`, default `gemini-3.8-flash`. I called the provider APIs directly because tool calls are ordinary request fields, so one loop serves the fake model and both hosted models. I left LangChain and LangGraph out. The loop is small, and a framework would hide the tool cycle this assignment is meant to show.

## How the agent works

`run_agent` sends the system prompt and the customer question to the model. If the reply contains tool calls, the loop runs each tool, appends the JSON result, and calls the model again. It stops when the model returns text and no tool call. After 6 rounds it tells the customer to contact the staff.

The prompt says the clock is 2026-06-10 09:00 Europe/Warsaw, UTC+02:00. It tells the model to call a tool before stating a price, a rule, a booking field, or a refund, to copy `refund_eur` and `channel_detail`, and to send unanswered or off-topic questions to help@citywheels.example or +48 000 000 000. Storm refunds stay with the staff. The prompt forbids general knowledge.

`search_policies` splits each file in `data/policies/` on markdown headings and returns the sections that share words with the question. Each passage is a slice of the file.

`get_booking` returns the stored object from `data/bookings.json`, or `found: false`. An unknown id does not raise.

`calculate_refund` applies the cancellation rules in code. It uses the stored `rental_price_eur` and does not look up the price table again. The default clock is `shop_now()`, which returns 2026-06-10 09:00 Europe/Warsaw. For a `confirmed` booking the rental refund is 100% at 48 hours or more before the start, 50% at 24 hours or more and under 48, and 0% under 24. Exactly 48 hours is the full band. Exactly 24 hours is the half band. The booking fee is never added. The deposit is refunded in full only when the booking can be cancelled. Status `in_progress`, `completed`, or `cancelled` refunds 0, deposit included. Card money is described as going back to the same card. Voucher money, rental portion and deposit, is described as a new voucher.

The fake model picks one tool from the wording, then writes the answer from that tool's JSON. For a cancellation it calls `calculate_refund` and prints `refund_eur`. It does not do the percentage itself. "What is the capital of Australia?" matches no shop word, so the model returns the staff message and calls nothing.

## Evaluation

`eval/questions.json` holds 16 questions. The five examples from the data README are in that file. Five questions ask for a refund, including CW-1002, CW-1001, CW-1004, and the in-progress booking CW-1008. Three questions must be refused. Australia is one. The population of Tokyo is another. A request for an ocean poem is the third.

`python3 eval/run_eval.py` calls `run_agent` with `FakeModel` on every question. It does not ship a list of canned replies. For each refund it calls `calculate_refund` and checks that the answer contains that amount, that a cancellable booking says the fee is not refunded, and that a card or voucher channel is named. The fee-included total must be absent. CW-1002's fee-included total would be 342. The run writes `eval/results.md`.

The offline run records a pass count and, separately, costly misses. A costly miss weighs 5. It is a wrong amount, the booking fee added back, money returned on a booking that cannot be cancelled, an invented booking, an automatic storm refund, a change inside 24 hours of the start, or an age rule that lets the wrong rider through. An ordinary miss weighs 1. The shop rules did not change. The extra questions are a holdout: they were not used to edit the system prompt. Refund amounts are still checked by calling `calculate_refund`, not by asking another model.

That score is the scripted stand-in. A later live check used the keys in the repository `.env`. `gpt-4o-mini` and `gemini-3.8-flash` both called `calculate_refund` for CW-1002 and said 340 EUR back to the same card, with the booking fee excluded. Both refused the capital of Australia and named the staff contacts. Both gave Saturday hours as 08:00 to 20:00 for the April to October season. Both said a 14-year-old cannot rent an e-bike because the minimum is 16. Both said CW-9999 was not found, with no traceback. Gemini also called `get_booking` on the refund questions. The first Gemini model name, `gemini-2.5-flash`, is retired, and Gemini 3 rejects a follow-up tool call unless the original function-call part is sent back with its thought signature. The client now does that, and the default Gemini model is `gemini-3.8-flash`.

One test did fail while the suite was being written. The refund numbers were already 340 for CW-1002. The failing test tried to patch `datetime.now` and raised `TypeError: cannot set 'now' attribute of immutable type 'datetime.datetime'` on Python 3.14. See the assistant note below. After the change, the suite checks that `calculate_refund` calls `shop_now`, and that `citywheels/*.py` contains no `.now(` call. The machine date during this work was 1 November 2026. A refund that read the machine clock would treat the June bookings as already started. CW-1002 would then be 300 EUR of deposit and none of the 80 EUR rental. The sample test would fail.

## AI coding assistants

I used Grok to write this agent. The wall-clock patch above is the case that was wrong. The assistant wrote `patch("citywheels.refunds.datetime.now", ...)`. The test process raised `TypeError` from `unittest.mock` before it could assert 340. That traceback is how the mistake showed up. `datetime.datetime` on this interpreter does not allow assigning `.now`. The refund function was not reading the machine clock. The fix is `shop_now()` in `citywheels/clock.py`, which returns the fixed instant, and a source scan for `.now(`.

A second change came from the sample totals, before that test existed. Reading "refund the customer" as "give back every euro they paid" produces 342 for CW-1002, because the booking fee is 2 EUR. The sample total is 340. That is half of the stored 80 EUR rental plus the 300 EUR deposit. `refund_eur` leaves the fee out. The same samples fix the boundaries. CW-1010 starts exactly 24 hours later and is 170 EUR, the 50% band. CW-1007 starts exactly 48 hours later and is 186 EUR, the 100% band. The comparisons are `>= 48 hours` and `>= 24 hours`.

## Limitations

`FakeModel` is a script, not a language model. It handles the eval questions and the same shapes, such as "cancel booking CW-..." and "what time do you open". A hours question with none of those words can miss the summer line and answer with the staff message, or quote a weaker section. Keyword overlap is the whole search. There are no embeddings.

`eval/results.md` is still the fake-model score. The live check covered five questions on two providers, not the full set of 16.

The agent does not store a cancellation, charge a card, or call a weather service. A storm warning is a staff decision.

Opening hours in the fake model always use June 2026, even when a test passes another `now` into `run_agent`. Refund math uses that argument. The CLI does not offer a second clock.

## What I would do next

Run the same 16 questions through `gpt-4o-mini` and keep the failures in `eval/results.md`. Where the live model invents a figure or answers an off-topic question, change the prompt and run the set again.

`calculate_refund` has an optional `at` for a what-if question about a later time. It cannot be earlier than now. Only the caller of `run_agent` can pass `now`; the model cannot move the clock itself.
