import { useCallback, useEffect, useState } from "react";

export type ThemePreference = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

const STORAGE_KEY = "operly:appearance";
const THEME_EVENT = "operly:theme-change";
const DARK_MEDIA = "(prefers-color-scheme: dark)";

function isThemePreference(value: string | null): value is ThemePreference {
  return value === "light" || value === "dark" || value === "system";
}

export function readThemePreference(): ThemePreference {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    return isThemePreference(stored) ? stored : "system";
  } catch {
    return "system";
  }
}

export function resolveTheme(preference: ThemePreference): ResolvedTheme {
  if (preference === "light" || preference === "dark") return preference;
  return window.matchMedia?.(DARK_MEDIA).matches ? "dark" : "light";
}

export function applyTheme(preference: ThemePreference): ResolvedTheme {
  const resolved = resolveTheme(preference);
  document.documentElement.dataset.theme = resolved;
  document.documentElement.dataset.themePreference = preference;
  document.documentElement.style.colorScheme = "dark";
  const meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
  if (meta) meta.content = "#0d0a15";
  return resolved;
}

export function initializeTheme(): ThemePreference {
  const preference = readThemePreference();
  applyTheme(preference);
  return preference;
}

export function useThemePreference() {
  const [preference, setPreferenceState] = useState<ThemePreference>(() => readThemePreference());
  const [resolvedTheme, setResolvedTheme] = useState<ResolvedTheme>(() => resolveTheme(readThemePreference()));

  const setPreference = useCallback((next: ThemePreference) => {
    try { window.localStorage.setItem(STORAGE_KEY, next); } catch { /* local persistence is best effort */ }
    setPreferenceState(next);
    setResolvedTheme(applyTheme(next));
    window.dispatchEvent(new CustomEvent(THEME_EVENT, { detail: next }));
  }, []);

  useEffect(() => {
    setResolvedTheme(applyTheme(preference));
    const media = window.matchMedia?.(DARK_MEDIA);
    const onSystemTheme = () => { if (preference === "system") setResolvedTheme(applyTheme("system")); };
    const onStorage = (event: StorageEvent) => {
      if (event.key !== STORAGE_KEY || !isThemePreference(event.newValue)) return;
      setPreferenceState(event.newValue);
      setResolvedTheme(applyTheme(event.newValue));
    };
    const onThemeEvent = (event: Event) => {
      const next = (event as CustomEvent<ThemePreference>).detail;
      if (!isThemePreference(next)) return;
      setPreferenceState(next);
      setResolvedTheme(applyTheme(next));
    };
    media?.addEventListener?.("change", onSystemTheme);
    window.addEventListener("storage", onStorage);
    window.addEventListener(THEME_EVENT, onThemeEvent);
    return () => {
      media?.removeEventListener?.("change", onSystemTheme);
      window.removeEventListener("storage", onStorage);
      window.removeEventListener(THEME_EVENT, onThemeEvent);
    };
  }, [preference]);

  return { preference, resolvedTheme, setPreference };
}
