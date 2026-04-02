import { Fragment, ReactNode } from "react";

function renderInline(text: string): ReactNode[] {
  const parts: ReactNode[] = [];
  const pattern = /(\*\*[^*]+\*\*|\*[^*\n]+\*|_[^_\n]+_|`[^`]+`|\[([^\]]+)\]\(([^)]+)\))/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let key = 0;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }

    const token = match[0];
    if (token.startsWith("**") && token.endsWith("**")) {
      parts.push(<strong key={`strong-${key++}`}>{token.slice(2, -2)}</strong>);
    } else if (
      ((token.startsWith("*") && token.endsWith("*")) || (token.startsWith("_") && token.endsWith("_"))) &&
      token.length >= 3
    ) {
      parts.push(<em key={`em-${key++}`}>{token.slice(1, -1)}</em>);
    } else if (token.startsWith("`") && token.endsWith("`")) {
      parts.push(<code key={`code-${key++}`}>{token.slice(1, -1)}</code>);
    } else if (token.startsWith("[")) {
      const linkText = match[2];
      const linkUrl = match[3];
      parts.push(
        <a key={`link-${key++}`} href={linkUrl} target="_blank" rel="noopener noreferrer">
          {linkText}
        </a>,
      );
    } else {
      parts.push(token);
    }

    lastIndex = match.index + token.length;
  }

  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }

  return parts;
}

function renderParagraph(text: string, keyPrefix: string) {
  const lines = text.split("\n");
  return (
    <p key={keyPrefix}>
      {lines.map((line, index) => (
        <Fragment key={`${keyPrefix}-${index}`}>
          {index > 0 ? <br /> : null}
          {renderInline(line)}
        </Fragment>
      ))}
    </p>
  );
}

function renderList(lines: string[], keyPrefix: string) {
  const ordered = lines.every((line) => /^\d+\.\s+/.test(line));
  const ListTag = ordered ? "ol" : "ul";
  return (
    <ListTag key={keyPrefix}>
      {lines.map((line, index) => {
        const content = ordered ? line.replace(/^\d+\.\s+/, "") : line.replace(/^[-*]\s+/, "");
        return <li key={`${keyPrefix}-${index}`}>{renderInline(content)}</li>;
      })}
    </ListTag>
  );
}

export function renderMessageContent(text: string) {
  const blocks = text.split(/```/);
  const rendered: ReactNode[] = [];

  blocks.forEach((block, blockIndex) => {
    const isCodeBlock = blockIndex % 2 === 1;
    const trimmed = block.replace(/^\n+|\n+$/g, "");
    if (!trimmed) {
      return;
    }

    if (isCodeBlock) {
      const lines = trimmed.split("\n");
      const firstLine = lines[0]?.trim() || "";
      const looksLikeLanguage = /^[a-zA-Z0-9#+._-]{1,24}$/.test(firstLine);
      const code = looksLikeLanguage ? lines.slice(1).join("\n") : trimmed;
      rendered.push(
        <pre key={`code-block-${blockIndex}`}>
          <code>{code}</code>
        </pre>,
      );
      return;
    }

    const paragraphs = trimmed.split(/\n\s*\n/);
    paragraphs.forEach((paragraph, paragraphIndex) => {
      const lines = paragraph
        .split("\n")
        .map((line) => line.trimEnd())
        .filter(Boolean);
      if (!lines.length) {
        return;
      }

      const keyPrefix = `block-${blockIndex}-${paragraphIndex}`;

      // Horizontal rule
      if (lines.length === 1 && /^[-*_]{3,}$/.test(lines[0])) {
        rendered.push(<hr key={keyPrefix} />);
        return;
      }

      // Headings (single-line paragraph starting with #)
      if (lines.length === 1) {
        const headingMatch = lines[0].match(/^(#{1,3})\s+(.+)$/);
        if (headingMatch) {
          const level = headingMatch[1].length;
          const headingText = headingMatch[2];
          const Tag = (["h2", "h3", "h4"] as const)[level - 1];
          rendered.push(<Tag key={keyPrefix}>{renderInline(headingText)}</Tag>);
          return;
        }
      }

      // Blockquote (all lines start with "> ")
      if (lines.every((line) => /^>\s?/.test(line))) {
        const quoteContent = lines.map((line) => line.replace(/^>\s?/, "")).join("\n");
        rendered.push(
          <blockquote key={keyPrefix}>
            <p>{renderInline(quoteContent)}</p>
          </blockquote>,
        );
        return;
      }

      // List
      const isList = lines.every((line) => /^[-*]\s+/.test(line) || /^\d+\.\s+/.test(line));
      if (isList) {
        rendered.push(renderList(lines, keyPrefix));
        return;
      }

      rendered.push(renderParagraph(lines.join("\n"), keyPrefix));
    });
  });

  return rendered;
}
