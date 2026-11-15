"use client";

import { useCallback, useEffect, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
import { formatDate } from "@/lib/catalog";

type ApiToken = { id: number; name: string; created_at: string; last_used_at: string | null };

export default function SettingsPage() {
  return (
    <AppShell>
      <Settings />
    </AppShell>
  );
}

function Settings() {
  const [tokens, setTokens] = useState<ApiToken[] | null>(null);
  const [name, setName] = useState("");
  // The token is only known right after it is made. It is gone once the page reloads.
  const [newToken, setNewToken] = useState("");
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");

  const loadTokens = useCallback(async () => {
    const response = await apiFetch("/api/tokens");
    if (response.ok) setTokens(await response.json());
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- loading data on mount
    loadTokens();
  }, [loadTokens]);

  async function createToken(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    const response = await apiFetch("/api/tokens", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    const body = await response.json().catch(() => null);
    if (!response.ok) {
      setError(typeof body?.detail === "string" ? body.detail : "That didn't work. Try again.");
      return;
    }
    setNewToken(body.token);
    setCopied(false);
    setName("");
    loadTokens();
  }

  async function revokeToken(token: ApiToken) {
    if (!window.confirm(`Revoke "${token.name}"? Anything using it stops working.`)) return;
    await apiFetch(`/api/tokens/${token.id}`, { method: "DELETE" });
    loadTokens();
  }

  async function copyToken() {
    await navigator.clipboard.writeText(newToken);
    setCopied(true);
  }

  const mcpUrl = `${window.location.origin}/mcp`;

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-xl font-semibold">Settings</h1>

      <Card>
        <CardHeader>
          <CardTitle>MCP API tokens</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4 text-sm">
          <p className="text-muted-foreground">
            A token lets Claude or another MCP client search the catalogue and run read-only
            queries as you. It sees exactly what you can see, personal data stays masked, and
            every call is written to the audit log.
          </p>

          <form onSubmit={createToken} className="flex max-w-md gap-2">
            <Input
              aria-label="Token name"
              placeholder="What is it for? e.g. Claude Desktop"
              value={name}
              maxLength={100}
              onChange={(event) => setName(event.target.value)}
            />
            <Button type="submit" disabled={!name.trim()}>
              Create token
            </Button>
          </form>
          {error && <p className="text-destructive">{error}</p>}

          {newToken && (
            <div className="flex flex-col gap-2 rounded-md border p-3">
              <p className="font-medium">Copy this token now. It won&apos;t be shown again.</p>
              <div className="flex items-center gap-2">
                <code className="min-w-0 flex-1 rounded bg-muted px-2 py-1 break-all">
                  {newToken}
                </code>
                <Button variant="outline" size="sm" onClick={copyToken}>
                  {copied ? "Copied" : "Copy"}
                </Button>
              </div>
            </div>
          )}

          {tokens === null ? (
            <p className="text-muted-foreground">Loading…</p>
          ) : tokens.length === 0 ? (
            <p className="text-muted-foreground">No tokens yet.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead>Last used</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {tokens.map((token) => (
                  <TableRow key={token.id}>
                    <TableCell className="font-medium">{token.name}</TableCell>
                    <TableCell>{formatDate(token.created_at)}</TableCell>
                    <TableCell>
                      {token.last_used_at ? formatDate(token.last_used_at) : "Never"}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button variant="outline" size="sm" onClick={() => revokeToken(token)}>
                        Revoke
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Connect an MCP client</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3 text-sm">
          <p className="text-muted-foreground">
            Point the client at this address (Streamable HTTP) and send the token as a bearer
            token.
          </p>
          <pre className="overflow-x-auto rounded bg-muted p-3 text-xs">
            {`URL:     ${mcpUrl}\nHeader:  Authorization: Bearer <your token>`}
          </pre>
          <p className="text-muted-foreground">For Claude Code:</p>
          <pre className="overflow-x-auto rounded bg-muted p-3 text-xs">
            {`claude mcp add --transport http data-fog ${mcpUrl} \\\n  --header "Authorization: Bearer <your token>"`}
          </pre>
          <p className="text-muted-foreground">
            Tools: search_catalog, get_schema, run_sql, search_knowledge, find_owner,
            list_pii_columns and propose_merge (which only creates a proposal for the Approvals
            page).
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
