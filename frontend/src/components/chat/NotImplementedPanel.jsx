import React from 'react';
import { Construction } from 'lucide-react';

// Shared "this feature isn't built yet" state — used for the Chat Interface's
// Query tab and the standalone Query Agent route. Deliberately a clear dead
// end, not a broken chat: the General Query Agent depends on the unresolved
// General Query Agent architectural fork (MERGE_DECISIONS.md §5,
// DEFERRED_ITEMS.md #16).
const NotImplementedPanel = ({
  title = 'Not implemented yet',
  children,
}) => (
  <div className="flex flex-1 min-h-0 flex-col items-center justify-center bg-background p-8 text-center">
    <div className="mb-4 rounded-2xl border border-border bg-surface p-4 text-primary/70">
      <Construction size={32} />
    </div>
    <h3 className="text-lg font-semibold text-gray-100">{title}</h3>
    <div className="mt-2 max-w-md text-sm text-gray-400">
      {children}
    </div>
  </div>
);

export default NotImplementedPanel;
