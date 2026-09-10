import React, { useRef } from 'react';
import { X, Upload, FileText } from 'lucide-react';
import TemplateCard from './TemplateCard';
import DocFileCard from '../ui/DocFileCard';

// The Studio panel (Part 2): a toggleable side panel — NOT a permanent column.
// Contains ONLY the document-template list plus a scratch/working template
// upload (client-side only — no persistence, no ABAC). Finalized drafts do NOT
// appear here; they live in the Chat Interface as a file card.
const StudioPanel = ({
  onClose,
  scratchTemplate,
  onScratchUpload,
  onScratchClear,
  onScratchView,
}) => {
  const fileRef = useRef(null);

  const handleFile = (e) => {
    const file = e.target.files?.[0];
    e.target.value = ''; // allow re-selecting the same file
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => onScratchUpload?.({ name: file.name, content: String(reader.result || '') });
    reader.readAsText(file);
  };

  return (
    <div className="flex h-full flex-col bg-surface">
      <div className="flex items-center justify-between p-4 border-b border-border/50 shrink-0">
        <div>
          <h2 className="text-lg font-semibold text-gray-100">Studio</h2>
          <p className="text-sm text-gray-400 mt-0.5">Document templates &amp; a working reference file.</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close Studio"
          className="rounded-md p-1.5 text-gray-400 hover:text-gray-100 hover:bg-surface-hover transition-colors"
        >
          <X size={18} />
        </button>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto p-4">
        <h3 className="text-xs font-bold text-gray-500 uppercase tracking-wider mb-3">Document templates</h3>
        <TemplateCard templateId="prd" title="PRD" description="Product Requirements Document" />
        <TemplateCard templateId="ard" title="ARD" description="Architecture Requirements Document" />
        <TemplateCard templateId="test-plan" title="Test Plan" description="Quality Assurance Test Plan" />
        <TemplateCard templateId="brd" title="Business Requirements" description="Business Requirements Document" />

        <h3 className="text-xs font-bold text-gray-500 uppercase tracking-wider mt-6 mb-3">Working template</h3>
        <input
          ref={fileRef}
          type="file"
          accept=".md,.markdown,.txt,text/markdown,text/plain"
          onChange={handleFile}
          className="hidden"
        />
        {scratchTemplate ? (
          <DocFileCard
            title={scratchTemplate.name}
            meta="Working template · not saved"
            onOpen={() => onScratchView?.(scratchTemplate)}
            onRemove={() => onScratchClear?.()}
          />
        ) : (
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            className="w-full flex flex-col items-center justify-center gap-1.5 rounded-lg border-2 border-dashed border-border bg-background/50 p-5 text-center hover:border-primary/50 transition-colors"
          >
            <Upload size={20} className="text-primary" />
            <span className="text-sm font-medium text-gray-200">Upload a template</span>
            <span className="text-xs text-gray-500">A working reference file (.md / .txt) — kept only for this session.</span>
          </button>
        )}
        <p className="mt-2 text-[11px] text-gray-600 flex items-start gap-1">
          <FileText size={12} className="mt-0.5 shrink-0" />
          Not persisted and not added to Sources — just a reference while you work.
        </p>
      </div>
    </div>
  );
};

export default StudioPanel;
