"""The process pack's event-log adapter (ADR 0027).

`discover` maps an event log (CSV, XES, OCEL 2.0 JSON) into the kernel as the view of a
process as done, without reading the model. `conform` checks the process as the kernel
holds it (told, written and done) against the same log and writes one observed verdict per
step, flow and decision branch. Both build a plan of a digest source and observed claims,
played through the gateway with the shared adapter kit, so the adapter holds no database
credentials.
"""
