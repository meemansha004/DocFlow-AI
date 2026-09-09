import React, { useEffect, useMemo, useState } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { workspaceApi, documentsApi, clearWorkspaceCache, ApiError } from '../lib/api';
import Input from '../components/ui/Input';
import Dropdown from '../components/ui/Dropdown';
import Button from '../components/ui/Button';
import Textarea from '../components/ui/Textarea';

const SENSITIVITY_OPTIONS = [
  { label: 'Public', value: 'public' },
  { label: 'Internal', value: 'internal' },
  { label: 'Confidential', value: 'confidential' },
];

// Our POST /documents/upload takes Markdown `content` (not a file), an explicit
// `team_id` (which of the caller's teams they act as), and a real `stage_id`.
// The page shell / layout below is the teammate's; only the fields changed.
const UploadDocumentPage = () => {
  const { projectId } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();

  const [workspace, setWorkspace] = useState(null);
  const [wsError, setWsError] = useState('');

  const [documentType, setDocumentType] = useState('');
  const [stageId, setStageId] = useState('');
  const [teamId, setTeamId] = useState('');
  const [sensitivity, setSensitivity] = useState('internal');
  const [content, setContent] = useState('');
  const [description, setDescription] = useState('');

  const [errors, setErrors] = useState({});
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState('');

  useEffect(() => {
    workspaceApi.get().then(setWorkspace).catch((err) => setWsError(err.message));
  }, []);

  const project = useMemo(
    () => workspace?.projects.find((p) => p.project_id === projectId) || null,
    [workspace, projectId],
  );

  useEffect(() => {
    if (project?.teams.length === 1) setTeamId(project.teams[0].team_id);
  }, [project]);

  const selectedStage = project?.stages.find((s) => s.stage_id === stageId);

  const handleSubmit = async (e) => {
    e.preventDefault();
    const next = {};
    if (!documentType.trim()) next.documentType = 'Document type is required.';
    if (!teamId) next.teamId = 'Choose which team you are uploading as.';
    if (!stageId) next.stageId = 'Stage is required.';
    if (!content.trim()) next.content = 'Document content is required.';
    if (Object.keys(next).length) { setErrors(next); return; }

    setSubmitting(true);
    setSubmitError('');
    try {
      await documentsApi.upload({
        document_type: documentType.trim(),
        stage_id: stageId,
        content,
        team_id: teamId,
        sensitivity_level: sensitivity,
      });
      clearWorkspaceCache();
      navigate(`/projects/${projectId}`);
    } catch (err) {
      setSubmitError(err instanceof ApiError ? `${err.status}: ${err.message}` : err.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex-1 flex flex-col bg-background">
      <div className="h-14 border-b border-border bg-background px-6 flex items-center shrink-0">
        <Link to={`/projects/${projectId}`} className="text-gray-400 hover:text-gray-200 transition-colors flex items-center gap-1 text-sm">
          <ArrowLeft size={16} />
          Back to Project
        </Link>
      </div>

      <div className="max-w-3xl mx-auto w-full p-8">
        <div className="mb-8">
          <h1 className="text-2xl font-bold text-gray-100">Upload Document</h1>
          <p className="text-gray-400 mt-1">
            Paste the document as Markdown. If the chosen stage requires approval, a
            draft workflow entry is created automatically.
          </p>
        </div>

        {wsError && <p className="text-red-400 text-sm mb-4">{wsError}</p>}

        <form onSubmit={handleSubmit} className="space-y-8">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="space-y-6">
              <h3 className="text-lg font-medium text-gray-200 border-b border-border pb-2">Required Details</h3>
              <Input
                label="Document Type"
                placeholder="e.g. Test Plan"
                required
                value={documentType}
                onChange={(e) => { setDocumentType(e.target.value); if (errors.documentType) setErrors({ ...errors, documentType: null }); }}
                error={errors.documentType}
              />
              <Dropdown
                label="Stage"
                placeholder={project ? 'Select a stage' : 'Loading…'}
                options={(project?.stages || []).map((s) => ({
                  label: s.requires_approval ? `${s.name} · needs approval` : s.name,
                  value: s.stage_id,
                }))}
                value={stageId}
                onChange={(val) => { setStageId(val); if (errors.stageId) setErrors({ ...errors, stageId: null }); }}
                error={errors.stageId}
              />
              {selectedStage?.requires_approval && (
                <p className="text-xs text-amber-400 -mt-3">
                  This stage requires sign-off — the document starts as a draft you can submit for review.
                </p>
              )}
            </div>

            <div className="space-y-6">
              <h3 className="text-lg font-medium text-gray-200 border-b border-border pb-2">Access Control</h3>
              <Dropdown
                label="Upload as team"
                placeholder={project ? 'Which of your teams?' : 'Loading…'}
                options={(project?.teams || []).map((t) => ({ label: `${t.name} (${t.role})`, value: t.team_id }))}
                value={teamId}
                onChange={(val) => { setTeamId(val); if (errors.teamId) setErrors({ ...errors, teamId: null }); }}
                error={errors.teamId}
              />
              <Dropdown
                label="Sensitivity Level"
                options={SENSITIVITY_OPTIONS}
                value={sensitivity}
                onChange={setSensitivity}
              />
            </div>
          </div>

          <Textarea
            label="Document Content (Markdown)"
            required
            placeholder={'# My Document\n\nSection one…'}
            value={content}
            onChange={(e) => { setContent(e.target.value); if (errors.content) setErrors({ ...errors, content: null }); }}
            error={errors.content}
            className="min-h-[240px] font-mono"
          />

          <Textarea
            label="Description (optional, not stored yet — for your own notes)"
            placeholder="Provide context about this document..."
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />

          <div className="bg-surface p-4 rounded-lg border border-border flex gap-8">
            <div>
              <p className="text-xs text-gray-500 uppercase font-medium mb-1">Uploaded By</p>
              <p className="text-sm text-gray-200">{user?.email}</p>
            </div>
            <div>
              <p className="text-xs text-gray-500 uppercase font-medium mb-1">Upload Date</p>
              <p className="text-sm text-gray-200">{new Date().toLocaleDateString()}</p>
            </div>
          </div>

          {submitError && <p className="text-red-400 text-sm">{submitError}</p>}

          <div className="flex justify-end gap-4 pt-6 border-t border-border">
            <Button variant="ghost" type="button" onClick={() => navigate(`/projects/${projectId}`)}>Cancel</Button>
            <Button type="submit" loading={submitting}>Upload Document</Button>
          </div>
        </form>
      </div>
    </div>
  );
};

export default UploadDocumentPage;
