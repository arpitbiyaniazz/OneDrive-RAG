import { FileSpreadsheet, FileText, Presentation } from 'lucide-react';

/** A file icon in the colour of its type: PDF, Word, spreadsheet, slides or other. */
export function FileTypeIcon({ type, size = 16 }: { type: string; size?: number }) {
  switch ((type || '').toLowerCase()) {
    case 'pdf':
      return <FileText size={size} color="#f43f5e" aria-hidden="true" />;
    case 'docx':
    case 'doc':
    case 'gdoc':
      return <FileText size={size} color="#3b82f6" aria-hidden="true" />;
    case 'xlsx':
    case 'xls':
    case 'gsheet':
      return <FileSpreadsheet size={size} color="#10b981" aria-hidden="true" />;
    case 'pptx':
    case 'ppt':
    case 'gslides':
      return <Presentation size={size} color="#f59e0b" aria-hidden="true" />;
    default:
      return <FileText size={size} color="#a855f7" aria-hidden="true" />;
  }
}
