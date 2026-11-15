"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { Markdown } from "@/components/markdown";
import { Button } from "@/components/ui/button";
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

type Source = { id: number; name: string; path: string };
type ResultTable = { columns: string[]; rows: unknown[][] };
type Turn = {
  role: "user" | "assistant";
  text: string;
  sources?: Source[];
  table?: ResultTable | null;
};
type ConversationSummary = { id: number; title: string; updated_at: string };

// Names the agent's tools in words, for the "working on it" line.
const TOOL_LABELS: Record<string, string> = {
  search_catalog: "Searching the catalogue",
  get_schema: "Reading columns",
  run_sql: "Running a query",
  search_knowledge: "Reading team notes",
  find_owner: "Looking up the owner",
  list_pii_columns: "Listing personal data columns",
};

export default function ChatPage() {
  return (
    <AppShell>
      <Chat />
    </AppShell>
  );
}

function Chat() {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [message, setMessage] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  async function loadConversations() {
    const response = await apiFetch("/api/chat/conversations");
    if (response.ok) setConversations(await response.json());
  }

  useEffect(() => {
    apiFetch("/api/chat/conversations").then(async (response) => {
      if (response.ok) setConversations(await response.json());
    });
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [turns]);

  async function openConversation(id: number) {
    const response = await apiFetch(`/api/chat/conversations/${id}`);
    if (!response.ok) return;
    setConversationId(id);
    setTurns((await response.json()).turns);
    setError(null);
  }

  function startNewConversation() {
    setConversationId(null);
    setTurns([]);
    setError(null);
  }

  // Changes the assistant turn that is still being written, which is always the last one.
  function updateAnswer(change: (answer: Turn) => Turn) {
    setTurns((current) => [...current.slice(0, -1), change(current[current.length - 1])]);
  }

  function handleEvent(event: Record<string, unknown>) {
    if (event.type === "conversation") setConversationId(event.id as number);
    if (event.type === "text") {
      setStatus(null);
      updateAnswer((answer) => ({ ...answer, text: answer.text + event.text }));
    }
    if (event.type === "tool") setStatus(TOOL_LABELS[event.name as string] ?? "Working");
    if (event.type === "done") {
      updateAnswer((answer) => ({
        ...answer,
        sources: event.sources as Source[],
        table: event.table as ResultTable | null,
      }));
    }
    if (event.type === "error") setError(event.message as string);
  }

  async function sendMessage(event: React.FormEvent) {
    event.preventDefault();
    const question = message.trim();
    if (!question || status) return;

    setMessage("");
    setError(null);
    setStatus("Thinking");
    setTurns((current) => [
      ...current,
      { role: "user", text: question },
      { role: "assistant", text: "" },
    ]);

    const response = await apiFetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: question, conversation_id: conversationId }),
    });
    if (!response.ok || !response.body) {
      const body = await response.json().catch(() => null);
      setError(body?.detail ?? "The chat service is unavailable.");
      setStatus(null);
      return;
    }

    // Server-sent events: each event is a "data: {json}" line followed by a blank line.
    const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += value;
      const events = buffer.split("\n\n");
      buffer = events.pop()!;
      for (const raw of events) {
        if (raw.startsWith("data: ")) handleEvent(JSON.parse(raw.slice(6)));
      }
    }
    setStatus(null);
    loadConversations();
  }

  return (
    <div className="flex gap-6">
      <aside className="hidden w-56 shrink-0 flex-col gap-2 md:flex">
        <Button variant="outline" onClick={startNewConversation}>
          New chat
        </Button>
        {conversations.map((conversation) => (
          <button
            key={conversation.id}
            onClick={() => openConversation(conversation.id)}
            className={`truncate rounded-md px-2 py-1 text-left text-sm hover:bg-muted ${
              conversation.id === conversationId ? "bg-muted font-medium" : ""
            }`}
          >
            {conversation.title}
          </button>
        ))}
      </aside>

      <div className="flex min-w-0 flex-1 flex-col gap-4">
        {turns.length === 0 && (
          <p className="text-sm text-muted-foreground">
            Ask about your company&apos;s data, for example &quot;Where is the latest orders
            data?&quot; or &quot;What was net revenue in March 2026?&quot;
          </p>
        )}

        {turns.map((turn, index) =>
          turn.role === "user" ? (
            <div key={index} className="self-end rounded-lg bg-muted px-3 py-2 text-sm">
              {turn.text}
            </div>
          ) : (
            <AnswerView key={index} turn={turn} />
          ),
        )}

        {status && <p className="text-sm text-muted-foreground">{status}…</p>}
        {error && <p className="text-sm text-destructive">{error}</p>}
        <div ref={bottomRef} />

        <form onSubmit={sendMessage} className="flex gap-2">
          <Textarea
            aria-label="Message"
            placeholder="Ask a question"
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) sendMessage(event);
            }}
            rows={2}
          />
          <Button type="submit" disabled={!!status || !message.trim()}>
            Send
          </Button>
        </form>
      </div>
    </div>
  );
}

