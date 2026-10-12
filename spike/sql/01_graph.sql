-- S0 steps 1-2: graph, pgvector, a vertex label with a promoted vector property and an HNSW
-- property index, a topic label and an edge label.
CREATE EXTENSION IF NOT EXISTS vector;
DROP GRAPH IF EXISTS s0 CASCADE;
CREATE GRAPH s0;
SET graph_path = s0;
CREATE VLABEL doc (embedding vector(3) GENERATED);
CREATE PROPERTY INDEX doc_emb ON doc USING hnsw (embedding vector_l2_ops);
CREATE VLABEL topic;
CREATE ELABEL about;
CREATE (:doc {name: 'east', embedding: [1,0,0]}), (:doc {name: 'north', embedding: [0,1,0]}),
       (:doc {name: 'up', embedding: [0,0,1]});
MATCH (a:doc {name: 'east'}), (b:doc {name: 'north'}), (c:doc {name: 'up'})
CREATE (h:topic {name: 'horizontal'}), (v:topic {name: 'vertical'}),
       (a)-[:about]->(h), (b)-[:about]->(h), (c)-[:about]->(v);
