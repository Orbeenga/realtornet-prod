/**
 * AI-assist types, generated from the LOCAL backend spec - do not hand-edit.
 *
 * Source: local backend `/api/v1/openapi.json`, trimmed to `/api/v1/ai-assist/*`
 * and generated with the repo's own `openapi-typescript` (see `pnpm gen:types`).
 * After the backend deploys to production, regenerate the canonical
 * `src/types/api.generated.ts` against production and re-point these aliases.
 */
import type { components } from "./api.generated.local";

export type AiAssistActionType = components["schemas"]["AiAssistActionType"];
export type ModerationReasonRequest =
  components["schemas"]["ModerationReasonRequest"];
export type InquiryReplyAssistRequest =
  components["schemas"]["InquiryReplyAssistRequest"];
export type AiAssistMeta = components["schemas"]["AiAssistMeta"];
export type AiAssistResponse = components["schemas"]["AiAssistResponse"];
export type AiAssistLogEntry = components["schemas"]["AiAssistLogEntry"];

export type AiAssistKind = "moderation-reason" | "inquiry-reply";

/** Provider chat path - the backend appends this to META_MODEL_BASE_URL. */
export const AI_ASSIST_PROVIDER_PATH = "/chat/completions";
