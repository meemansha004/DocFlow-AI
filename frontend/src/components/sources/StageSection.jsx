import React, { useState } from 'react';
import { ChevronDown, ShieldCheck } from 'lucide-react';
import Badge from '../ui/Badge';
import DocumentItem from './DocumentItem';

// One stage grouping in the Sources panel. Renders even when it has no
// documents (so newly created / empty stages are visible and manageable).
const StageSection = ({
  stage,
  documents = [],
  canReview,
  canDelete,
  onChanged,
  requiresApproval = false,
  menu = null, // optional <KebabMenu /> element rendered in the header
  defaultExpanded = true,
}) => {
  const [expanded, setExpanded] = useState(defaultExpanded);

  return (
    <div className="border border-border rounded-xl bg-surface mb-4">
      <div className="flex items-center justify-between pr-2 bg-surface hover:bg-surface-hover transition-colors rounded-t-xl">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="flex flex-1 items-center gap-3 p-4 min-w-0"
        >
          <ChevronDown
            size={18}
            className={`text-gray-400 transition-transform duration-200 shrink-0 ${expanded ? 'rotate-180' : ''}`}
          />
          <h3 className="font-medium text-gray-100 truncate">{stage}</h3>
          <Badge variant="neutral">{documents.length}</Badge>
          {requiresApproval && (
            <span className="inline-flex items-center gap-1 text-[11px] text-amber-400" title="Documents in this stage need approval">
              <ShieldCheck size={12} /> approval
            </span>
          )}
        </button>
        {menu && <div className="shrink-0">{menu}</div>}
      </div>

      <div className={`grid transition-all duration-200 ease-in-out ${expanded ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0'}`}>
        <div className="overflow-hidden">
          <div className="p-4 border-t border-border/50 bg-background/30">
            {documents.length > 0 ? (
              <div className="flex flex-col gap-1">
                {documents.map((doc) => (
                  <DocumentItem
                    key={doc.document_id}
                    document={doc}
                    canReview={canReview}
                    canDelete={canDelete}
                    onChanged={onChanged}
                  />
                ))}
              </div>
            ) : (
              <p className="text-xs text-gray-600 py-1">No documents in this stage yet.</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default StageSection;
