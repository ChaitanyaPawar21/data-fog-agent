"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { apiFetch } from "@/lib/auth";
import { formatDate, type MergeConflict, type MergePreview, type Proposal } from "@/lib/catalog";

export default function ApprovalsPage() {
  return (
    <AppShell>
      <Approvals />
    </AppShell>
  );
}

async function fetchProposals(params: Record<string, string>): Promise<Proposal[]> {
  const response = await apiFetch(`/api/proposals?${new URLSearchParams(params)}`);
  return response.ok ? response.json() : [];
}

function Approvals() {
  const [pending, setPending] = useState<Proposal[] | null>(null);
  const [merges, setMerges] = useState<Proposal[]>([]);
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    const [waiting, running, done, failed] = await Promise.all([
      fetchProposals({ status: "pending" }),
      fetchProposals({ status: "approved", type: "merge" }),
      fetchProposals({ status: "done", type: "merge" }),
      fetchProposals({ status: "failed", type: "merge" }),
    ]);
    setPending(waiting);
    setMerges([...running, ...done, ...failed]);
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- loading data on mount
    load();
    // Merges run in the background, so keep their status fresh while the page is open.
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [load]);

  async function act(proposal: Proposal, action: "approve" | "reject" | "undo", done: string) {
    const response = await apiFetch(`/api/proposals/${proposal.id}/${action}`, { method: "POST" });
    if (response.ok) setMessage(done);
    else {
      const body = await response.json().catch(() => null);
      setMessage(body?.detail ?? "That didn't work. Try again.");
    }
    load();
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold">Approvals</h1>
        <p className="text-sm text-muted-foreground">
          Changes the agent proposed. Nothing happens until you approve it.
        </p>
      </div>
      {message && <p className="text-sm text-muted-foreground">{message}</p>}

      {pending === null && <p className="text-sm text-muted-foreground">Loading…</p>}
      {pending?.length === 0 && (
        <p className="text-sm text-muted-foreground">Nothing waiting for your review.</p>
      )}

      {pending?.map((proposal) => (
        <Card key={proposal.id}>
          <CardHeader className="flex flex-row items-start justify-between gap-4">
            <ProposalTitle proposal={proposal} />
            <div className="flex shrink-0 gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => act(proposal, "reject", `Rejected: ${titleOf(proposal)}.`)}
              >
                Reject
              </Button>
              <Button
                size="sm"
                onClick={() =>
                  act(
                    proposal,
                    "approve",
                    proposal.type === "merge"
                      ? `Merging into ${proposal.merge!.output_name}. This page updates when it's done.`
                      : `Description for ${proposal.asset_name} approved.`,
                  )
                }
              >
                {proposal.type === "merge" ? "Approve merge" : "Approve"}
              </Button>
            </div>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            {proposal.merge ? <MergeView merge={proposal.merge} /> : <DocView proposal={proposal} />}
          </CardContent>
        </Card>
      ))}

      {merges.length > 0 && (
        <section className="flex flex-col gap-2">
          <h2 className="font-medium">Merges</h2>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Result</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Approved</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {merges.map((proposal) => (
                <TableRow key={proposal.id}>
                  <TableCell>
                    <div className="font-medium">{proposal.merge!.output_name}</div>
                    <div className="text-xs text-muted-foreground">
                      from {proposal.merge!.files.length} files
                    </div>
                    {proposal.merge!.error && (
                      <div className="max-w-md text-xs whitespace-normal text-destructive">
                        {proposal.merge!.error}
                      </div>
                    )}
                  </TableCell>
                  <TableCell>
                    <Badge variant={proposal.status === "failed" ? "destructive" : "secondary"}>
                      {proposal.status === "approved" ? "running" : proposal.status}
                    </Badge>
                  </TableCell>
                  <TableCell>{formatDate(proposal.reviewed_at)}</TableCell>
                  <TableCell className="text-right">
                    {proposal.status === "done" && (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() =>
                          act(proposal, "undo", "Undoing: the original files are being put back.")
                        }
                      >
                        Undo
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </section>
      )}
    </div>
  );
}

function titleOf(proposal: Proposal): string {
  return proposal.type === "merge"
    ? `merge into ${proposal.merge!.output_name}`
    : `description for ${proposal.asset_name}`;
}

function ProposalTitle({ proposal }: { proposal: Proposal }) {
  if (proposal.type === "merge") {
    return (
      <div className="min-w-0">
        <CardTitle>
          Merge {proposal.merge!.files.length} copies into {proposal.merge!.output_name}
        </CardTitle>
        <p className="text-xs text-muted-foreground">
          Originals move to an _archive folder, nothing is deleted, and you can undo it ·
          proposed {formatDate(proposal.created_at)}
        </p>
      </div>
    );
  }
  return (
    <div className="min-w-0">
      <CardTitle>
        <Link href={`/catalog/${proposal.asset_id}`} className="hover:underline">
          {proposal.asset_name}
        </Link>
      </CardTitle>
      <p className="truncate text-xs text-muted-foreground">
        {proposal.asset_path} · drafted {formatDate(proposal.created_at)}
      </p>
    </div>
  );
}

function DocView({ proposal }: { proposal: Proposal }) {
  return (
    <>
      <p className="text-sm leading-relaxed">{proposal.description}</p>
      {proposal.columns && proposal.columns.length > 0 && (
        <div className="overflow-x-auto rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Column</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Proposed description</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {proposal.columns.map((column) => (
                <TableRow key={column.name}>
                  <TableCell className="font-mono text-xs">{column.name}</TableCell>
                  <TableCell className="text-muted-foreground">{column.data_type}</TableCell>
                  <TableCell className="whitespace-normal">{column.description ?? "—"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </>
  );
}

function MergeView({ merge }: { merge: MergePreview }) {
  const facts: [string, string | number][] = [
    ["Rows in all files", merge.total_rows],
    ["Exact duplicates dropped", merge.exact_duplicate_rows],
    ["Conflicting rows", merge.conflicting_keys],
    ["Rows after merging", merge.merged_rows],
    ["Matched on", merge.key_column ?? "whole rows"],
  ];
  return (
    <>
      <div className="overflow-x-auto rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>File (oldest first)</TableHead>
              <TableHead className="text-right">Rows</TableHead>
              <TableHead>Last modified</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {merge.files.map((file) => (
              <TableRow key={file.asset_id}>
                <TableCell>
                  <Link href={`/catalog/${file.asset_id}`} className="hover:underline">
                    {file.path}
                  </Link>
                </TableCell>
                <TableCell className="text-right tabular-nums">{file.rows}</TableCell>
                <TableCell>{formatDate(file.last_modified_at)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-5">
        {facts.map(([label, value]) => (
          <div key={label}>
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>

      {merge.conflicts.length > 0 && (
        <div className="flex flex-col gap-2">
          <p className="text-sm">
            Where files disagree, the most recently modified file wins. Values that differ:
          </p>
          <div className="overflow-x-auto rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{merge.key_column}</TableHead>
                  <TableHead>Versions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {merge.conflicts.map((conflict) => (
                  <ConflictRow key={String(conflict.key)} conflict={conflict} />
                ))}
              </TableBody>
            </Table>
          </div>
        </div>
      )}
    </>
  );
}

function ConflictRow({ conflict }: { conflict: MergeConflict }) {
  // Only the columns whose values differ between versions are worth showing.
  const columns = Object.keys(conflict.versions[0].row).filter((column) =>
    conflict.versions.some(
      (version) => String(version.row[column]) !== String(conflict.versions[0].row[column]),
    ),
  );
  return (
    <TableRow>
      <TableCell className="align-top tabular-nums">{conflict.key}</TableCell>
      <TableCell className="whitespace-normal">
        <ul className="flex flex-col gap-0.5">
          {conflict.versions.map((version) => (
            <li
              key={version.file}
              className={version.file === conflict.winner ? "font-medium" : "text-muted-foreground"}
            >
              {version.file}:{" "}
              {columns.map((column) => `${column} ${String(version.row[column])}`).join(", ")}
              {version.file === conflict.winner && " (kept)"}
            </li>
          ))}
        </ul>
      </TableCell>
    </TableRow>
  );
}
