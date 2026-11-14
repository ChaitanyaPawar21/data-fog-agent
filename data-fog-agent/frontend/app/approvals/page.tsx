"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { AppShell } from "@/components/app-shell";
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
import { formatDate, type DocProposal } from "@/lib/catalog";

export default function ApprovalsPage() {
  return (
    <AppShell>
      <Approvals />
    </AppShell>
  );
}

function Approvals() {
  const [proposals, setProposals] = useState<DocProposal[] | null>(null);
  const [message, setMessage] = useState("");

  const loadProposals = useCallback(async () => {
    const response = await apiFetch("/api/proposals");
    if (response.ok) setProposals(await response.json());
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- loading data on mount
    loadProposals();
  }, [loadProposals]);

  async function review(proposal: DocProposal, action: "approve" | "reject") {
    const response = await apiFetch(`/api/proposals/${proposal.id}/${action}`, { method: "POST" });
    if (response.ok) {
      setMessage(
        action === "approve"
          ? `Description for ${proposal.asset_name} approved.`
          : `Draft for ${proposal.asset_name} rejected.`,
      );
    } else {
      const body = await response.json().catch(() => null);
      setMessage(body?.detail ?? "That didn't work. Try again.");
    }
    loadProposals();
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold">Approvals</h1>
        <p className="text-sm text-muted-foreground">
          Descriptions the agent drafted. Nothing reaches the catalogue until you approve it.
        </p>
      </div>
      {message && <p className="text-sm text-muted-foreground">{message}</p>}

      {proposals === null && <p className="text-sm text-muted-foreground">Loading…</p>}
      {proposals?.length === 0 && (
        <p className="text-sm text-muted-foreground">Nothing waiting for your review.</p>
      )}

      {proposals?.map((proposal) => (
        <Card key={proposal.id}>
          <CardHeader className="flex flex-row items-start justify-between gap-4">
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
            <div className="flex shrink-0 gap-2">
              <Button variant="outline" size="sm" onClick={() => review(proposal, "reject")}>
                Reject
              </Button>
              <Button size="sm" onClick={() => review(proposal, "approve")}>
                Approve
              </Button>
            </div>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <p className="text-sm leading-relaxed">{proposal.description}</p>
            {proposal.columns.length > 0 && (
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
                        <TableCell className="whitespace-normal">
                          {column.description ?? "—"}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
