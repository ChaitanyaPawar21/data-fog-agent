"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { apiFetch } from "@/lib/auth";
import { type AssetSummary, formatDate, selectClassName } from "@/lib/catalog";

export default function CatalogPage() {
  return (
    <AppShell>
      <Catalog />
    </AppShell>
  );
}

function Catalog() {
  const [query, setQuery] = useState("");
  const [type, setType] = useState("");
  const [pii, setPii] = useState("");
  const [sourceId, setSourceId] = useState("");
  const [minTrust, setMinTrust] = useState("");
  const [assets, setAssets] = useState<AssetSummary[] | null>(null);
  // Members can't list sources, so the source filter is built from what search returns.
  const [sources, setSources] = useState<Map<number, string>>(new Map());

  useEffect(() => {
    const params = new URLSearchParams();
    if (query) params.set("q", query);
    if (type) params.set("type", type);
    if (pii) params.set("has_pii", pii);
    if (sourceId) params.set("source_id", sourceId);
    if (minTrust) params.set("min_trust", minTrust);

    // Wait for a pause in typing, and ignore answers to searches that were replaced.
    let cancelled = false;
    const timer = setTimeout(async () => {
      const response = await apiFetch(`/api/catalog/assets?${params}`);
      if (cancelled || !response.ok) return;
      const found: AssetSummary[] = await response.json();
      setAssets(found);
      setSources((known) => {
        const next = new Map(known);
        for (const asset of found) next.set(asset.source_id, asset.source_name);
        return next;
      });
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [query, type, pii, sourceId, minTrust]);

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-xl font-semibold">Catalogue</h1>

      <div className="flex flex-wrap gap-2">
        <Input
          aria-label="Search"
          placeholder="Search names, paths and descriptions"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          className="max-w-sm"
        />
        <select
          aria-label="Source"
          className={selectClassName}
          value={sourceId}
          onChange={(event) => setSourceId(event.target.value)}
        >
          <option value="">All sources</option>
          {[...sources].map(([id, name]) => (
            <option key={id} value={id}>
              {name}
            </option>
          ))}
        </select>
        <select
          aria-label="Type"
          className={selectClassName}
          value={type}
          onChange={(event) => setType(event.target.value)}
        >
          <option value="">All types</option>
          <option value="table">Tables</option>
          <option value="file">Files</option>
          <option value="channel">Channels</option>
        </select>
        <select
          aria-label="Personal data"
          className={selectClassName}
          value={pii}
          onChange={(event) => setPii(event.target.value)}
        >
          <option value="">Any data</option>
          <option value="true">Has personal data</option>
          <option value="false">No personal data</option>
        </select>
        <select
          aria-label="Trust score"
          className={selectClassName}
          value={minTrust}
          onChange={(event) => setMinTrust(event.target.value)}
        >
          <option value="">Any trust</option>
          <option value="50">Trust 50+</option>
          <option value="70">Trust 70+</option>
        </select>
      </div>

      {assets === null ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : assets.length === 0 ? (
        <p className="text-sm text-muted-foreground">No assets match.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead className="text-right">Trust</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Source</TableHead>
              <TableHead className="text-right">Rows</TableHead>
              <TableHead>Last modified</TableHead>
              <TableHead>Owner</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {assets.map((asset) => (
              <TableRow key={asset.id}>
                <TableCell>
                  <Link href={`/catalog/${asset.id}`} className="font-medium hover:underline">
                    {asset.name}
                  </Link>
                  {asset.has_pii && (
                    <Badge variant="destructive" className="ml-2">
                      PII
                    </Badge>
                  )}
                  <div className="text-xs text-muted-foreground">{asset.path}</div>
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {asset.trust_score ?? "—"}
                </TableCell>
                <TableCell className="capitalize">{asset.type}</TableCell>
                <TableCell>{asset.source_name}</TableCell>
                <TableCell className="text-right tabular-nums">
                  {asset.row_count?.toLocaleString("en-IN") ?? "—"}
                </TableCell>
                <TableCell>{formatDate(asset.last_modified_at)}</TableCell>
                <TableCell>{asset.owner_name ?? "—"}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
