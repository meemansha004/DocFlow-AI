import React, { useEffect, useState } from 'react';
import { ArrowLeft, Check, ScanSearch, Sparkles } from 'lucide-react';
import { Link, useParams } from 'react-router-dom';
import Button from '../ui/Button';
import Textarea from '../ui/Textarea';
import Input from '../ui/Input';
import Dropdown from '../ui/Dropdown';
import Badge from '../ui/Badge';
import { projectsApi, studioApi } from '../../lib/api';
import { STAGES } from '../../constants/stages';

const stageOptions = STAGES.map((stage) => ({ label: stage, value: stage }));

const ScannerInterface = () => {
  const { projectId } = useParams();
  const [documentType, setDocumentType] = useState('PRD');
  const [stage, setStage] = useState('Requirements');
  const [documentText, setDocumentText] = useState('');
  const [result, setResult] = useState(null);
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState('');
  const [documents, setDocuments] = useState([]);
  const [selectedDocument, setSelectedDocument] = useState('');
  const [uploading, setUploading] = useState(false);

  useEffect(() => {
    if (!projectId) return;
    projectsApi.documents(projectId).then(setDocuments).catch((err) => setError(err.message || 'Could not load project documents.'));
  }, [projectId]);

  const handleScan = async () => {
    if (!documentText.trim()) return;
    setScanning(true);
    setError('');
    try {
      setResult(await studioApi.scanDraft({ document_text: documentText, document_type: documentType, project_stage: stage }));
    } catch (err) {
      setError(err.message || 'Could not scan this document.');
    } finally {
      setScanning(false);
    }
  };

  const handleUpload = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    if (file.size > 10 * 1024 * 1024) {
      setError('Documents must be 10 MB or smaller.');
      return;
    }
    const formData = new FormData();
    formData.append('file', file);
    formData.append('document_type', documentType);
    formData.append('project_stage', stage);
    setUploading(true);
    setError('');
    try {
      setResult(await studioApi.scanUpload(formData));
      setDocumentText(`[Uploaded document: ${file.name}]`);
    } catch (err) {
      setError(err.message || 'Could not upload this document.');
    } finally {
      setUploading(false);
      event.target.value = '';
    }
  };

  const handleProjectDocument = async () => {
    if (!selectedDocument || !projectId) return;
    setScanning(true);
    setError('');
    try {
      setResult(await studioApi.scanProjectDocument(projectId, selectedDocument));
    } catch (err) {
      setError(err.message || 'Could not scan the project document.');
    } finally {
      setScanning(false);
    }
  };

  return (
    <div className="flex-1 flex flex-col bg-background">
      <div className="h-14 border-b border-border bg-background px-6 flex items-center">
        <Link to={projectId ? `/projects/${projectId}` : '/studio'} className="text-gray-400 hover:text-gray-200 flex items-center gap-1 text-sm">
          <ArrowLeft size={16} /> {projectId ? 'Back to Project' : 'Back to Studio'}
        </Link>
      </div>
      <div className="max-w-4xl mx-auto w-full p-8">
        <div className="mb-8">
          <div className="flex items-center gap-2 text-primary text-xs font-bold uppercase tracking-widest mb-3"><ScanSearch size={15} /> Scanner Agent</div>
          <h1 className="text-3xl font-bold text-gray-100">Review document quality</h1>
          <p className="text-gray-400 mt-2">Score structure, completeness, and labeling, then apply a suggested revision.</p>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-5 mb-5">
          <Input label="Document type" value={documentType} onChange={(event) => setDocumentType(event.target.value)} placeholder="PRD" />
          <Dropdown label="Project stage" options={stageOptions} value={stage} onChange={setStage} />
        </div>
        <Textarea label="Document content" required value={documentText} onChange={(event) => setDocumentText(event.target.value)} placeholder="Paste the draft you want the Scanner Agent to review..." className="min-h-[300px]" />
        <div className="flex flex-wrap items-center gap-3 mt-4">
          <label className="inline-flex items-center justify-center rounded-md border border-border px-4 py-2 text-sm text-gray-200 cursor-pointer hover:border-primary/50">
            {uploading ? 'Uploading...' : 'Upload document'}
            <input type="file" className="hidden" onChange={handleUpload} accept=".txt,.md,.json,.pdf,.doc,.docx,.ppt,.pptx" disabled={uploading} />
          </label>
          <Button icon={ScanSearch} onClick={handleScan} disabled={!documentText.trim()} loading={scanning}>Scan document</Button>
        </div>
        {projectId && documents.length > 0 && (
          <div className="mt-5 rounded-lg border border-border bg-surface p-4">
            <Dropdown label="Scan a document already in this project" options={documents.map((document) => ({ label: `${document.filename} · ${document.stage}`, value: document.document_id }))} value={selectedDocument} onChange={setSelectedDocument} placeholder="Select project document" />
            <Button className="mt-3" size="sm" variant="secondary" onClick={handleProjectDocument} disabled={!selectedDocument} loading={scanning}>Scan selected project document</Button>
          </div>
        )}
        {error && <p className="text-red-400 text-sm mt-3">{error}</p>}
        {result && (
          <div className="mt-8 border border-border rounded-xl bg-surface p-5 space-y-4">
            <div className="flex items-center justify-between"><h2 className="text-lg font-semibold text-gray-100">Scanner result</h2><Badge variant={result.score.passed ? 'success' : 'warning'}>{result.score.total}/60 pts</Badge></div>
            <p className="text-sm text-gray-400">{result.score.summary}</p>
            <div className="space-y-2">{result.score.criteria.map((criterion) => <div key={criterion.name} className="flex justify-between text-sm"><span className="text-gray-400">{criterion.name}</span><span className={criterion.score >= criterion.minimum ? 'text-emerald-400' : 'text-amber-400'}>{criterion.score}/{criterion.max_score}</span></div>)}</div>
            {result.line_suggestions?.length > 0 && (
              <div className="space-y-2">
                <p className="text-xs text-gray-500 uppercase tracking-wide">Gemini line-by-line suggestions</p>
                {result.line_suggestions.map((suggestion) => (
                  <div key={`${suggestion.line}-${suggestion.issue}`} className="rounded-lg border border-border bg-background p-3 text-sm">
                    <p className="text-primary-light">Line {suggestion.line}: {suggestion.issue}</p>
                    <p className="text-gray-300 mt-1">{suggestion.suggestion}</p>
                  </div>
                ))}
              </div>
            )}
            {(!result.line_suggestions || result.line_suggestions.length === 0) && (
              <p className="text-xs text-gray-500">Gemini returned no line-specific suggestions for this document.</p>
            )}
            {result.revised_document && <div><p className="text-xs text-gray-500 uppercase tracking-wide mb-2">Suggested revision</p><div className="rounded-lg bg-background p-4 text-sm text-gray-300 whitespace-pre-wrap max-h-80 overflow-y-auto">{result.revised_document}</div><Button variant="secondary" size="sm" icon={Sparkles} className="mt-3" onClick={() => setDocumentText(result.revised_document)}><Check size={14} className="mr-1" /> Use revision</Button></div>}
          </div>
        )}
      </div>
    </div>
  );
};

export default ScannerInterface;
