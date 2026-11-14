// Shapes returned by the backend catalogue and sources APIs.

export type AssetSummary = {
  id: number;
  type: "table" | "file" | "channel";
  name: string;
  path: string;
  source_id: number;
  source_name: string;
  description: string | null;
  owner_name: string | null;
  row_count: number | null;
  last_modified_at: string | null;
  trust_score: number | null;
  has_pii: boolean;
  archived: boolean;
};

export type AssetDetail = AssetSummary & {
  description_status: "none" | "proposed" | "approved";
  size_bytes: number | null;
  last_seen_at: string | null;
  groups: string[];
  columns: { name: string; data_type: string; description: string | null; pii_type: string | null }[];
};

export type Source = {
  id: number;
  type: "postgres" | "files" | "slack_export";
  name: string;
  status: string;
  last_synced_at: string | null;
  last_error: string | null;
  asset_count: number;
};

export function formatDate(value: string | null): string {
  return value ? new Date(value).toLocaleDateString("en-IN", { dateStyle: "medium" }) : "—";
}

export const selectClassName =
  "h-8 rounded-lg border border-input bg-transparent px-2 text-sm outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50";

export type DocProposal = {
  id: number;
  type: "doc" | "merge";
  status: "pending" | "approved" | "rejected" | "done" | "failed";
  created_at: string;
  asset_id: number;
  asset_name: string;
  asset_path: string;
  description: string;
  columns: { name: string; data_type: string; description: string | null }[];
};
