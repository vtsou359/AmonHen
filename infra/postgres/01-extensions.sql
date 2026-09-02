-- Spatial types and indexing for the history store.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_topology;

-- Trigram search, so incident lookup tolerates the transliteration problem:
-- "Varnavas" / "Varnava" / "Βαρνάβας" all need to find the same fire.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
