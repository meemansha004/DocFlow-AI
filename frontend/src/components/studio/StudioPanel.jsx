import React from 'react';
import { Bot, ScanSearch, SearchCheck, Database, MessageSquare } from 'lucide-react';
import { Link } from 'react-router-dom';
import TemplateCard from './TemplateCard';
import UploadDocumentCard from './UploadDocumentCard';

const StudioPanel = ({ projectId, onAnalyzeGaps }) => {
  return (
    <div className="flex flex-col bg-surface lg:border-l border-border">
      <div className="p-4 border-b border-border/50">
        <div className="flex items-center justify-between"><h2 className="text-lg font-semibold text-gray-100">Studio</h2><span className="text-[10px] uppercase tracking-widest text-accent-light font-bold">Create</span></div>
        <p className="text-sm text-gray-400 mt-1">Create project documents using AI-powered templates.</p>
      </div>
      <div className="p-4">
        <div className="mb-6">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-xs font-bold text-gray-500 uppercase tracking-wider">AI agents</h3>
            <span className="text-[10px] uppercase tracking-widest text-primary font-bold">Ready</span>
          </div>
          <div className="space-y-2">
            <Link to={`/projects/${projectId}/studio/prd`} className="flex items-center gap-3 rounded-lg border border-border bg-background p-3 hover:border-primary/50 hover:bg-surface-hover transition-colors">
              <span className="w-8 h-8 rounded-lg bg-primary/10 text-primary flex items-center justify-center"><Bot size={16} /></span>
              <span className="min-w-0"><span className="block text-sm font-medium text-gray-200">Drafting Agent</span><span className="block text-xs text-gray-500 truncate">Turn instructions into structured documents</span></span>
            </Link>
            <Link to={`/projects/${projectId}/studio/scan`} className="flex items-center gap-3 rounded-lg border border-border bg-background p-3 hover:border-primary/50 hover:bg-surface-hover transition-colors">
              <span className="w-8 h-8 rounded-lg bg-accent/10 text-accent-light flex items-center justify-center"><ScanSearch size={16} /></span>
              <span className="min-w-0"><span className="block text-sm font-medium text-gray-200">Scanner Agent</span><span className="block text-xs text-gray-500 truncate">Score, revise, and improve a generated draft</span></span>
            </Link>
            <button type="button" onClick={onAnalyzeGaps} className="w-full flex items-center gap-3 rounded-lg border border-border bg-background p-3 text-left hover:border-primary/50 hover:bg-surface-hover transition-colors">
              <span className="w-8 h-8 rounded-lg bg-amber-500/10 text-amber-400 flex items-center justify-center"><SearchCheck size={16} /></span>
              <span className="min-w-0"><span className="block text-sm font-medium text-gray-200">Gap Detection Agent</span><span className="block text-xs text-gray-500 truncate">Find missing coverage across SDLC stages</span></span>
            </button>
            <Link to={`/studio/query?agent=rag&project_id=${encodeURIComponent(projectId)}`} className="flex items-center gap-3 rounded-lg border border-border bg-background p-3 hover:border-primary/50 hover:bg-surface-hover transition-colors">
              <span className="w-8 h-8 rounded-lg bg-cyan-500/10 text-cyan-300 flex items-center justify-center"><Database size={16} /></span>
              <span className="min-w-0"><span className="block text-sm font-medium text-gray-200">RAG Retrieval Agent</span><span className="block text-xs text-gray-500 truncate">Search authorized source chunks</span></span>
            </Link>
            <Link to={`/studio/query?project_id=${encodeURIComponent(projectId)}`} className="flex items-center gap-3 rounded-lg border border-border bg-background p-3 hover:border-primary/50 hover:bg-surface-hover transition-colors">
              <span className="w-8 h-8 rounded-lg bg-indigo-500/10 text-indigo-300 flex items-center justify-center"><MessageSquare size={16} /></span>
              <span className="min-w-0"><span className="block text-sm font-medium text-gray-200">General Query Agent</span><span className="block text-xs text-gray-500 truncate">Ask grounded questions about this project</span></span>
            </Link>
          </div>
        </div>
        <div className="mb-6">
          <h3 className="text-xs font-bold text-gray-500 uppercase tracking-wider mb-3">Document templates</h3>
          <TemplateCard 
            templateId="prd" 
            title="PRD" 
            description="Product Requirements Document" 
          />
          <TemplateCard 
            templateId="ard" 
            title="ARD" 
            description="Architecture Requirements Document" 
          />
          <TemplateCard 
            templateId="test-plan" 
            title="Test Plan" 
            description="Quality Assurance Test Plan" 
          />
          <TemplateCard 
            templateId="brd" 
            title="Business Requirements" 
            description="Business Requirements Document" 
          />
        </div>
        
        <UploadDocumentCard />
      </div>
    </div>
  );
};

export default StudioPanel;
