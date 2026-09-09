import React from 'react';
import { STAGES } from '../../constants/stages';
import StageSection from './StageSection';

const SourcePanel = ({ documents = [], canReview = false, canDelete = false, onChanged }) => {
  const groupedDocs = documents.reduce((acc, doc) => {
    const stage = doc.stage || 'Unspecified';
    if (!acc[stage]) acc[stage] = [];
    acc[stage].push(doc);
    return acc;
  }, {});

  const knownStages = STAGES.filter((stage) => groupedDocs[stage]?.length > 0);
  const extraStages = Object.keys(groupedDocs).filter((stage) => !STAGES.includes(stage));
  const activeStages = [...knownStages, ...extraStages];

  return (
    <div className="flex flex-col bg-surface border-r border-border">
      <div className="p-4 border-b border-border/50">
        <div className="flex items-center justify-between"><h2 className="text-lg font-semibold text-gray-100">Sources</h2><span className="text-[10px] uppercase tracking-widest text-primary font-bold">Library</span></div>
        <p className="text-sm text-gray-400 mt-1">Project documents and evidence</p>
      </div>
      <div className="p-4">
        {activeStages.length > 0 ? (
          activeStages.map((stage) => (
            <StageSection
              key={stage}
              stage={stage}
              documents={groupedDocs[stage]}
              canReview={canReview}
              canDelete={canDelete}
              onChanged={onChanged}
            />
          ))
        ) : (
          <div className="text-center py-8 text-gray-500 text-sm">
            No documents found for this project.
          </div>
        )}
      </div>
    </div>
  );
};

export default SourcePanel;
