# encoder-substrate

Reads Substrate's store (Postgres schema `store`, read-only) and submits what it holds as kernel claims, in
Wikidata's and Substrate's own terms (D-102, D-103). One change set per item, and one per property that carries constraints (a `property-constraint` statement becomes a claim on the property's term, wdt:P2302, with its pattern, allowed values and classes as qualifiers; D-108), so running it again over an
unchanged store submits nothing, and a changed item supersedes what the encoder said about it before.
The mapping is in `encoder_substrate/encode.py`; the decision is D-103 in `docs/decisions.md`.

```sh
python -m encoder_substrate --store "dbname=substrate" [--scope urn:kk:scope:default] [--dry-run]
```

`DATABASE_URL` is the module's own login (or one that may `SET ROLE kkm_encoder_substrate`); the store is
`--store` or `SUBSTRATE_STORE_URL`. The output is one JSON line: counts, findings, the change sets submitted.
The kernel needs the `validator` part running (`kk serve --only validator`) for the change sets to apply.

## Repeat the end-to-end run (Postgres 16 with pgvector; no Substrate stack needed)

```sh
# 1. A fixture store: Substrate's own migrations (not 002, pgGraph), then a few items written with its Store class.
createdb substrate_fixture
for f in 001_store 003_components 004_map; do psql -q -1 -v ON_ERROR_STOP=1 -d substrate_fixture -f $SUBSTRATE/store/sql/$f.sql; done
PYTHONPATH=$SUBSTRATE python modules/encoder-substrate/tests/make_fixture_store.py "dbname=substrate_fixture"

# 2. A throwaway kernel, as scripts/smoke_kernel.sh makes one.
createdb kk_enc
export KK_JWT_SECRET=$(python -c 'import secrets;print(secrets.token_hex(24))') AP=$(python -c 'import secrets;print(secrets.token_hex(12))')
KK_ADMIN_URL="dbname=kk_enc" KK_AUTHENTICATOR_PASSWORD=$AP python -m kk init
export DATABASE_URL="postgresql://kk_authenticator:$AP@localhost:5432/kk_enc"
python -m kk serve --only validator --port 8099 &

# 3. Export; look; export again (nothing new); change the store, export (the old claims are superseded).
export SUBSTRATE_STORE_URL="dbname=substrate_fixture"
python -m encoder_substrate
psql -d kk_enc -c "select subject, predicate, object_entity, object_value, status, evidence_type from api.claims where attributed_to = 'urn:kk:agent:encoder-substrate'"
python -m encoder_substrate                      # "new": 0, "unchanged": 11
# rename an item or change a rank in the fixture store (Store.item with its key; Store.rank), then:
python -m encoder_substrate                      # the changed items only; their earlier claims deprecated

# 4. Clean up.
kill %1; dropdb kk_enc; dropdb substrate_fixture
```

Checked on 2026-11-20: 20 items and 23 statements gave 11 change sets (the 9 others are the properties' and
packs' own items, which have nothing to say), 25 claims, all applied; the second run submitted 0 new; a renamed
item and a deprecated statement gave 2 new change sets and deprecated 10 earlier claims (superseded where the
new version states the claim again, retracted where not).

"Unchanged" means the same content as the item's latest change set from this encoder. An item that returns to content
it had before is submitted again, keyed `substrate:<Qid>:<hash>:after:<the latest change set>`, and supersedes what
came between.
