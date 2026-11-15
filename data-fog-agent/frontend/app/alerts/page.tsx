"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { apiFetch } from "@/lib/auth";
import { type Alert, formatDate, selectClassName } from "@/lib/catalog";

const TYPE_LABELS: Record<Alert["type"], string> = {
  stale: "Stale",
  schema_change: "Schema change",
  pii_found: "Personal data",
};

export default function AlertsPage() {
  return (
    <AppShell>
      <Alerts />
    </AppShell>
  );
}

function Alerts() {
  const [status, setStatus] = useState<"open" | "resolved">("open");
  const [type, setType] = useState("");
  const [alerts, setAlerts] = useState<Alert[] | null>(null);
  const [error, setError] = useState("");

  const loadAlerts = useCallback(async () => {
    const params = new URLSearchParams({ status });
    if (type) params.set("type", type);
    const response = await apiFetch(`/api/alerts?${params}`);
    if (response.ok) setAlerts(await response.json());
  }, [status, type]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- loading data when filters change
    loadAlerts();
  }, [loadAlerts]);

  async function resolve(alert: Alert) {
    const response = await apiFetch(`/api/alerts/${alert.id}/resolve`, { method: "POST" });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      setError(body?.detail ?? "That didn't work. Try again.");
    }
    loadAlerts();
  }

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-xl font-semibold">Alerts</h1>

      <div className="flex flex-wrap gap-2">
        <select
          aria-label="Status"
          className={selectClassName}
          value={status}
          onChange={(event) => setStatus(event.target.value as "open" | "resolved")}
        >
          <option value="open">Open</option>
          <option value="resolved">Resolved</option>
        </select>
        <select
          aria-label="Type"
          className={selectClassName}
          value={type}
          onChange={(event) => setType(event.target.value)}
        >
          <option value="">All types</option>
          {Object.entries(TYPE_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}

      {alerts === null ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : alerts.length === 0 ? (
        <p className="text-sm text-muted-foreground">No {status} alerts.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Type</TableHead>
              <TableHead>Asset</TableHead>
              <TableHead>What happened</TableHead>
              <TableHead>{status === "open" ? "Raised" : "Resolved"}</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {alerts.map((alert) => (
              <TableRow key={alert.id}>
                <TableCell>
                  <Badge variant={alert.type === "pii_found" ? "destructive" : "secondary"}>
                    {TYPE_LABELS[alert.type]}
                  </Badge>
                </TableCell>
                <TableCell>
                  <Link href={`/catalog/${alert.asset_id}`} className="font-medium hover:underline">
                    {alert.asset_name}
                  </Link>
                </TableCell>
                <TableCell className="max-w-md whitespace-normal">{alert.message}</TableCell>
                <TableCell>
                  {formatDate(status === "open" ? alert.created_at : alert.resolved_at)}
                </TableCell>
                <TableCell className="text-right">
                  {status === "open" && alert.can_resolve && (
                    <Button variant="outline" size="sm" onClick={() => resolve(alert)}>
                      Resolve
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
