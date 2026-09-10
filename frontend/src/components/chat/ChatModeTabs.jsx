import React from 'react';
import { PenLine, ScanSearch, Database, HelpCircle } from 'lucide-react';

// Deterministic mode selection for the Chat Interface panel — clickable tabs,
// NOT LLM-judged routing (that approach was tried and dropped earlier in the
// project). The active tab decides which backend a typed message hits.
// Switching tabs remounts ChatPanel (see ProjectWorkspace `key=`), so each
// mode starts a fresh session rather than carrying context across.
const TABS = [
  { id: 'draft', label: 'Draft', icon: PenLine, hint: 'Draft a document with the AI (downloadable, not persisted)' },
  { id: 'scan', label: 'Scan', icon: ScanSearch, hint: 'Score / reform / injection-check any pasted content' },
  { id: 'rag', label: 'Search', icon: Database, hint: 'Grounded Q&A over this project’s indexed documents' },
  { id: 'query', label: 'Query', icon: HelpCircle, hint: 'Read-only metadata questions (uploader, status, versions, approvals)' },
];

const ChatModeTabs = ({ active, onChange }) => (
  <div role="tablist" aria-label="Chat mode" className="flex shrink-0 items-stretch gap-1 border-b border-border bg-surface px-2 pt-2">
    {TABS.map(({ id, label, icon: Icon, hint }) => {
      const selected = id === active;
      return (
        <button
          key={id}
          type="button"
          role="tab"
          aria-selected={selected}
          title={hint}
          onClick={() => onChange(id)}
          className={`flex items-center gap-1.5 rounded-t-lg border border-b-0 px-3 py-2 text-sm font-medium transition-colors ${
            selected
              ? 'border-border bg-background text-gray-100'
              : 'border-transparent text-gray-400 hover:text-gray-200 hover:bg-background/50'
          }`}
        >
          <Icon size={15} className={selected ? 'text-primary' : ''} />
          {label}
        </button>
      );
    })}
  </div>
);

export default ChatModeTabs;
