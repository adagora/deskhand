---- MODULE Desk ----
\* One booking at the CityWheels desk.
\* The steps match schedule.change_allowed and refunds.refund_for_booking.
\* A quote and a change decision remember the band and status at that moment,
\* because the clock can move afterward.

EXTENDS Integers

VARIABLES status, band, quote, changeResult, bandAtQuote, statusAtQuote, bandAtChange, statusAtChange

vars == <<status, band, quote, changeResult, bandAtQuote, statusAtQuote, bandAtChange, statusAtChange>>

Init ==
  /\ status = "confirmed"
  /\ band = "far"
  /\ quote = "none"
  /\ changeResult = "none"
  /\ bandAtQuote = "none"
  /\ statusAtQuote = "none"
  /\ bandAtChange = "none"
  /\ statusAtChange = "none"

TimePasses ==
  /\ band # "near"
  /\ band' = IF band = "far" THEN "mid" ELSE "near"
  /\ UNCHANGED <<status, quote, changeResult, bandAtQuote, statusAtQuote, bandAtChange, statusAtChange>>

RentalStarts ==
  /\ status = "confirmed"
  /\ status' = "in_progress"
  /\ UNCHANGED <<band, quote, changeResult, bandAtQuote, statusAtQuote, bandAtChange, statusAtChange>>

RentalEnds ==
  /\ status = "in_progress"
  /\ status' = "completed"
  /\ UNCHANGED <<band, quote, changeResult, bandAtQuote, statusAtQuote, bandAtChange, statusAtChange>>

MarkCancelled ==
  /\ status = "confirmed"
  /\ status' = "cancelled"
  /\ UNCHANGED <<band, quote, changeResult, bandAtQuote, statusAtQuote, bandAtChange, statusAtChange>>

AskCancel ==
  /\ quote = "none"
  /\ quote' = IF status # "confirmed" THEN "nothing"
              ELSE IF band = "far" THEN "full"
              ELSE IF band = "mid" THEN "half"
              ELSE "deposit"
  /\ bandAtQuote' = band
  /\ statusAtQuote' = status
  /\ UNCHANGED <<status, band, changeResult, bandAtChange, statusAtChange>>

AskChange ==
  /\ changeResult = "none"
  /\ changeResult' = IF status = "confirmed" /\ band # "near" THEN "yes" ELSE "no"
  /\ bandAtChange' = band
  /\ statusAtChange' = status
  /\ UNCHANGED <<status, band, quote, bandAtQuote, statusAtQuote>>

Done ==
  /\ quote # "none"
  /\ changeResult # "none"
  /\ UNCHANGED vars

Next ==
  TimePasses \/ RentalStarts \/ RentalEnds \/ MarkCancelled \/ AskCancel \/ AskChange \/ Done

Spec == Init /\ [][Next]_vars

QuoteHonest ==
  \/ quote = "none"
  \/ /\ quote = "nothing"
     /\ statusAtQuote # "confirmed"
  \/ /\ quote = "full"
     /\ statusAtQuote = "confirmed"
     /\ bandAtQuote = "far"
  \/ /\ quote = "half"
     /\ statusAtQuote = "confirmed"
     /\ bandAtQuote = "mid"
  \/ /\ quote = "deposit"
     /\ statusAtQuote = "confirmed"
     /\ bandAtQuote = "near"

ChangeHonest ==
  \/ changeResult = "none"
  \/ /\ changeResult = "yes"
     /\ statusAtChange = "confirmed"
     /\ bandAtChange \in {"far", "mid"}
  \/ /\ changeResult = "no"
     /\ \/ statusAtChange # "confirmed"
        \/ bandAtChange = "near"

====
