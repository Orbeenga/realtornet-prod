"use client";

import { create } from "zustand";
import type { AiAssistKind, AiAssistMeta } from "./types";

const PIN_STORAGE_KEY = "ai-assist-panel-pinned";

type AiAssistPanelState = {
  open: boolean;
  pinned: boolean;
  lastKind: AiAssistKind | null;
  lastMeta: AiAssistMeta | null;
  lastAt: string | null;
  setOpen: (open: boolean) => void;
  setPinned: (pinned: boolean) => void;
  hydratePinned: () => void;
  recordSuggestion: (kind: AiAssistKind, meta: AiAssistMeta) => void;
};

function readPinned(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(PIN_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

/* Shared AI-assist panel state lives HERE ONLY (useAiAssistPanelStore).
   Every suggest button records here; the debug sheet reads from here. */
export const useAiAssistPanelStore = create<AiAssistPanelState>()((set) => ({
  open: false,
  pinned: false,
  lastKind: null,
  lastMeta: null,
  lastAt: null,
  setOpen: (open) => set({ open }),
  setPinned: (pinned) => {
    if (typeof window !== "undefined") {
      try {
        window.localStorage.setItem(PIN_STORAGE_KEY, pinned ? "1" : "0");
      } catch {
        // Private mode: the pin just will not persist. Nothing else to do.
      }
    }
    set({ pinned });
  },
  hydratePinned: () => set({ pinned: readPinned() }),
  recordSuggestion: (kind, meta) =>
    set({ lastKind: kind, lastMeta: meta, lastAt: new Date().toISOString() }),
}));
