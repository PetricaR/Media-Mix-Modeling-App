import React from "react";
import { Box, Typography } from "@mui/material";
import { FONT_MONO, PALETTE } from "../../theme";

/**
 * A small, safe Markdown renderer for the narrative the backend writes (headings, paragraphs, bullet and numbered
 * lists, fenced code, **bold**, *italic* and `code`). It builds React elements, never HTML strings.
 */

function renderInline(text: string, keyPrefix: string): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*)/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let i = 0;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) out.push(text.slice(last, match.index));
    const token = match[0];
    const key = `${keyPrefix}-${i++}`;
    if (token.startsWith("**")) {
      out.push(<strong key={key}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith("`")) {
      out.push(
        <Box
          key={key}
          component="code"
          sx={{ fontFamily: FONT_MONO, fontSize: "0.86em", px: 0.5, py: 0.1, borderRadius: 0.75, backgroundColor: PALETTE.bgSubtle }}
        >
          {token.slice(1, -1)}
        </Box>
      );
    } else {
      out.push(<em key={key}>{token.slice(1, -1)}</em>);
    }
    last = match.index + token.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

type Block =
  | { kind: "heading"; level: number; text: string }
  | { kind: "para"; text: string }
  | { kind: "ul" | "ol"; items: string[] }
  | { kind: "code"; text: string };

function parse(source: string): Block[] {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const blocks: Block[] = [];
  let para: string[] = [];
  let list: { kind: "ul" | "ol"; items: string[] } | null = null;
  const flush = () => {
    if (para.length) blocks.push({ kind: "para", text: para.join("\n") });
    para = [];
    if (list) blocks.push(list);
    list = null;
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.trim().startsWith("```")) {
      flush();
      const code: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith("```")) code.push(lines[i++]);
      blocks.push({ kind: "code", text: code.join("\n") });
      continue;
    }
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    const bullet = /^\s*[-•]\s+(.*)$/.exec(line);
    const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (heading) {
      flush();
      blocks.push({ kind: "heading", level: heading[1].length, text: heading[2] });
    } else if (bullet || numbered) {
      const kind = bullet ? "ul" : "ol";
      if (para.length) {
        blocks.push({ kind: "para", text: para.join("\n") });
        para = [];
      }
      if (!list || list.kind !== kind) {
        if (list) blocks.push(list);
        list = { kind, items: [] };
      }
      list.items.push((bullet ?? numbered)![1]);
    } else if (!line.trim()) {
      flush();
    } else if (list && /^\s{2,}\S/.test(line)) {
      list.items[list.items.length - 1] += ` ${line.trim()}`;
    } else {
      if (list) {
        blocks.push(list);
        list = null;
      }
      para.push(line);
    }
  }
  flush();
  return blocks;
}

export const Markdown: React.FC<{ source: string; dense?: boolean; color?: string }> = ({ source, dense, color }) => {
  const blocks = React.useMemo(() => parse(source || ""), [source]);
  const gap = dense ? 0.75 : 1.25;
  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap, color: color ?? PALETTE.inkPrimary, "& strong": { fontWeight: 700 } }}>
      {blocks.map((b, idx) => {
        const key = `md-${idx}`;
        if (b.kind === "heading") {
          return (
            <Typography key={key} variant={b.level <= 2 ? "h2" : "h3"} component={b.level <= 2 ? "h2" : "h3"} sx={{ mt: idx ? 1 : 0 }}>
              {renderInline(b.text, key)}
            </Typography>
          );
        }
        if (b.kind === "code") {
          return (
            <Box
              key={key}
              component="pre"
              sx={{
                m: 0,
                p: 1.5,
                overflowX: "auto",
                borderRadius: 1.5,
                backgroundColor: PALETTE.bgSubtle,
                border: `1px solid ${PALETTE.borderSubtle}`,
                fontFamily: FONT_MONO,
                fontSize: "0.8rem",
                lineHeight: 1.55,
              }}
            >
              {b.text}
            </Box>
          );
        }
        if ("items" in b) {
          return (
            <Box key={key} component={b.kind} sx={{ m: 0, pl: 2.75, display: "flex", flexDirection: "column", gap: 0.5 }}>
              {b.items.map((item, j) => (
                <Typography key={`${key}-${j}`} component="li" variant={dense ? "body2" : "body1"} sx={{ color: "inherit" }}>
                  {renderInline(item, `${key}-${j}`)}
                </Typography>
              ))}
            </Box>
          );
        }
        return (
          <Typography key={key} variant={dense ? "body2" : "body1"} sx={{ whiteSpace: "pre-line", color: "inherit" }}>
            {renderInline(b.text, key)}
          </Typography>
        );
      })}
    </Box>
  );
};
