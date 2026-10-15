# 0015. WhatsApp for applicants, a web app for internal users

- Status: Accepted; how WhatsApp connects (Hermes gateway) is superseded by 0018 (our service owns the WhatsApp webhook); Phase 2 runs on a web chat test channel first (0027)
- Date: 2026-10-09
- Case: Grupo Sazón

## Context
The case has two audiences. Delivery-driver applicants are mobile-first and reach us from a chat link. Internal users (Grupo Sazón's recruiters and HR, Criba ops) need to query applications, see dashboards and watch conversations.

## Decision
- **Applicants: WhatsApp is the main channel.** The website link is a WhatsApp click-to-chat link carrying a source tag. It runs through Hermes's WhatsApp gateway (the Cloud API integration, `hermes whatsapp-cloud`; which integration to use is confirmed in the first spike).
- **Internal users: a web app** that talks to `criba-hq` through the Hermes API server. It offers chat with `criba-hq` ("how many qualified drivers in Monterrey this week?"), dashboards over our applicant database and case records, and a view of live and past candidate chats.
- **No writes to Grupo Sazón's systems** (ADRs 0012, 0014). All data stays in our store, and Grupo Sazón sees it through the web app.

## Alternatives considered
- **Web chat for applicants:** loses the WhatsApp habit and the push notifications that make asynchronous screening work.
- **Internal users on Slack or Telegram:** no dashboards, and it mixes operational data with chat.

## Consequences
- WhatsApp rules apply. Free-form messages are allowed within 24 hours of the candidate's last message; outside that window, only templates Meta has approved in advance. This constrains the stalled-candidate policy and outreach.
- The web app is a new component. How its dashboards query the data is to be specified (read-only views over the applicant database and case events).
