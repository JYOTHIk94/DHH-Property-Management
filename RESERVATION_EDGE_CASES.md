# Reservations — Scenarios and Edge Cases

**Status:** For discussion. Nothing in here is built yet.
**Purpose:** Agree how the system should behave in every situation a booking can get into, *before* we write the code.

---

## How to use this document

An **edge case** is an uncommon situation the system must still handle correctly — a guest who pays half and disappears, a booking cancelled after the money is already in the bank, two guests with the same name and no contact details. These are rare individually, but between them they account for most of the confusion and rework in a booking system, because nobody agreed up front what should happen.

Below, every situation we could think of is listed as a row. Each row has:

| Column | What goes in it |
|---|---|
| **#** | A stable reference (A1, C6, E11…) so we can point at a row in a meeting. A **⚠** beside it means the row is open. |
| **Scenario** | What happens in the real world. |
| **Proposed behaviour** | What we suggest the system does. On a **⚠** row we genuinely cannot decide it for you; it is a business or accounting policy question, and the options are laid out for you to choose. |
| **Client decision** | Deliberately blank, in every row. This is the column you fill in. |

**No row should be left blank before development starts.** A blank row becomes an assumption, and an assumption becomes a bug.

The **22 rows marked ⚠** are the ones we need answers to; they are collected again in the open questions summary at the end. Rows without the marker carry our recommendation; please confirm or correct them, but they are not blockers.

---

## Terms used in this document

| Term | What it means here |
|---|---|
| **Booking** | A guest's reservation of a property for a set of dates. |
| **Booking Amount** | The total agreed price of the stay. |
| **Draft Invoice** | An invoice the system has prepared but which has **no accounting effect yet**. It can still be edited or deleted. |
| **Approved Invoice** | An invoice the finance team has reviewed and posted. It now affects the accounts and cannot simply be deleted — it must be reversed with a credit note. |
| **Credit Note** | The formal way to reverse or reduce an approved invoice. |
| **Advance** | Money received from a guest that is not yet matched to any approved invoice. It sits as a liability — we owe the guest either a stay or a refund. |
| **OTA** | Online Travel Agent — Airbnb, Booking.com, and similar, reaching us through Guesty. |

---

## The agreed billing rules

These came from the business and drive everything below.

| Rule | What it says | Worked example |
|---|---|---|
| **Rule 1** | Invoicing is triggered by **money received**, not by the guest checking out. | Guest checks out having paid nothing. No invoice is raised. |
| **Rule 2** | A partial payment produces an invoice for **the amount paid, and nothing more**. | Booking is AED 1,000. Guest pays AED 500 → one invoice for AED 500. Guest later pays the remaining AED 500 → a second invoice for AED 500. |
| **Rule 3** | The system **never approves an invoice by itself**. It prepares drafts; the finance team reviews and approves them. | A payment lands overnight. In the morning finance finds a draft waiting, not a posted invoice. |
| **Rule 4** | When Guesty sends a change to a booking that has already been invoiced, the system **never quietly alters the accounts**. | Guesty cancels a paid booking. The booking is flagged; no credit note is issued automatically. |

---

## Three questions we need answered before anything else

These three change the shape of everything downstream. Nothing else in this document can be finalised until they are settled.

| Ref | Question | Why it blocks everything | Options | Our recommendation |
|---|---|---|---|---|
| **Q1** | For bookings that come through an OTA, who takes the guest's money — the OTA, or us? | **Thirty-five of the forty-one bookings currently in the system came from Guesty.** If the OTA collects and later pays us out, there is no "payment received" event for those bookings — and under Rule 1 they would **never be invoiced at all**. | (a) We always collect directly; (b) the OTA always collects and pays us out; (c) both, depending on the channel. | None — this is a fact about your business, not a choice. If the answer is (c), the system must know each booking's channel and behave differently per channel. |
| **Q2** | Between the guest paying and finance approving the invoice, where does that money sit? | Rules 2 and 3 together create a gap. The AED 500 is in the bank, but the invoice is still a draft — perhaps for days. The accounts have to say something about it. | (a) Hold it as an **advance** — money owed to the guest until a stay is delivered and an invoice approved, then applied against that invoice; (b) do not record it until finance approves. | **(a).** It is the standard accounting treatment. (b) leaves the bank balance and the accounts disagreeing for as long as the draft sits unreviewed. Finance must agree, because (a) changes what a guest's day-to-day balance looks like. |
| **Q3** | When a booking is cancelled after money has changed hands, what does the guest get back? | We cannot invent a cancellation policy. Until one exists, every cancellation row below is provisional. | See the sub-questions below. | None — business policy. |

