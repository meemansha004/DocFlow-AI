import React from 'react';
import CollapsibleSection from '../ui/CollapsibleSection';
import DocumentItem from './DocumentItem';

const StageSection = ({ stage, documents, canReview, canDelete, onChanged }) => {
  if (!documents || documents.length === 0) return null;

  return (
    <CollapsibleSection
      title={stage}
      count={documents.length}
      badgeVariant="neutral"
      defaultExpanded={true}
      className="mb-4"
    >
      <div className="flex flex-col gap-1">
        {documents.map((doc) => (
        <DocumentItem key={doc.document_id} document={doc} canReview={canReview} canDelete={canDelete} onChanged={onChanged} />
        ))}
      </div>
    </CollapsibleSection>
  );
};

export default StageSection;
