import { useState } from "react";
import { api } from "../api";
import type { AppSettingsConfig, AppSettingsPatch } from "../types";

export type UseSettingsResult = {
  settings: AppSettingsConfig | null;
  loading: boolean;
  saving: boolean;
  error: string | null;
  fetchSettings: () => Promise<void>;
  saveSettings: (patch: AppSettingsPatch) => Promise<void>;
};

export function useSettings(): UseSettingsResult {
  const [settings, setSettings] = useState<AppSettingsConfig | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function fetchSettings() {
    setLoading(true);
    setError(null);
    try {
      const data = await api.getSettings();
      setSettings(data);
    } catch (err) {
      setError((err as Error).message || "Failed to load settings.");
    } finally {
      setLoading(false);
    }
  }

  async function saveSettings(patch: AppSettingsPatch) {
    setSaving(true);
    setError(null);
    try {
      const updated = await api.updateSettings(patch);
      setSettings(updated);
    } catch (err) {
      // Re-throw so SettingsDialog can catch and keep the dialog open
      const message = (err as Error).message || "Failed to save settings.";
      setError(message);
      throw new Error(message);
    } finally {
      setSaving(false);
    }
  }

  return { settings, loading, saving, error, fetchSettings, saveSettings };
}
