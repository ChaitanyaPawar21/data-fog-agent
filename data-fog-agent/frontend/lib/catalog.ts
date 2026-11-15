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
  trust_factors: Record<string, number>;
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

export type MergeConflict = {
  key: string | number;
  versions: { file: string; row: Record<string, unknown> }[];
  winner: string;
};

export type MergePreview = {
  files: { asset_id: number; path: string; rows: number; last_modified_at: string }[];
  key_column: string | null;
  total_rows: number;
  exact_duplicate_rows: number;
  conflicting_keys: number;
  conflicts: MergeConflict[];
  merged_rows: number;
  output_name: string;
  output_path: string | null;
  groups: string[] | null;
  error: string | null;
};

export type Proposal = {
  id: number;
  type: "doc" | "merge";
  status: "pending" | "approved" | "rejected" | "done" | "failed" | "undone";
  created_at: string;
  reviewed_at: string | null;
  asset_id: number;
  asset_name: string;
  asset_path: string;
  description: string | null;
  columns: { name: string; data_type: string; description: string | null }[] | null;
  merge: MergePreview | null;
};

export type Alert = {
  id: number;
  type: "stale" | "schema_change" | "pii_found";
  message: string;
  created_at: string;
  resolved_at: string | null;
  asset_id: number;
  asset_name: string;
  asset_path: string;
  can_resolve: boolean;
};

/** Words for each trust factor key the API returns, with the most points it can give. */
export const TRUST_FACTORS: Record<string, [string, number]> = {
  freshness: ["Recently modified", 30],
  owner: ["Has an owner", 20],
  approved_description: ["Approved description", 20],
  recently_used: ["Used in the last 30 days", 15],
  not_a_copy: ["Not an older copy of another asset", 15],
};