function AnswerView({ turn }: { turn: Turn }) {
  return (
    <div className="flex flex-col gap-3">
      <Markdown text={turn.text} />
      {turn.table && <ResultChart table={turn.table} />}
      {turn.table && turn.table.rows.length > 0 && <ResultView table={turn.table} />}
      {turn.sources && turn.sources.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {turn.sources.map((source) => (
            <Link
              key={source.id}
              href={`/catalog/${source.id}`}
              title={source.path}
              className="rounded-full border px-2 py-0.5 text-xs hover:bg-muted"
            >
              {source.name}
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

function isNumber(value: unknown): boolean {
  // Postgres numerics arrive as strings like "241626.00".
  return typeof value === "number" || (typeof value === "string" && /^-?\d+(\.\d+)?$/.test(value));
}

/** The label and value column to chart, or null when the result isn't a simple "name, number" list. */
function chartColumns(table: ResultTable): { label: number; value: number } | null {
  if (table.rows.length < 2 || table.rows.length > 30) return null;
  const numeric = table.columns.map((_, column) =>
    table.rows.every((row) => row[column] !== null && isNumber(row[column])),
  );
  const label = numeric.indexOf(false);
  const value = numeric.indexOf(true);
  if (label === -1 || value === -1) return null;
  // Bars start at zero, so a negative value has no honest bar.
  if (table.rows.some((row) => Number(row[value]) < 0)) return null;
  return { label, value };
}

/** A plain bar per row, for results like "revenue by month". The table below has every column. */
function ResultChart({ table }: { table: ResultTable }) {
  const columns = chartColumns(table);
  if (!columns) return null;
  const values = table.rows.map((row) => Number(row[columns.value]));
  const largest = Math.max(...values, 1);

  return (
    <figure className="flex flex-col gap-2 rounded-md border p-3">
      <figcaption className="text-xs text-muted-foreground">
        {table.columns[columns.value]} by {table.columns[columns.label]}
      </figcaption>
      <div className="grid grid-cols-[minmax(0,10rem)_1fr_auto] items-center gap-x-3 gap-y-1.5 text-xs">
        {table.rows.map((row, index) => {
          const label = String(row[columns.label] ?? "—");
          const value = values[index].toLocaleString("en-IN");
          return (
            <div key={index} className="contents" title={`${label}: ${value}`}>
              <span className="truncate">{label}</span>
              <div className="h-2">
                <div
                  className="h-full min-w-px rounded-r bg-chart-2"
                  style={{ width: `${(values[index] / largest) * 100}%` }}
                />
              </div>
              <span className="text-right tabular-nums">{value}</span>
            </div>
          );
        })}
      </div>
    </figure>
  );
}

function ResultView({ table }: { table: ResultTable }) {
  return (
    <div className="max-h-80 overflow-auto rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            {table.columns.map((column) => (
              <TableHead key={column}>{column}</TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {table.rows.map((row, rowIndex) => (
            <TableRow key={rowIndex}>
              {row.map((value, columnIndex) => (
                <TableCell key={columnIndex} className="tabular-nums">
                  {value === null ? "—" : String(value)}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
