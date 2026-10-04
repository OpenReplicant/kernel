# Incident report: checkout outage on 12 September 2026

## Summary

From 14:05 to 14:50 UTC on 12 September 2026, customers could not complete checkout.
The checkout service depends on the payments API, which depends on the main Postgres cluster.

## Timeline

At 13:58 UTC a configuration change to the payments API lowered its connection pool size.
The change caused the outage: the payments API ran out of database connections.
Priya Shah (priya@shop.test), the on-call engineer, responded and rolled the change back at 14:47 UTC.

## Follow-up

We propose adding a canary stage before configuration changes reach the payments API.
