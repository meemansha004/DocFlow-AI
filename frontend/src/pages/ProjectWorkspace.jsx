import React, { useEffect, useState, useCallback } from 'react';
import { useParams, Link } from 'react-router-dom';
import { ArrowLeft, Search as SearchIcon, UploadCloud, MessageSquare, ShieldCheck, Lock } from 'lucide-react';
import SourcePanel from '../components/sources/SourcePanel';
import ChatPanel from '../components/chat/ChatPanel';
import StudioPanel from '../components/studio/StudioPanel';
import Badge from '../components/ui/Badge';
import Modal from '../components/ui/Modal';
import Button from '../components/ui/Button';
import { projectsApi, agentsApi, workspaceApi, accessRequestsApi } from '../lib/api';
import { useAuth } from '../context/AuthContext';

const ProjectWorkspace = () => {
  const { projectId } = useParams();
  const { user } = useAuth();
  const [project, setProject] = useState(null);
  const [documents, setDocuments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [memberSearch, setMemberSearch] = useState('');

  const [gapReport, setGapReport] = useState(null);
  const [gapLoading, setGapLoading] = useState(false);
  const [gapOpen, setGapOpen] = useState(false);

  // Confidential-access requests (viewer/contributor entry point)
  const [wsProject, setWsProject] = useState(null); // this project's teams + my per-team role
  const [myRequests, setMyRequests] = useState([]);
  const [accessOpen, setAccessOpen] = useState(false);
  const [reqBusy, setReqBusy] = useState(null); // team_id in flight
  const [reqNotice, setReqNotice] = useState('');
  const [reqError, setReqError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [projects, docs, ws, mine] = await Promise.all([
        projectsApi.list(),
        projectsApi.documents(projectId),
        workspaceApi.get().catch(() => null),
        accessRequestsApi.mine().catch(() => []),
      ]);
      setProject(projects.find((p) => p.project_id === projectId) || { project_id: projectId, project_name: projectId });
      setDocuments(docs);
      setWsProject(ws?.projects?.find((p) => p.project_id === projectId) || null);
      setMyRequests(Array.isArray(mine) ? mine : []);
    } catch (err) {
      setError(err.message || 'Could not load this project.');
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    load();
  }, [load]);

  const handleAnalyzeGaps = async () => {
    setGapOpen(true);
    setGapLoading(true);
    try {
      const result = await agentsApi.analyzeGaps(projectId);
      setGapReport(result);
    } catch (err) {
      setGapReport({ gap_report: `Could not analyze gaps: ${err.message}` });
    } finally {
      setGapLoading(false);
    }
  };

  // Teams in this project where I'm viewer/contributor — i.e. not auto-cleared for confidential docs.
  const requestableTeams = (wsProject?.teams || []).filter(
    (t) => t.role === 'viewer' || t.role === 'contributor',
  );
  const requestStatus = (teamId) => {
    const r = myRequests.find((x) => x.team_id === teamId);
    if (!r) return { state: 'none' };
    if (r.active) return { state: 'granted', request: r };
    if (r.status === 'pending') return { state: 'pending', request: r };
    return { state: r.status === 'denied' ? 'denied' : 'expired', request: r };
  };

  const handleRequestAccess = async (teamId) => {
    setReqBusy(teamId);
    setReqNotice('');
    setReqError('');
    try {
      await accessRequestsApi.create(teamId);
      setReqNotice('Request sent — a team lead will review it.');
      setMyRequests(await accessRequestsApi.mine());
    } catch (err) {
      setReqError(err.message || 'Could not send the request.');
    } finally {
      setReqBusy(null);
    }
  };

  const role = user?.is_org_admin ? 'org_admin' : user?.project_roles?.[projectId];
  // Our model: team_lead+ or project_admin can review (the backend still checks
  // the document's *specific* team and returns a clear 403 otherwise).
  const canReview = ['org_admin', 'project_admin', 'team_lead'].includes(role);
  const canManageAccess = ['org_admin', 'project_admin', 'team_lead'].includes(role);
  const pendingCount = documents.filter((document) => document.workflow_state === 'pending_review').length;
  const approvedCount = documents.filter((document) => document.workflow_state === 'approved').length;
  const memberNames = project?.members?.map((member) => member.name || member.username).filter(Boolean) || [];
  const matchingMembers = project?.members?.filter((member) => {
    const query = memberSearch.trim().toLowerCase();
    if (!query) return false;
    return [member.name, member.username, member.team_name, member.role]
      .filter(Boolean)
      .some((value) => value.toLowerCase().includes(query));
  }) || [];
  const memberSearchPlaceholder = memberNames.length
    ? `Search members: ${memberNames.slice(0, 2).join(', ')}${memberNames.length > 2 ? ', ...' : ''}`
    : 'Search project members';

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="w-8 h-8 border-4 border-primary/30 border-t-primary rounded-full animate-spin" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center p-8 text-center">
        <h2 className="text-2xl font-bold text-gray-200 mb-2">Couldn't open this project</h2>
        <p className="text-gray-500 mb-4">{error}</p>
        <Link to="/" className="text-primary hover:underline">Return to Projects</Link>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col">
      {/* Workspace Header */}
      <div className="h-14 border-b border-border bg-background px-6 flex items-center gap-4 shrink-0">
        <Link to="/" className="text-gray-400 hover:text-gray-200 transition-colors flex items-center gap-1 text-sm">
          <ArrowLeft size={16} />
          All Projects
        </Link>
        <div className="w-px h-4 bg-border"></div>
        <h1 className="font-semibold text-gray-100">{project.project_name}</h1>
        {project.assigned_teams?.length > 0 && (
          <Badge variant="neutral">Team: {project.assigned_teams.join(', ')}</Badge>
        )}
        <Badge variant="active">{documents.length} doc{documents.length === 1 ? '' : 's'}</Badge>
        <div className="hidden xl:flex items-center gap-2">
          <Badge variant="success">{approvedCount} approved</Badge>
          {pendingCount > 0 && <Badge variant="warning">{pendingCount} pending review</Badge>}
        </div>
        <div className="relative w-40 sm:w-52 shrink-0">
          <SearchIcon size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
          <input
            type="search"
            value={memberSearch}
            onChange={(event) => setMemberSearch(event.target.value)}
            placeholder={memberSearchPlaceholder}
            aria-label="Search project members"
            className="w-full rounded-lg border border-border bg-surface py-2 pl-9 pr-3 text-xs text-gray-200 placeholder:text-gray-600 focus:border-primary focus:outline-none"
          />
          {memberSearch.trim() && (
            <div className="absolute right-0 top-full z-20 mt-2 w-72 rounded-lg border border-border bg-surface p-2 shadow-xl">
              {matchingMembers.length > 0 ? matchingMembers.map((member) => (
                <div key={member.user_id} className="rounded-md px-3 py-2 hover:bg-background">
                  <p className="text-sm text-gray-200">{member.name || member.username}</p>
                  <p className="text-xs text-gray-500 mt-0.5">
                    {member.role || 'Member'}{member.team_name ? ` · ${member.team_name}` : ''}
                  </p>
                </div>
              )) : (
                <p className="px-3 py-2 text-xs text-gray-500">No project members found.</p>
              )}
            </div>
          )}
        </div>
        <div className="flex-1" />
        {canManageAccess && (
          <Link to={`/admin?project_id=${encodeURIComponent(projectId)}`}>
            <Button size="sm" variant="secondary" icon={ShieldCheck}>
              {role === 'team_lead' && !user?.is_org_admin ? 'Manage team' : 'Manage access'}
            </Button>
          </Link>
        )}
        {requestableTeams.length > 0 && (
          <Button
            size="sm"
            variant="secondary"
            icon={Lock}
            onClick={() => { setReqNotice(''); setReqError(''); setAccessOpen(true); }}
          >
            Confidential access
          </Button>
        )}
        <Link to={`/projects/${projectId}/upload`}>
          <Button size="sm" variant="secondary" icon={UploadCloud}>Add sources</Button>
        </Link>
        <Link to={`/studio/query?project_id=${encodeURIComponent(projectId)}`}>
         <Button size="sm" variant="secondary" icon={MessageSquare}>Open Query Agent</Button>
        </Link>
        <Button size="sm" variant="secondary" icon={SearchIcon} onClick={handleAnalyzeGaps}>
          Analyze Gaps
        </Button>
      </div>

      {/* 3-Column Layout */}
      <div className="flex-1 flex flex-col lg:flex-row">
        <div className="w-full lg:w-[30%] shrink-0">
          <SourcePanel documents={documents} canReview={canReview} canDelete={user?.is_org_admin} onChanged={load} />
        </div>
        <div className="w-full lg:w-[40%] shrink-0 border-t border-border lg:border-t-0">
          <ChatPanel projectId={projectId} />
        </div>
        <div className="w-full lg:w-[30%] shrink-0 border-t border-border lg:border-t-0">
          <StudioPanel projectId={projectId} onAnalyzeGaps={handleAnalyzeGaps} />
        </div>
      </div>

      <Modal
        open={accessOpen}
        onClose={() => setAccessOpen(false)}
        title="Request confidential access"
        description="Confidential documents need clearance. Ask the team lead for a grant — it lasts 90 days."
        footer={<Button variant="ghost" onClick={() => setAccessOpen(false)}>Close</Button>}
      >
        <div className="space-y-3">
          {reqNotice && <p className="text-sm text-emerald-400">{reqNotice}</p>}
          {reqError && <p className="text-sm text-red-400">{reqError}</p>}
          {requestableTeams.map((t) => {
            const { state } = requestStatus(t.team_id);
            return (
              <div key={t.team_id} className="flex items-center justify-between gap-3 rounded-lg border border-border bg-background px-3 py-2.5">
                <div className="min-w-0">
                  <p className="text-sm text-gray-200 truncate">{t.name}</p>
                  <p className="text-xs text-gray-500">Your role: {t.role}</p>
                </div>
                {state === 'granted' && <Badge variant="success">Access granted</Badge>}
                {state === 'pending' && <Badge variant="warning">Pending review</Badge>}
                {(state === 'none' || state === 'denied' || state === 'expired') && (
                  <div className="flex items-center gap-2">
                    {state === 'denied' && <span className="text-xs text-gray-500">Previously denied</span>}
                    {state === 'expired' && <span className="text-xs text-gray-500">Grant expired</span>}
                    <Button
                      size="sm"
                      onClick={() => handleRequestAccess(t.team_id)}
                      loading={reqBusy === t.team_id}
                    >
                      {state === 'none' ? 'Request access' : 'Request again'}
                    </Button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </Modal>

      <Modal
        open={gapOpen}
        onClose={() => setGapOpen(false)}
        title="Documentation Gap Analysis"
        description="Gap-Detection Agent — compares uploaded documents against expected SDLC stage coverage."
      >
        {gapLoading ? (
          <div className="flex justify-center py-8">
            <div className="w-6 h-6 border-4 border-primary/30 border-t-primary rounded-full animate-spin" />
          </div>
        ) : (
          <div className="text-sm text-gray-300 whitespace-pre-wrap max-h-[50vh] overflow-y-auto">
            {gapReport?.gap_report}
          </div>
        )}
      </Modal>
    </div>
  );
};

export default ProjectWorkspace;