**Q3 in detail.** Each stage needs its own answer:

| Stage | Question |
|---|---|
| Cancelled before arrival | Full refund, partial, or nothing? |
| Cancelled after arrival (guest leaves early) | Is the used portion charged? |
| Guest never turns up (no-show) | Is any of it kept? |
| OTA bookings | Does the answer differ, where the OTA's own policy may already govern? |

Once the policy is known, the system can apply it.

---

## A. Booking lifecycle

| # | Scenario | Proposed behaviour | Client decision |
|---|---|---|---|
| A1 | A new booking arrives from Guesty | Record it. No invoice, no accounting effect. | |
| A2 | A booking is created manually by staff | Same as A1 — record it only. | |
| A3 | Booking is confirmed | Record the status. Still no invoice; invoicing waits for money (Rule 1). | |
| A4 | Guest checks in | Record the status. The property counts as occupied. | |
| A5 | Guest checks out | Record the status. **Checkout alone does not create an invoice** — only payment does. | |
| **A6** ⚠ | Booking cancelled before the guest arrives | Release the property. Money handling per **Q3** and section C. | |
| **A7** ⚠ | Guest leaves early (cancelled after arrival) | Release the property. Whether the unused nights are refunded is **Q3**. | |
| **A8** ⚠ | Guest never arrives (no-show) | Options: (a) treat as a cancellation and follow the refund policy; (b) treat the stay as delivered and keep the money; (c) charge a fixed no-show fee. Needs a policy. | |
| A9 | A historical booking is imported with dates already in the past | Accept it. Do not block it for having a past arrival date, and do not create invoices for it retroactively. | |
| A10 | A booking arrives from Guesty already marked cancelled | Record it as cancelled. Do not create anything financial. | |
| **A11** ⚠ | Same-day arrival and departure (zero nights) | Is this a valid booking (a day-use let) or a data error to reject? | |
| A12 | Departure date is before the arrival date | Reject it. This is always a data error. Report it rather than storing it. | |
| A13 | Stay is extended while the guest is in the house | Update the dates and the booking amount. Any invoices already approved stand; the additional nights are billed when the guest pays for them. | |
| **A14** ⚠ | Stay is shortened while the guest is in the house | Update the dates and the booking amount. If this drops the amount below what has already been invoiced, see **B4**. | |

---

## B. Changes to an existing booking

| # | Scenario | Proposed behaviour | Client decision |
|---|---|---|---|
| B1 | Dates change, no money received yet | Apply the change and recalculate the booking amount. Nothing to reverse. | |
| B2 | Dates change after a part-payment | Apply the change to the booking. Do not touch any approved invoice. The new balance is simply what remains to be paid. | |
| B3 | The price goes **up** after a part-payment | Apply the new price. The guest now owes more. The extra is invoiced when it is paid. | |
| **B4** ⚠ | The price drops **below** what has already been invoiced | Booking was AED 1,000, guest paid AED 500 and we approved an AED 500 invoice. The booking is now reduced to AED 400. The guest is AED 100 in credit. Options: (a) raise a credit note for AED 100 and refund it; (b) raise a credit note and hold the AED 100 as credit against a future stay; (c) do not adjust, treat the AED 100 as a cancellation fee. | |
| B5 | Number of guests changes | Update the booking and the property's occupancy. No accounting effect unless the price changes with it. | |
| **B6** ⚠ | The booking is transferred to a different guest | Does the money follow the booking to the new guest, or is the original guest refunded and the new guest charged fresh? This matters when an invoice is already approved in the original guest's name. | |
| B7 | The booking is moved to a different property | Apply the change. Release occupancy on the old property, take it on the new one. If the price changes, treat as B3 or B4. | |
| B8 | A change arrives while a draft invoice is sitting with finance | Update the booking, and re-issue the draft to match. Nothing has been committed to the accounts, so nothing needs reversing. | |
| B9 | A change arrives after finance has approved an invoice | Update the booking, but **never** alter the approved invoice automatically. Flag the booking for a person to review. See section E. | |

