-- S0 step 3: row triggers on a vertex label and an edge label write an outbox row and NOTIFY.
DROP TABLE IF EXISTS public.outbox;
SET graph_path = s0;
CREATE TABLE public.outbox (
  seq         bigserial PRIMARY KEY,
  graph       text NOT NULL,
  label       text NOT NULL,
  op          text NOT NULL,
  element_id  text NOT NULL,
  old_props   jsonb,
  new_props   jsonb,
  causation   text,
  depth       int NOT NULL DEFAULT 0,
  txid        bigint NOT NULL DEFAULT txid_current(),
  created_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
  done_at     timestamptz,
  attempts    int NOT NULL DEFAULT 0,
  last_error  text,
  result      text
);

CREATE OR REPLACE FUNCTION public.outbox_capture() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  r record := CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
  s bigint;
BEGIN
  INSERT INTO public.outbox (graph, label, op, element_id, old_props, new_props, causation, depth)
  VALUES (TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP, r.id::text,
          CASE WHEN TG_OP <> 'INSERT' THEN OLD.properties END,
          CASE WHEN TG_OP <> 'DELETE' THEN NEW.properties END,
          nullif(current_setting('kernel.causation', true), ''),
          coalesce(nullif(current_setting('kernel.depth', true), '')::int, 0))
  RETURNING seq INTO s;
  PERFORM pg_notify('outbox', s::text);
  RETURN NULL;
END $$;

DROP TRIGGER IF EXISTS doc_outbox ON s0.doc;
CREATE TRIGGER doc_outbox AFTER INSERT OR UPDATE OR DELETE ON s0.doc
  FOR EACH ROW EXECUTE FUNCTION public.outbox_capture();
DROP TRIGGER IF EXISTS about_outbox ON s0.about;
CREATE TRIGGER about_outbox AFTER INSERT OR UPDATE OR DELETE ON s0.about
  FOR EACH ROW EXECUTE FUNCTION public.outbox_capture();
