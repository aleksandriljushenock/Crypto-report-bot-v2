-- V44 operational integrity
-- Prevent duplicate durable learning observations by metadata fingerprint.
CREATE UNIQUE INDEX IF NOT EXISTS uq_learning_observations_fingerprint
ON learning_observations ((metadata->>'fingerprint'))
WHERE metadata->>'fingerprint' IS NOT NULL;
