// Response types mirroring the eval-harness API schemas

export type ScenarioListItem = {
	id: string;
	scenario_name: string;
	title: string;
	lifecycle_status: string;
	verification_status: string;
	benchmark_run_count: number;
	created_at: string;
	updated_at?: string | null;
	last_run_at?: string | null;
	run_count?: number;
	tags?: string[];
	current_verified_revision_id: string | null;
	last_verified_at: string | null;
};

export type ScenarioRevisionItem = {
	id: string;
	revision_number: number;
	target_image: string;
	summary: string;
	created_at: string;
};

export type ScenarioDetail = {
	id: string;
	scenario_name: string;
	title: string;
	lifecycle_status: string;
	verification_status: string;
	benchmark_run_count: number;
	created_at: string;
	last_verified_at: string | null;
	current_verified_revision_id: string | null;
	revisions: ScenarioRevisionItem[];
};

export type RunListItem = {
	id: string;
	kind: "setup" | "benchmark";
	scenario_revision_id: string;
	status: string;
	created_at: string;
	updated_at: string | null;
	correction_count: number | null;
	max_corrections: number | null;
	staging_handle_id: string | null;
	broken_image_id: string | null;
	failure_reason: string | null;
	verified_setup_run_id: string | null;
	subject_count: number | null;
	started_at: string | null;
	finished_at: string | null;
};

export type RunListResponse = {
	items: RunListItem[];
	total: number;
	page: number;
	page_size: number;
};

export type SetupRunItem = {
	id: string;
	scenario_revision_id: string;
	status: string;
	staging_handle_id: string | null;
	correction_count: number;
	max_corrections: number;
	broken_image_id: string | null;
	failure_reason: string | null;
	planner_approved_at: string | null;
	backend_metadata: Record<string, unknown> | null;
	created_at: string;
	updated_at: string | null;
};

export type BenchmarkRunItem = {
	id: string;
	scenario_revision_id: string;
	verified_setup_run_id: string;
	status: string;
	subject_count: number;
	started_at: string | null;
	finished_at: string | null;
	created_at: string;
	metadata_json: Record<string, unknown> | null;
};

export type EvaluationRunItem = {
	id: string;
	benchmark_run_id: string;
	subject_id: string;
	clone_handle_id: string | null;
	status: string;
	repair_success: boolean | null;
	resolution_result: Record<string, unknown> | null;
	subject_metadata: Record<string, unknown> | null;
	started_at: string | null;
	finished_at: string | null;
};

export type RunEventItem = {
	id: string;
	seq: number;
	actor_role: string;
	event_kind: string;
	payload: Record<string, unknown> | null;
	created_at: string;
};

export type SetupRunEventItem = {
	id: string;
	round_index: number;
	seq: number;
	actor_role: string;
	event_kind: string;
	payload: Record<string, unknown> | null;
	created_at: string;
};

export type JudgeJobItem = {
	id: string;
	benchmark_run_id: string;
	status: string;
	judge_adapter_type: string;
	started_at: string | null;
	finished_at: string | null;
	created_at: string;
};

export type JudgeItemDetail = {
	id: string;
	evaluation_run_id: string | null;
	blind_label: string;
	parsed_scores: Record<string, unknown> | null;
	summary: string;
	kind: string;
	judge_name: string | null;
};

export type JudgeJobDetail = {
	id: string;
	benchmark_run_id: string;
	status: string;
	judge_adapter_type: string;
	rubric: Record<string, unknown> | null;
	metadata: Record<string, unknown> | null;
	started_at: string | null;
	finished_at: string | null;
	created_at: string;
	judge_items: JudgeItemDetail[];
};

export type SubjectItem = {
	id: string;
	subject_name: string;
	adapter_type: string;
	display_name: string;
	adapter_config: Record<string, unknown> | null;
	is_active: boolean;
	created_at: string;
};

// M4 — Authoring types

export type GenerateRequest = {
	planning_brief: string;
	target_image?: string;
	scenario_name_hint?: string;
	tags?: string[];
	constraints?: string[];
};

export type GenerateResponse = {
	scenario: Record<string, unknown>;
};

export type ValidateRequest = {
	scenario_json: Record<string, unknown>;
};

export type ValidateResponse = {
	valid: boolean;
	errors: string[];
};

export type CreateScenarioRequest = {
	title: string;
	scenario_name_hint: string;
};

export type CreateScenarioResponse = {
	id: string;
	scenario_name: string;
	title: string;
	lifecycle_status: string;
	verification_status: string;
	created_at: string;
};

export type CreateRevisionRequest = {
	target_image: string;
	summary: string;
	what_it_tests: Record<string, unknown>;
	observable_problem_statement: string;
	initial_user_message?: string;
	sabotage_plan: Record<string, unknown>;
	verification_plan: Record<string, unknown>;
	judge_rubric: Record<string, unknown>;
	planner_metadata?: Record<string, unknown> | null;
};

export type CreateRevisionResponse = {
	id: string;
	revision_number: number;
	target_image: string;
	summary: string;
	created_at: string;
};

// M2 — Infra types

export type InstanceItem = {
	instance_id: string;
	state: string;
	instance_type: string;
	public_ip: string | null;
	tags: Record<string, string>;
	launched_at: string | null;
};

export type ImageItem = {
	image_id: string;
	name: string | null;
	state: string;
	tags: Record<string, string>;
	created_at: string | null;
};

export type PreflightResponse = {
	ok: boolean;
	message: string;
};

// M3 — Control types

export type VerifyRequest = {
	group_id?: string | null;
	revision_id?: string | null;
};

export type BenchmarkRequest = {
	setup_run_id: string;
	subject_ids?: string[] | null;
};

export type JudgeRequest = {
	mode?: string;
	anchor_subject?: string | null;
	bootstrap_samples?: number;
};

export type RunAllRequest = {
	group_id?: string | null;
	revision_id?: string | null;
	subject_ids?: string[] | null;
	judge_mode?: string;
	judge_anchor_subject?: string | null;
};

export type ControlResponse = {
	ok: boolean;
	scenario_id?: string;
	benchmark_run_id?: string;
	run_id?: string;
	adaption?: string;
	run_type?: string;
};

// M5 — Data browser types

export type DataTableResponse = {
	table: string;
	columns: string[];
	rows: Record<string, unknown>[];
	total: number;
	page: number;
	page_size: number;
};

export type TableMeta = {
	name: string;
	label: string;
};

export type SubjectCreateRequest = {
	subject_name: string;
	adapter_type: string;
	display_name: string;
	adapter_config: Record<string, unknown> | null;
	is_active: boolean;
};

export type SubjectPatchRequest = {
	adapter_type?: string;
	display_name?: string;
	adapter_config?: Record<string, unknown> | null;
	is_active?: boolean;
};

export type ArtifactExportRequest = {
	artifacts_root?: string;
};
