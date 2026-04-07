import { useEffect, useState } from "react";
import { useSettings } from "../../hooks/useSettings";
import type { AppSettingsConfig, AppSettingsPatch, ComponentKey, ComponentSettings, ComponentSettingsPatch } from "../../types";
import { COMPONENT_KEYS } from "../../types";

const MODEL_OPTIONS: Record<string, string[]> = {
  openai: ["gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano"],
  anthropic: ["claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
  local: ["qwen2.5:7b"],
};

const COMPONENT_LABELS: Record<ComponentKey, string> = {
  classifier: "Classifier",
  contextualizer: "Contextualizer",
  responder: "Responder",
  magi_eager: "Eager",
  magi_skeptic: "Skeptic",
  magi_historian: "Historian",
  magi_arbiter: "Arbiter",
  magi_lite_eager: "Eager (lite)",
  magi_lite_skeptic: "Skeptic (lite)",
  magi_lite_historian: "Historian (lite)",
  magi_lite_arbiter: "Arbiter (lite)",
  history_summarizer: "History Summarizer",
  context_summarizer: "Context Summarizer",
  memory_extractor: "Memory Extractor",
  registry_updater: "Registry Updater",
  ingest_enricher: "Ingest Enricher",
  chat_namer: "Chat Namer",
};

const CORE_PIPELINE_KEYS: ComponentKey[] = ["classifier", "contextualizer", "responder"];
const MAGI_FULL_KEYS: ComponentKey[] = ["magi_eager", "magi_skeptic", "magi_historian", "magi_arbiter"];
const MAGI_LITE_KEYS: ComponentKey[] = ["magi_lite_eager", "magi_lite_skeptic", "magi_lite_historian", "magi_lite_arbiter"];
const ADVANCED_KEYS: ComponentKey[] = [
  "history_summarizer", "context_summarizer", "memory_extractor",
  "registry_updater", "ingest_enricher", "chat_namer",
];

function computePatch(
  original: AppSettingsConfig,
  draft: AppSettingsConfig,
): AppSettingsPatch {
  const patch: AppSettingsPatch = {};
  for (const key of COMPONENT_KEYS) {
    const orig = original[key];
    const next = draft[key];
    const compPatch: ComponentSettingsPatch = {};
    let changed = false;
    if (next.provider !== orig.provider) { compPatch.provider = next.provider; changed = true; }
    if (next.model !== orig.model) { compPatch.model = next.model; changed = true; }
    if (next.reasoning_effort !== orig.reasoning_effort) { compPatch.reasoning_effort = next.reasoning_effort; changed = true; }
    if (changed) {
      patch[key] = compPatch;
    }
  }
  return patch;
}

type ComponentRowProps = {
  compKey: ComponentKey;
  value: ComponentSettings;
  onChange: (key: ComponentKey, patch: Partial<ComponentSettings>) => void;
};

function ComponentRow({ compKey, value, onChange }: ComponentRowProps) {
  const listId = `model-list-${compKey}`;
  const knownModels = MODEL_OPTIONS[value.provider] ?? [];

  function handleProviderChange(e: React.ChangeEvent<HTMLSelectElement>) {
    onChange(compKey, { provider: e.target.value, model: "", is_default: false });
  }

  function handleModelChange(e: React.ChangeEvent<HTMLInputElement>) {
    onChange(compKey, { model: e.target.value, is_default: false });
  }

  function handleEffortChange(e: React.ChangeEvent<HTMLSelectElement>) {
    onChange(compKey, { reasoning_effort: e.target.value, is_default: false });
  }

  return (
    <div className="settings-component-row">
      <span className="settings-component-label">
        {COMPONENT_LABELS[compKey]}
        {value.is_default ? <span className="settings-default-badge">default</span> : null}
      </span>
      <select value={value.provider} onChange={handleProviderChange} aria-label={`${COMPONENT_LABELS[compKey]} provider`}>
        <option value="openai">openai</option>
        <option value="anthropic">anthropic</option>
        <option value="local">local</option>
      </select>
      <input
        list={listId}
        value={value.model}
        onChange={handleModelChange}
        placeholder="model"
        aria-label={`${COMPONENT_LABELS[compKey]} model`}
      />
      <datalist id={listId}>
        {knownModels.map((m) => <option key={m} value={m} />)}
      </datalist>
      <select value={value.reasoning_effort} onChange={handleEffortChange} aria-label={`${COMPONENT_LABELS[compKey]} reasoning effort`}>
        <option value="">none</option>
        <option value="low">low</option>
        <option value="medium">medium</option>
        <option value="high">high</option>
      </select>
    </div>
  );
}

type SettingsDialogProps = {
  onClose: () => void;
};

export function SettingsDialog({ onClose }: SettingsDialogProps) {
  const { settings, loading, saving, error: hookError, fetchSettings, saveSettings } = useSettings();
  const [draft, setDraft] = useState<AppSettingsConfig | null>(null);
  const [tab, setTab] = useState<"core" | "advanced">("core");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState(false);

  useEffect(() => {
    void fetchSettings();
  }, []);

  useEffect(() => {
    if (settings && !draft) {
      setDraft(settings);
    }
  }, [settings, draft]);

  function updateDraft(key: ComponentKey, patch: Partial<ComponentSettings>) {
    setDraft((current) => {
      if (!current) return current;
      return { ...current, [key]: { ...current[key], ...patch, is_default: false } };
    });
    setSaveSuccess(false);
  }

  async function handleSave() {
    if (!settings || !draft) return;
    const patch = computePatch(settings, draft);
    if (Object.keys(patch).length === 0) {
      onClose();
      return;
    }
    setSaveError(null);
    setSaveSuccess(false);
    try {
      await saveSettings(patch);
      setSaveSuccess(true);
    } catch (err) {
      setSaveError((err as Error).message || "Failed to save settings.");
    }
  }

  function renderComponentGroup(keys: ComponentKey[]) {
    if (!draft) return null;
    return keys.map((key) => (
      <ComponentRow key={key} compKey={key} value={draft[key]} onChange={updateDraft} />
    ));
  }

  const displayError = saveError ?? hookError;

  return (
    <div className="dialog-backdrop" onClick={onClose}>
      <div
        className="dialog-card settings-dialog-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-dialog-title"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="dialog-header">
          <div>
            <p className="eyebrow">Admin</p>
            <h2 id="settings-dialog-title">Model settings</h2>
          </div>
          <button type="button" className="icon-button" aria-label="Close settings" onClick={onClose}>
            ×
          </button>
        </div>

        <div className="settings-tabs">
          <button
            type="button"
            className={`settings-tab${tab === "core" ? " active" : ""}`}
            onClick={() => setTab("core")}
          >
            Core &amp; Magi
          </button>
          <button
            type="button"
            className={`settings-tab${tab === "advanced" ? " active" : ""}`}
            onClick={() => setTab("advanced")}
          >
            Advanced
          </button>
        </div>

        <div className="settings-body">
          {loading ? (
            <p className="settings-loading">Loading settings…</p>
          ) : !draft ? (
            <p className="settings-loading">No settings loaded.</p>
          ) : tab === "core" ? (
            <>
              <section className="settings-section">
                <h3 className="settings-section-title">Core pipeline</h3>
                {renderComponentGroup(CORE_PIPELINE_KEYS)}
              </section>
              <section className="settings-section">
                <h3 className="settings-section-title">Magi (full)</h3>
                {renderComponentGroup(MAGI_FULL_KEYS)}
              </section>
              <section className="settings-section">
                <h3 className="settings-section-title">Magi lite</h3>
                {renderComponentGroup(MAGI_LITE_KEYS)}
              </section>
            </>
          ) : (
            <section className="settings-section">
              <h3 className="settings-section-title">Utility</h3>
              {renderComponentGroup(ADVANCED_KEYS)}
            </section>
          )}
        </div>

        {displayError ? <p className="error-banner">{displayError}</p> : null}
        {saveSuccess ? <p className="settings-success">Settings saved.</p> : null}

        <div className="dialog-actions">
          <button type="button" className="ghost-button compact" onClick={onClose}>
            Cancel
          </button>
          <button type="button" onClick={() => void handleSave()} disabled={saving || loading}>
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
