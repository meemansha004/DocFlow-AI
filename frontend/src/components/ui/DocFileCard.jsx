import React from 'react';
import { FileText, Download, X } from 'lucide-react';

// A compact file card: document icon, title, a type/meta label, and action
// icons. Clicking the card body calls onOpen (in-app viewer); the download /
// remove icons are separate and stop propagation.
const DocFileCard = ({ title, meta = 'Document · MD', onOpen, onDownload, onRemove }) => (
  <div
    role={onOpen ? 'button' : undefined}
    tabIndex={onOpen ? 0 : undefined}
    onClick={onOpen}
    onKeyDown={(e) => { if (onOpen && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); onOpen(); } }}
    className={`flex items-center gap-3 rounded-lg border border-border bg-background p-3 ${
      onOpen ? 'cursor-pointer hover:border-primary/50 hover:bg-surface-hover transition-colors' : ''
    }`}
  >
    <div className="rounded-lg bg-primary/10 p-2 text-primary shrink-0">
      <FileText size={18} />
    </div>
    <div className="min-w-0 flex-1">
      <p className="text-sm font-medium text-gray-200 truncate">{title}</p>
      <p className="text-xs text-gray-500 mt-0.5">{meta}</p>
    </div>
    {onDownload && (
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); onDownload(); }}
        title="Download"
        aria-label="Download"
        className="rounded-md p-1.5 text-gray-400 hover:text-primary-light hover:bg-surface-hover transition-colors shrink-0"
      >
        <Download size={15} />
      </button>
    )}
    {onRemove && (
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); onRemove(); }}
        title="Remove"
        aria-label="Remove"
        className="rounded-md p-1.5 text-gray-500 hover:text-red-400 hover:bg-surface-hover transition-colors shrink-0"
      >
        <X size={15} />
      </button>
    )}
  </div>
);

export default DocFileCard;
