-- Profile extras: columns the route code already references but the original 013 migration omitted.
ALTER TABLE retrieval_profiles
  ADD COLUMN IF NOT EXISTS k             INT,
  ADD COLUMN IF NOT EXISTS threshold     REAL,
  ADD COLUMN IF NOT EXISTS rerank        BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS include_graph BOOLEAN DEFAULT TRUE;