---

## C. Money coming in

| # | Scenario | Proposed behaviour | Client decision |
|---|---|---|---|
| C1 | Nothing has been paid | No invoice exists. The booking simply shows a balance due. | |
| C2 | Guest pays part of the amount (AED 500 of AED 1,000) | Record the AED 500. Prepare a **draft invoice for AED 500** — not for AED 1,000. | |
| C3 | Guest makes several part-payments | Each payment produces its own draft invoice for that amount. A booking can carry many invoices. | |
| C4 | Guest pays the full amount at once | One draft invoice for the full booking amount. | |
| C5 | A final payment clears the outstanding balance | A draft invoice for that final amount. The booking is now fully invoiced. | |
| **C6** ⚠ | Guest **overpays** (AED 1,200 on an AED 1,000 booking) | Options: (a) invoice AED 1,000 and hold AED 200 as credit; (b) invoice AED 1,000 and refund AED 200 immediately; (c) reject the overpayment at the point of entry. | |
| **C7** ⚠ | The OTA collected the money, not us | **This is Q1.** If we never see a payment, Rule 1 never triggers and the booking is never invoiced. Needs a separate rule for OTA bookings: likely invoice on checkout and reconcile against the OTA's payout. | |
| **C8** ⚠ | Guest pays, then cancels — invoice still in draft | Delete the draft invoice (nothing has hit the accounts). Refund per **Q3**. | |
| **C9** ⚠ | Guest pays, then cancels — invoice already approved | Do **not** delete the invoice. Raise a credit note to reverse it. Whether cash is returned is **Q3**. | |
| C10 | Partial refund | Raise a credit note for the refunded portion, then return the cash. Never adjust an approved invoice in place. | |
| **C11** ⚠ | A payment is reversed or charged back by the bank | The money has left our account after an invoice was approved. This needs a defined handling: credit note, or a bad-debt write-off, or reinstating the balance as owed. | |
| **C12** ⚠ | Guest pays in a different currency from the booking | Which exchange rate applies — the booking date, or the payment date? This affects the invoiced amount. | |
| **C13** ⚠ | A security deposit is taken | A deposit is not revenue — it is money we hold and expect to return. It must not appear on a normal invoice. Options: (a) hold it as a liability, separate from booking revenue; (b) do not track it in this system at all. **Currently the system records a deposit field but does nothing with it.** | |
| **C14** ⚠ | A security deposit is returned, or kept because of damage | Returning it is a refund. Keeping it converts a liability into income, and probably has tax consequences. Depends on C13. | |

---

## D. Invoicing

