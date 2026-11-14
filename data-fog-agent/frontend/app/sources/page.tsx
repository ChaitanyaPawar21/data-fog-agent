"use client";

import { useCallback, useEffect, useState } from "react";

import { AppShell, useUser } from "@/components/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { apiFetch } from "@/lib/auth";
import { formatDate, selectClassName, type Source } from "@/lib/catalog";

const EXAMPLE_CONFIG: Record<Source["type"], object> = {
  postgres: {
    dsn: "postgresql://readonly_user:password@host:5432/database",
    schemas: ["public"],
    groups: ["sales"],
  },
  files: { path: "/data/drive", folder_groups: { sales: ["sales"], hr: ["hr"] } },
  slack_export: { path: "/data/slack", channel_groups: { general: ["sales", "finance"] } },
};

export default function SourcesPage() {
  return (
    <AppShell>
      <Sources />
    </AppShell>
  );
}

function Sources() {
  const user = useUser();
  const [sources, setSources] = useState<Source[] | null>(null);
  const [message, setMessage] = useState("");

  const loadSources = useCallback(async () => {
    const response = await apiFetch("/api/sources");
    if (response.ok) setSources(await response.json());
  }, []);

  useEffect(() => {
    if (user.role !== "admin") return;
    // Syncs run in the background, so keep the statuses fresh while the page is open.
    // eslint-disable-next-line react-hooks/set-state-in-effect -- loading data on mount
    loadSources();
    const timer = setInterval(loadSources, 5000);
    return () => clearInterval(timer);
  }, [user.role, loadSources]);

  if (user.role !== "admin") {
    return <p className="text-sm">Only admins can manage sources.</p>;
  }

  async function syncNow(source: Source) {
    const response = await apiFetch(`/api/sources/${source.id}/sync`, { method: "POST" });
    const { queued } = await response.json();
    setMessage(
      queued ? `Sync of ${source.name} started.` : `${source.name} is already syncing.`,
    );
    loadSources();
  }

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-xl font-semibold">Sources</h1>
      {message && <p className="text-sm text-muted-foreground">{message}</p>}

      {sources === null ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Assets</TableHead>
              <TableHead>Last synced</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {sources.map((source) => (
              <TableRow key={source.id}>
                <TableCell className="font-medium">
                  {source.name}
                  {source.last_error && (
                    <p className="max-w-md text-xs whitespace-normal text-destructive">
                      {source.last_error}
                    </p>
                  )}
                </TableCell>
                <TableCell>{source.type}</TableCell>
                <TableCell>
                  <Badge variant={source.status === "error" ? "destructive" : "secondary"}>
                    {source.status}
                  </Badge>
                </TableCell>
                <TableCell className="text-right tabular-nums">{source.asset_count}</TableCell>
                <TableCell>{formatDate(source.last_synced_at)}</TableCell>
                <TableCell className="text-right">
                  <Button variant="outline" size="sm" onClick={() => syncNow(source)}>
                    Sync now
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <AddSourceForm onAdded={loadSources} />
    </div>
  );
}

function AddSourceForm({ onAdded }: { onAdded: () => void }) {
  const [type, setType] = useState<Source["type"]>("postgres");
  const [error, setError] = useState("");

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);

    let config;
    try {
      config = JSON.parse(String(form.get("config")));
    } catch {
      setError("Settings must be valid JSON.");
      return;
    }

    const response = await apiFetch("/api/sources", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type, name: form.get("name"), config }),
    });
    if (!response.ok) {
      setError((await response.json()).detail ?? "Could not add the source.");
      return;
    }
    setError("");
    formElement.reset();
    onAdded();
  }

  return (
    <Card className="max-w-xl">
      <CardHeader>
        <CardTitle>Add a source</CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div className="flex gap-4">
            <div className="flex flex-col gap-2">
              <Label htmlFor="type">Type</Label>
              <select
                id="type"
                className={selectClassName}
                value={type}
                onChange={(event) => setType(event.target.value as Source["type"])}
              >
                <option value="postgres">Postgres</option>
                <option value="files">Files folder</option>
                <option value="slack_export">Slack export</option>
              </select>
            </div>
            <div className="flex flex-1 flex-col gap-2">
              <Label htmlFor="name">Name</Label>
              <Input id="name" name="name" required />
            </div>
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="config">Settings (JSON)</Label>
            {/* key resets the example when the type changes */}
            <Textarea
              key={type}
              id="config"
              name="config"
              rows={6}
              className="font-mono text-xs"
              defaultValue={JSON.stringify(EXAMPLE_CONFIG[type], null, 2)}
              required
            />
            <p className="text-xs text-muted-foreground">
              Settings are stored encrypted and are never shown again. Use a read-only database
              user.
            </p>
          </div>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <Button type="submit" className="self-start">
            Add and sync
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
