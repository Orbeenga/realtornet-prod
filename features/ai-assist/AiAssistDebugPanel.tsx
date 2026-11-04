"use client";

import { useEffect } from "react";
import { Button } from "@/components";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { useAiAssistLogs } from "./hooks";
import { useAiAssistPanelStore } from "./store";
import { AI_ASSIST_PROVIDER_PATH } from "./types";
import type { AiAssistKind, AiAssistMeta } from "./types";

/* Shared AI-assist debug panel lives HERE ONLY (AiAssistDebugPanel).
   Mount once per page that offers suggestions; state is global via the store. */
function MetaRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between gap-3 py-1">
      <dt className="shrink-0 text-xs font-medium text-gray-500 dark:text-gray-400">
        {label}
      </dt>
      <dd className="min-w-0 break-all text-right font-mono text-xs text-gray-900 dark:text-gray-100">
        {value}
      </dd>
    </div>
  );
}

function LastSuggestion({
  kind,
  meta,
  at,
}: {
  kind: AiAssistKind;
  meta: AiAssistMeta;
  at: string;
}) {
  return (
    <dl className="divide-y divide-gray-100 rounded-xl border border-gray-200 px-3 py-1 dark:divide-gray-800 dark:border-gray-800">
      <MetaRow label="Kind" value={kind} />
      <MetaRow label="Model ID" value={meta.model_id} />
      <MetaRow label="Endpoint host" value={meta.endpoint_host} />
      <MetaRow label="Endpoint path" value={AI_ASSIST_PROVIDER_PATH} />
      <MetaRow label="Local request ID" value={meta.local_request_id} />
      <MetaRow label="Provider request ID" value={meta.provider_request_id} />
      <MetaRow label="Latency" value={`${meta.latency_ms} ms`} />
      <MetaRow label="Received at" value={at} />
    </dl>
  );
}

export function AiAssistDebugPanel() {
  const open = useAiAssistPanelStore((state) => state.open);
  const pinned = useAiAssistPanelStore((state) => state.pinned);
  const lastKind = useAiAssistPanelStore((state) => state.lastKind);
  const lastMeta = useAiAssistPanelStore((state) => state.lastMeta);
  const lastAt = useAiAssistPanelStore((state) => state.lastAt);
  const setOpen = useAiAssistPanelStore((state) => state.setOpen);
  const setPinned = useAiAssistPanelStore((state) => state.setPinned);
  const hydratePinned = useAiAssistPanelStore((state) => state.hydratePinned);
  const logsQuery = useAiAssistLogs(open);

  useEffect(() => {
    hydratePinned();
  }, [hydratePinned]);

  return (
    <Sheet
      open={open}
      onOpenChange={(next) => {
        if (!pinned) setOpen(next);
      }}
    >
      <SheetContent
        side="right"
        className="sm:max-w-md"
        showCloseButton={!pinned}
      >
        <SheetHeader>
          <SheetTitle>AI assist debug</SheetTitle>
          <SheetDescription>
            Last suggestion metadata and recent calls. Drafts are AI-drafted -
            a human always reviews before submitting.
          </SheetDescription>
        </SheetHeader>
        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => setPinned(!pinned)}
            >
              {pinned ? "Unpin panel" : "Pin panel"}
            </Button>
            {pinned ? (
              <span className="text-xs text-gray-500 dark:text-gray-400">
                Pinned - unpin to close
              </span>
            ) : null}
          </div>
          {lastKind && lastMeta && lastAt ? (
            <LastSuggestion kind={lastKind} meta={lastMeta} at={lastAt} />
          ) : (
            <p className="text-sm text-gray-500 dark:text-gray-400">
              No suggestions yet this session. Use Suggest reason or Suggest
              reply and the request IDs will appear here.
            </p>
          )}
          {logsQuery.data && logsQuery.data.length > 0 ? (
            <div className="space-y-2">
              <h3 className="text-sm font-medium text-gray-900 dark:text-white">
                Recent calls
              </h3>
              <ul className="space-y-1">
                {logsQuery.data.map((entry) => (
                  <li
                    key={entry.local_request_id}
                    className="rounded-lg border border-gray-200 px-3 py-2 dark:border-gray-800"
                  >
                    <p className="font-mono text-xs text-gray-700 dark:text-gray-300">
                      {entry.kind} - {entry.success ? "ok" : "failed"}
                    </p>
                    <p className="mt-0.5 break-all font-mono text-[11px] text-gray-500 dark:text-gray-400">
                      {entry.local_request_id} /{" "}
                      {entry.provider_request_id ?? "no provider id"}
                    </p>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      </SheetContent>
    </Sheet>
  );
}
