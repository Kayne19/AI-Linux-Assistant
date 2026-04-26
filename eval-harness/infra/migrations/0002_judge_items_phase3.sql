-- Phase 3: drop the (judge_job_id, evaluation_run_id) unique constraint.
-- Per-judge absolute rows now set evaluation_run_id directly instead of
-- stuffing the eval_run id into evaluation_run_id_a as a workaround.
-- The constraint name is "uq_judge_item_eval_run" as declared in postgres_models.py.

ALTER TABLE judge_items
    DROP CONSTRAINT IF EXISTS uq_judge_item_eval_run;
