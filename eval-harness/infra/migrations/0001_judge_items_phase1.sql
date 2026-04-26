-- Phase 1: extend judge_items for multi-mode judging.
-- All columns are nullable/have defaults so existing absolute rows survive unchanged.

ALTER TABLE judge_items
    ADD COLUMN IF NOT EXISTS kind VARCHAR(32) NOT NULL DEFAULT 'absolute';

ALTER TABLE judge_items
    ADD COLUMN IF NOT EXISTS judge_name VARCHAR(120);

-- evaluation_run_id is kept for absolute back-compat; pairwise rows leave it NULL.
ALTER TABLE judge_items
    ALTER COLUMN evaluation_run_id DROP NOT NULL;

ALTER TABLE judge_items
    ADD COLUMN IF NOT EXISTS evaluation_run_id_a VARCHAR(36);

ALTER TABLE judge_items
    ADD COLUMN IF NOT EXISTS evaluation_run_id_b VARCHAR(36);

CREATE INDEX IF NOT EXISTS ix_judge_items_evaluation_run_id_a
    ON judge_items (evaluation_run_id_a);

CREATE INDEX IF NOT EXISTS ix_judge_items_evaluation_run_id_b
    ON judge_items (evaluation_run_id_b);