| # | Scenario | Proposed behaviour | Client decision |
|---|---|---|---|
| D1 | A payment is received | The system prepares a **draft** invoice for exactly that amount. It has no accounting effect yet. | |
| D2 | Finance reviews and approves the draft | The invoice posts to the accounts. The guest's advance is applied against it. | |
| D3 | Finance edits the amount before approving | Allowed. The draft is finance's to correct. | |
| D4 | Finance rejects and deletes the draft | Allowed. The money received remains as an unapplied advance until someone decides what to do with it. | |
| D5 | Two payments arrive before finance approves anything | Two separate drafts sit waiting. They do not merge. | |
| **D6** ⚠ | Money has been received but no invoice is approved yet | **This is Q2.** The money is held as an advance against the guest. It is in the bank and on the books, but not yet recognised as income. | |
| D7 | A booking is cancelled after an invoice is approved | Credit note. Never deletion. See C9. | |
| D8 | The booking price changes after an invoice is approved | The approved invoice is untouched. The difference is settled by a further invoice (price up) or a credit note (price down, see B4). | |
| D9 | The part-invoices don't add up to the booking total | This is **normal and expected** while a stay is in progress — a AED 1,000 booking with AED 500 paid is correctly invoiced at AED 500. It is only a problem if the booking is complete and paid and the totals still disagree. | |
| **D10** ⚠ | What should a part-payment invoice actually say? | Options: (a) a line reading "Booking Amount" for AED 500, which loses the context; (b) "Part payment 1 of 2 — Stay at [Property], 12–15 Mar" which is clearer to the guest. We recommend (b). | |
| **D11** ⚠ | Tax treatment of a part-payment invoice | **Needs your accountant.** Under UAE VAT the tax point is generally the earlier of invoice or payment, which suggests invoicing on payment is defensible. But we should not assume. Does VAT apply to the full booking at first payment, or proportionally to each part-invoice? | |
| **D12** ⚠ | A complimentary or zero-amount stay | No payment means, under Rule 1, no invoice is ever raised. Is that acceptable, or should a zero-value invoice be produced for the record? | |

---

## E. Updates arriving from Guesty

Guesty is where OTA bookings live, and it sends us changes as they happen. The governing principle we propose: **Guesty may freely change a booking that has no money against it. Once money is involved, Guesty may not change the accounts — only a person may.**

| # | Scenario | Proposed behaviour | Client decision |
|---|---|---|---|
| E1 | Guesty updates a booking with no money against it | Apply the change in full. Guesty is the authority on booking facts. | |
| E2 | Guesty updates a booking with a **draft** invoice pending | Apply the change, and re-issue the draft to match. Nothing is committed yet. | |
| E3 | Guesty updates a booking with an **approved** invoice | Record the booking change, but **do not touch the accounts**. Flag the booking for a person to review and resolve. | |
| E4 | Guesty cancels a booking with no money against it | Mark it cancelled. Release the property. | |
| **E5** ⚠ | Guesty cancels a booking with a **draft** invoice | Mark it cancelled, discard the draft. Refund per **Q3**. | |
| E6 | Guesty cancels a booking with an **approved** invoice and a payment | **Never reverse this automatically.** An unattended background job must not issue credit notes or refund guests. Flag it; a person reviews and cancels it properly. | |
| E7 | A booking arrives for a property we don't have on file | Do not create a half-complete booking. Report it as a failed sync so someone can map the property. **Today this failure is invisible** — it is counted the same as a normal skip. | |
| E8 | A booking is deleted in Guesty | Mark it cancelled here. **Never hard-delete** — we may have financial records attached to it. | |
| E9 | The same update is delivered twice | No effect. Bookings are matched on their Guesty reference, so a repeat is harmless. | |
| E10 | An update is missed during an outage | A nightly catch-up re-reads everything from Guesty and repairs the gap. *(This catch-up currently exists but is switched off — it should be re-enabled.)* | |
| **E11** ⚠ | The same booking is edited in both Guesty and our system at once | Who wins? We propose Guesty wins on booking facts (dates, guests, price) and our system wins on anything financial. But if a staff member deliberately overrode a date, a Guesty sync would silently undo it. | |
| E12 | Guesty jumps a booking straight from confirmed to checked-out | Accept it. Guesty may skip steps we model separately; refusing the update would strand the booking. | |
| E13 | Guesty re-opens a booking we already treat as finished or cancelled | Do not silently re-open it. Flag it for review. Reversing a finished booking may have accounting consequences. | |

---

## F. Guest identity

The system creates a customer record for each guest. Getting this wrong either splits one guest across several records, or merges two different people into one.

