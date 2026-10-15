# 0040. One booking path: one token format, one secret, all bookings through `book_slot`

- Status: Accepted
- Date: 2026-10-09
- Refines: 0030 (booking in the chat and on the booking page)

## Context
0030 lets a Candidate book in the chat or, through "none of these work", on a booking page. Wave 2 built both halves in parallel: lane W2-A wrote `offer_interview_slots`, `book_interview`, signed booking links and an `.ics` generator; lane W2-D wrote the public booking page with its own token format, its own key (derived from the session secret), its own booking service and its own `.ics`. A link the agent sent could not book on the page. The integration kept one of each.

## Decision
- **One booking-link token format and one key.** `make_booking_token` / `decode_booking_token` in `src/backend/criba/tools/booking.py` (W2-A's), keyed by `booking_secret()`: `CRIBA_BOOKING_SECRET_FILE` (Compose secret `criba_booking_secret`), else derived from the tools secret with a fixed label so the two never share a raw key.
- **Every booking goes through `book_slot`.** `book_interview` in the chat and the public booking page both reach it; it takes the slot lock in one transaction. W2-D's `create_booking_token`, `SqlBookingService` and `build_ics` were removed.
- **One `.ics` generator**, `ics_for_booking`.
- **Invite delivery depends on the channel.** On the web chat the invite is attached to the first agent message after the booking, as a signed download from `GET /api/public/booking/{token}/invite.ics`, and `ics_sent` is true only there. WhatsApp delivery waits for a document message in the real adapter. The agent never claims an invite was sent unless `ics_sent` is true.

## Alternatives considered
- **Keep W2-D's token and key:** the agent's links from `offer_interview_slots` are the main path, and W2-D's key was derived from the web session secret, which a public booking link should not share.
- **Two formats behind one decoder:** double the attack surface for no user benefit.

## Consequences
- Compose and the README list `criba_booking_secret` as a required secret.
- A link from the chat works on the booking page, and a booking from either place is the same event on the Application.
- Delivering the `.ics` on WhatsApp is open until the real adapter exists (ADR 0027).
