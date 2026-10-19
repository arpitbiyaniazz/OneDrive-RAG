import { memo, useMemo } from 'react';
import ReactMarkdown, { Components } from 'react-markdown';
import { Citation } from '../../types';

/** The subset of an mdast node the citation plugin touches. */
interface MdNode {
  type: string;
  value?: string;
  children?: MdNode[];
  data?: Record<string, unknown>;
}

const CITATION_MARKER = /\[(\d{1,3})\]/g;
const NO_CITATIONS: Citation[] = [];

/** Only http(s) links are rendered as links; any other scheme (or a relative URL) becomes plain text. */
function safeHttpUrl(value?: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : null;
  } catch {
    return null;
  }
}

const urlTransform = (url: string) => safeHttpUrl(url) ?? '';

function describe(c: Citation): string {
  const where = c.location || (c.page ? `page ${c.page}` : c.section) || '';
  const drive = c.drive_type === 'google_drive' ? 'Google Drive' : 'OneDrive';
  return [c.filename, where, drive].filter(Boolean).join(' · ');
}

/** Splits a text node around [n] markers of known sources; each marker becomes a <sup data-citation=n>. */
function splitCitations(node: MdNode, ids: ReadonlySet<number>): MdNode[] {
  const value = node.value ?? '';
  const out: MdNode[] = [];
  let last = 0;
  for (const match of value.matchAll(CITATION_MARKER)) {
    const n = Number(match[1]);
    if (!ids.has(n)) continue;
    const start = match.index ?? 0;
    if (start > last) out.push({ type: 'text', value: value.slice(last, start) });
    out.push({ type: 'text', value: String(n), data: { hName: 'sup', hProperties: { dataCitation: n } } });
    last = start + match[0].length;
  }
  if (out.length === 0) return [node];
  if (last < value.length) out.push({ type: 'text', value: value.slice(last) });
  return out;
}

/**
 * Remark plugin: rewrites [n] markers in text nodes only, so inline code and code blocks keep their
 * brackets, and never inside links (a link inside a link is invalid HTML).
 */
function remarkCitations(ids: ReadonlySet<number>) {
  const walk = (node: MdNode): void => {
    if (!node.children || node.type === 'link' || node.type === 'linkReference') return;
    node.children = node.children.flatMap((child) => {
      if (child.type === 'text') return splitCitations(child, ids);
      walk(child);
      return [child];
    });
  };
  return () => (tree: MdNode) => walk(tree);
}

/**
 * Renders an answer as Markdown, turning [n] markers into superscript links to the cited file.
 * Images are never rendered (a document could try to make the model emit tracking images).
 */
export const AnswerMarkdown = memo(function AnswerMarkdown({
  content,
  citations = NO_CITATIONS,
}: {
  content: string;
  citations?: Citation[];
}) {
  const { remarkPlugins, components } = useMemo(() => {
    const byId = new Map(citations.map((c) => [c.source_id, c]));
    const rendered: Components = {
      sup: ({ node, children }) => {
        const citation = byId.get(Number(node?.properties?.dataCitation));
        if (!citation) return <sup>{children}</sup>;
        const label = describe(citation);
        const href = safeHttpUrl(citation.url);
        return (
          <sup className="citation-ref">
            {href ? (
              <a href={href} target="_blank" rel="noopener noreferrer" title={label} aria-label={`Source: ${label}`}>
                {children}
              </a>
            ) : (
              <span title={label} aria-label={`Source: ${label}`}>
                {children}
              </span>
            )}
          </sup>
        );
      },
      a: ({ href, children }) => {
        const safe = safeHttpUrl(href);
        if (!safe) return <>{children}</>;
        return (
          <a href={safe} target="_blank" rel="noopener noreferrer nofollow">
            {children}
          </a>
        );
      },
    };
    return { remarkPlugins: [remarkCitations(new Set(byId.keys()))], components: rendered };
  }, [citations]);

  return (
    <ReactMarkdown
      remarkPlugins={remarkPlugins}
      components={components}
      urlTransform={urlTransform}
      disallowedElements={['img']}
      unwrapDisallowed
    >
      {content}
    </ReactMarkdown>
  );
});
