import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

// Maps markdown elements to the app's own styles, so agent answers look like the rest of the UI.
const components: Components = {
  p: ({ children }) => <p className="leading-relaxed">{children}</p>,
  ul: ({ children }) => <ul className="list-disc space-y-1 pl-5">{children}</ul>,
  ol: ({ children }) => <ol className="list-decimal space-y-1 pl-5">{children}</ol>,
  h1: ({ children }) => <h3 className="text-base font-semibold">{children}</h3>,
  h2: ({ children }) => <h3 className="text-base font-semibold">{children}</h3>,
  h3: ({ children }) => <h4 className="font-semibold">{children}</h4>,
  a: ({ href, children }) => (
    <a href={href} className="underline underline-offset-2" target="_blank" rel="noreferrer">
      {children}
    </a>
  ),
  // Fenced code blocks get a bordered, scrollable box; inline code gets a small chip.
  pre: ({ children }) => (
    <pre className="overflow-x-auto rounded-md border bg-muted p-3 font-mono text-xs [&>code]:bg-transparent [&>code]:p-0">
      {children}
    </pre>
  ),
  code: ({ children }) => (
    <code className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em]">{children}</code>
  ),
  table: ({ children }) => (
    <div className="max-h-80 overflow-auto rounded-md border">
      <Table>{children}</Table>
    </div>
  ),
  thead: ({ children }) => <TableHeader>{children}</TableHeader>,
  tbody: ({ children }) => <TableBody>{children}</TableBody>,
  tr: ({ children }) => <TableRow>{children}</TableRow>,
  th: ({ children }) => <TableHead>{children}</TableHead>,
  td: ({ children }) => <TableCell className="tabular-nums">{children}</TableCell>,
};

export function Markdown({ text }: { text: string }) {
  return (
    <div className="flex flex-col gap-3 text-sm">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
