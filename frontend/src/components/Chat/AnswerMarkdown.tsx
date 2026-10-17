import ReactMarkdown from 'react-markdown';
import { Citation } from '../../types';

const CITE_PREFIX = '#cite-';

function describe(c: Citation): string {
  const where = c.location || (c.page ? `page ${c.page}` : c.section) || '';
  const drive = c.drive_type === 'google_drive' ? 'Google Drive' : 'OneDrive';
  return [c.filename, where, drive].filter(Boolean).join(' · ');
}

/**
 * Renders an answer as Markdown, turning [n] markers into superscript links to the cited file.
 * Images are never rendered (a document could try to make the model emit tracking images).
 */
export function AnswerMarkdown({ content, citations = [] }: { content: string; citations?: Citation[] }) {
  const byId = new Map(citations.map((c) => [c.source_id, c]));
  const linked = content.replace(/\[(\d{1,3})\](?!\()/g, (marker, n) => (byId.has(Number(n)) ? `[${n}](${CITE_PREFIX}${n})` : marker));

  return (
    <ReactMarkdown
      disallowedElements={['img']}
      unwrapDisallowed
      components={{
        a: ({ href, children }) => {
          if (href?.startsWith(CITE_PREFIX)) {
            const citation = byId.get(Number(href.slice(CITE_PREFIX.length)));
            if (!citation) return <>{children}</>;
            const label = describe(citation);
            return (
              <sup className="citation-ref">
                {citation.url ? (
                  <a href={citation.url} target="_blank" rel="noopener noreferrer" title={label} aria-label={`Source: ${label}`}>
                    {children}
                  </a>
                ) : (
                  <span title={label} aria-label={`Source: ${label}`}>{children}</span>
                )}
              </sup>
            );
          }
          return (
            <a href={href} target="_blank" rel="noopener noreferrer nofollow">
              {children}
            </a>
          );
        },
      }}
    >
      {linked}
    </ReactMarkdown>
  );
}
