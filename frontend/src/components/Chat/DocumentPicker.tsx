import { useEffect } from 'react';
import { RefreshCw, X } from 'lucide-react';
import { DocumentRecord, SelectedDocument } from '../../types';
import { FileTypeIcon } from '../FileTypeIcon';

export const PICKER_ID = 'document-picker';
export const optionId = (index: number) => `${PICKER_ID}-option-${index}`;
/** Matches listed at once; typing more of the name narrows the list. */
export const PICKER_SHOWN = 50;

const DRIVE_NAMES: Record<string, string> = { onedrive: 'OneDrive', google_drive: 'Google Drive' };

/**
 * The "/name" typed right before the caret, or null. A "/" at the start of the input or after a space
 * opens the document picker; "and/or" or "1/2" do not.
 */
export function slashCommand(text: string, caret: number): { start: number; filter: string } | null {
  const before = text.slice(0, caret);
  const start = before.lastIndexOf('/');
  if (start < 0 || (start > 0 && !/\s/.test(before[start - 1]))) return null;
  const filter = before.slice(start + 1);
  return filter.length <= 80 && !filter.includes('\n') ? { start, filter } : null;
}

/**
 * Documents whose name or folder contains every typed word, leaving out those already selected. Names that
 * start with the text come first, then names with a word that starts with it; otherwise the newest first.
 */
export function matchDocuments(documents: DocumentRecord[], filter: string, exclude: Set<string>): DocumentRecord[] {
  const words = filter.toLowerCase().split(/\s+/).filter(Boolean);
  const first = words[0] ?? '';
  const ranked: { doc: DocumentRecord; rank: number; order: number }[] = [];
  documents.forEach((doc, order) => {
    if (exclude.has(doc.id)) return;
    const name = doc.filename.toLowerCase();
    const text = `${name} ${(doc.folder_path || '').toLowerCase()}`;
    if (!words.every((word) => text.includes(word))) return;
    const rank = !first || name.startsWith(first) ? 0 : name.split(/[\s_.()-]+/).some((w) => w.startsWith(first)) ? 1 : 2;
    ranked.push({ doc, rank, order });
  });
  return ranked.sort((a, b) => a.rank - b.rank || a.order - b.order).map((r) => r.doc);
}

/** The name with the typed text in bold. */
function Highlighted({ text, filter }: { text: string; filter: string }) {
  const word = filter.trim().toLowerCase().split(/\s+/)[0];
  const at = word ? text.toLowerCase().indexOf(word) : -1;
  if (at < 0) return <>{text}</>;
  return (
    <>
      {text.slice(0, at)}
      <strong>{text.slice(at, at + word.length)}</strong>
      {text.slice(at + word.length)}
    </>
  );
}

interface DocumentPickerProps {
  /** Matching documents, best first (only the first PICKER_SHOWN are listed). */
  matches: DocumentRecord[];
  activeIndex: number;
  filter: string;
  state: 'idle' | 'loading' | 'error' | 'ready';
  /** Number of indexed documents, to tell "nothing indexed yet" from "no match". */
  total: number;
  /** The maximum number of documents is already selected. */
  full: boolean;
  maxDocuments: number;
  onPick: (doc: DocumentRecord) => void;
  onHover: (index: number) => void;
  onRetry: () => void;
}

/** The list that opens above the chat input when the user types "/". */
export function DocumentPicker({
  matches,
  activeIndex,
  filter,
  state,
  total,
  full,
  maxDocuments,
  onPick,
  onHover,
  onRetry,
}: DocumentPickerProps) {
  const shown = matches.slice(0, PICKER_SHOWN);

  useEffect(() => {
    document.getElementById(optionId(activeIndex))?.scrollIntoView({ block: 'nearest' });
  }, [activeIndex]);

  let note: string | null = null;
  if (full) note = `You can select up to ${maxDocuments} documents. Remove one to add another.`;
  else if (state === 'error') note = 'Could not load your documents.';
  else if (state !== 'ready' && total === 0) note = 'Loading your documents...';
  else if (total === 0) note = 'No indexed documents yet. Add some with Manage documents.';
  else if (shown.length === 0) note = filter.trim() ? `No document matches "${filter.trim()}".` : 'Every document is already selected.';

  return (
    // Clicks inside the list must not take the focus (and the caret) away from the input.
    <div className="doc-picker" onMouseDown={(e) => e.preventDefault()}>
      <div className="doc-picker-header">
        <span>Answer from specific documents</span>
        <span className="doc-picker-keys" aria-hidden="true">
          ↑↓ move · Enter select · Esc close
        </span>
      </div>
      {note ? (
        <div className="doc-picker-note" role="status">
          <span>{note}</span>
          {state === 'error' && !full && (
            <button type="button" className="doc-picker-retry" onClick={onRetry}>
              <RefreshCw size={12} aria-hidden="true" /> Try again
            </button>
          )}
        </div>
      ) : (
        <ul id={PICKER_ID} role="listbox" aria-label="Documents" className="doc-picker-list">
          {shown.map((doc, index) => (
            <li
              key={doc.id}
              id={optionId(index)}
              role="option"
              aria-selected={index === activeIndex}
              className="doc-option"
              onClick={() => onPick(doc)}
              onMouseMove={() => index !== activeIndex && onHover(index)}
            >
              <FileTypeIcon type={doc.file_type} size={15} />
              <span className="doc-option-text">
                <span className="doc-option-name">
                  <Highlighted text={doc.filename} filter={filter} />
                </span>
                <span className="doc-option-place">
                  {[doc.folder_path && doc.folder_path !== '/' ? doc.folder_path : null, doc.drive_type ? DRIVE_NAMES[doc.drive_type] : null]
                    .filter(Boolean)
                    .join(' · ')}
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}
      {!note && matches.length > shown.length && (
        <div className="doc-picker-more">{matches.length - shown.length} more: keep typing the name to narrow the list.</div>
      )}
      <span className="sr-only" role="status" aria-live="polite">
        {note ? '' : `${matches.length} document${matches.length === 1 ? '' : 's'} found`}
      </span>
    </div>
  );
}

/** A selected document: above the input (removable) and on the question it was sent with. */
export function DocumentChip({ doc, onRemove }: { doc: SelectedDocument; onRemove?: () => void }) {
  return (
    <span className="doc-chip" title={doc.filename}>
      <FileTypeIcon type={doc.file_type} size={13} />
      <span className="doc-chip-name">{doc.filename}</span>
      {onRemove && (
        <button type="button" onClick={onRemove} aria-label={`Remove ${doc.filename}`} title="Remove">
          <X size={12} aria-hidden="true" />
        </button>
      )}
    </span>
  );
}
