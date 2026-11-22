'use client';

import { useSyncExternalStore } from 'react';

const storageKey = 'weiran.edu.theme';
let darkPreference = false;

function readPreference() {
  try {
    return window.localStorage.getItem(storageKey) === 'dark';
  } catch {
    return darkPreference;
  }
}

function subscribe(listener: () => void) {
  window.addEventListener('storage', listener);
  window.addEventListener(storageKey, listener);
  return () => {
    window.removeEventListener('storage', listener);
    window.removeEventListener(storageKey, listener);
  };
}

export function useDarkMode() {
  return useSyncExternalStore(subscribe, readPreference, () => false);
}

export function setDarkMode(dark: boolean) {
  darkPreference = dark;
  try {
    window.localStorage.setItem(storageKey, dark ? 'dark' : 'light');
  } catch {
    // Keep the preference in memory when storage is unavailable.
  }
  window.dispatchEvent(new Event(storageKey));
}