| # | Scenario | Proposed behaviour | Client decision |
|---|---|---|---|
| F1 | Two bookings share an email address | The same guest. Use the existing customer record. | |
| F2 | A guest arrives with **no email and no phone** | Create a new customer record. Never merge two contactless guests just because they share a first name — they are probably different people. | |
| **F3** ⚠ | Two contactless guests share a name (e.g. two guests called "Sarah") | Today the system creates a separate customer for each, which is safe but produces duplicates. *There are currently two "sarah" records and two "Vijaya G" records among the 31 customers in the system.* Options: (a) accept duplicates and clean up periodically; (b) require staff to pick an existing guest or explicitly confirm a new one; (c) require at least one contact detail before a booking can be taken. | |
| F4 | A returning guest already exists as a customer | Reuse the existing record. Their booking history stays in one place. | |

---

## What the new rules change

Three consequences worth stating plainly, because they will look wrong to anyone expecting the old behaviour.

| Consequence | Why it follows | What it affects |
|---|---|---|
| **A booking can have many invoices.** | Under Rule 2, a guest paying in three instalments generates three invoices. Under the old design each booking had at most one. | Any report, screen, or statement that assumes one invoice per booking will need to change. |
| **The booking amount and the invoiced total will often disagree, correctly.** | A AED 1,000 booking with AED 500 received is invoiced at AED 500. That gap is not an error — it is the outstanding balance. | It only warrants attention once the stay is finished and settled. See **D9**. |
| **Cancelling a paid booking is an accounting event, not a deletion.** | Once finance has approved an invoice, the booking cannot simply be removed. It is reversed with a credit note, and any refund is a separate, deliberate act. | This is why Rule 4 exists, and why no automated Guesty update is permitted to touch an approved invoice. See **C9**, **E6**. |

---

## Open questions summary

For the meeting. Everything below blocks development. Several of these close further ⚠ rows as a side effect, shown in the last column.

| Ref | Question | Who decides | Also closes |
|---|---|---|---|
| **Q1** | For OTA bookings, who collects the guest's money? | Business | C7 |
| **Q2** | Where does money sit between payment and invoice approval? | Finance | D6 |
| **Q3** | Cancellation and refund policy, per lifecycle stage | Business | A6, A7, C8, C9, E5 |
| A8 | How is a no-show treated? | Business | |
| A11 | Is a zero-night booking valid? | Business | |
| B4 | Price drops below the invoiced amount — credit, refund, or fee? | Finance | A14 |
| B6 | Booking transferred to a different guest — does the money follow? | Finance | |
| C6 | Overpayment — hold as credit, or refund? | Finance | |
| C11 | Chargebacks and reversed payments | Finance | |
| C12 | Which exchange rate for foreign-currency payments? | Finance | |
| C13, C14 | Security deposits — liability, income, or out of scope? | Finance / Accountant | |
| D10 | What a part-payment invoice line should read | Business | |
| D11 | VAT treatment of part-payment invoices | Accountant | |
| D12 | Complimentary stays — invoice at zero, or not at all? | Business | |
| E11 | When Guesty and our system disagree, who wins? | Business | |
| F3 | Guests with no contact details — duplicates, or block the booking? | Business | |

That is **16 decisions**, and they close all 22 ⚠ rows.

---

## Separately: defects found in the current system

These are **not** decisions for the client. They are things already broken, listed here so they are not lost. They should be fixed regardless of how the scenarios above are answered.

| # | Defect | Evidence / effect today |
|---|---|---|
| 1 | The invoicing and payment code that exists today **can never run** — it waits on a status that nothing ever sets. | 41 bookings in the live system, and not one invoice or payment has ever been produced by it. |
| 2 | Changes arriving from Guesty for a booking finalised in our system are **silently discarded**. | No record is kept that anything was lost. Relates to **E3**. |
| 3 | When a booking is cancelled, nothing financial is reversed. | Relates to **C9**, **D7**. |
| 4 | The occupancy counter for each property drifts out of true. | Goes wrong when a booking's guest count changes. Relates to **B5**. |
| 5 | Duplicate customer records are being created for the same guest. | Two "sarah" records and two "Vijaya G" records among the 31 customers. Relates to **F3**. |
| 6 | The three billing items the invoicing code needs do not exist in the live system. | `Short Term Rental`, `Long Term Rental`, `Service Charge`. |
| 7 | The nightly catch-up that repairs missed Guesty updates is currently switched off. | Relates to **E10**. |
