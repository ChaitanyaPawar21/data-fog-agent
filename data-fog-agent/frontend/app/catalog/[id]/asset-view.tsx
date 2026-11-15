"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { apiFetch } from "@/lib/auth";
import { type AssetDetail, formatDate, TRUST_FACTORS } from "@/lib/catalog";

export function AssetView({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return (
    <AppShell>
      <Asset id={id} />
    </AppShell>
  );
}

function Asset({ id }: { id: string }) {
  const [asset, setAsset] = useState<AssetDetail | null>(null);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    apiFetch(`/api/catalog/assets/${id}`).then(async (response) => {
      if (response.ok) setAsset(await response.json());
      else setNotFound(true);
    });
  }, [id]);

  if (notFound) {
    return (
      <p className="text-sm">
        This asset doesn&apos;t exist or you don&apos;t have access to it.{" "}
        <Link href="/catalog" className="underline">
          Back to the catalogue
        </Link>
      </p>
    );
  }
  if (!asset) return <p className="text-sm text-muted-foreground">Loading…</p>;

  const facts: [string, string][] = [
    ["Source", asset.source_name],
    ["Type", asset.type],
    ["Owner", asset.owner_name ?? "Unknown"],
    ["Rows", asset.row_count?.toLocaleString("en-IN") ?? "—"],
    ["Last modified", formatDate(asset.last_modified_at)],
    ["Last seen in a sync", formatDate(asset.last_seen_at)],
    ["Visible to", asset.groups.length ? asset.groups.join(", ") : "Admins only"],
    ["Trust score", asset.trust_score?.toString() ?? "Not scored yet"],
  ];

  return (
    <div className="flex flex-col gap-6">
      <div>
        <Link href="/catalog" className="text-sm text-muted-foreground hover:underline">
          ← Catalogue
        </Link>
        <h1 className="mt-1 flex items-center gap-2 text-xl font-semibold">
          {asset.name}
          {asset.archived && <Badge variant="secondary">Archived</Badge>}
        </h1>
        <p className="text-sm text-muted-foreground">{asset.path}</p>
      </div>

      <p className="text-sm">
        {asset.description ??
          (asset.description_status === "proposed" ? (
            <span className="text-muted-foreground">
              A drafted description is waiting in{" "}
              <Link href="/approvals" className="underline underline-offset-2">
                Approvals
              </Link>
              .
            </span>
          ) : (
            <span className="text-muted-foreground">No description yet.</span>
          ))}
      </p>

      <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
        {facts.map(([label, value]) => (
          <div key={label}>
            <dt className="text-muted-foreground">{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>

      <section className="flex flex-col gap-1 text-sm">
        <h2 className="font-medium">Why this trust score</h2>
        <ul className="flex flex-col gap-0.5">
          {Object.entries(asset.trust_factors).map(([key, points]) => {
            const [label, most] = TRUST_FACTORS[key] ?? [key, points];
            return (
              <li key={key} className="flex max-w-sm justify-between gap-4">
                <span className={points ? "" : "text-muted-foreground"}>{label}</span>
                <span className="tabular-nums">
                  {points} / {most}
                </span>
              </li>
            );
          })}
        </ul>
      </section>

      {asset.columns.length > 0 && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Column</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Personal data</TableHead>
              <TableHead>Description</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {asset.columns.map((column) => (
              <TableRow key={column.name}>
                <TableCell className="font-mono text-xs">{column.name}</TableCell>
                <TableCell className="text-muted-foreground">{column.data_type}</TableCell>
                <TableCell>
                  {column.pii_type && <Badge variant="destructive">{column.pii_type}</Badge>}
                </TableCell>
                <TableCell>{column.description ?? "—"}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
