import React, { useMemo, useState } from 'react';
import { Plus, Pencil, ArrowUp, ArrowDown, ShieldCheck, Trash2, Settings2, Link2 } from 'lucide-react';
import { STAGES } from '../../constants/stages';
import StageSection from './StageSection';
import KebabMenu from '../ui/KebabMenu';
import Modal from '../ui/Modal';
import Button from '../ui/Button';
import Input from '../ui/Input';
import Dropdown from '../ui/Dropdown';
import { stagesApi } from '../../lib/api';

const SourcePanel = ({
  documents = [],
  canReview = false,
  canDelete = false,
  onChanged,
  projectId,
  stages = [],
  canManageStages = false,
  onStagesChanged,
}) => {
  const groupedDocs = useMemo(() => documents.reduce((acc, doc) => {
    const stage = doc.stage || 'Unspecified';
    (acc[stage] = acc[stage] || []).push(doc);
    return acc;
  }, {}), [documents]);

  // Real project stages first (in order, including empty ones), then any
  // stage names that appear only on documents (legacy / unspecified).
  const orderedStages = useMemo(
    () => [...stages].sort((a, b) => a.order_index - b.order_index),
    [stages],
  );
  const managedNames = new Set(orderedStages.map((s) => s.name));
  const extraNames = Object.keys(groupedDocs)
    .filter((name) => !managedNames.has(name))
    .sort((a, b) => {
      const ia = STAGES.indexOf(a); const ib = STAGES.indexOf(b);
      if (ia !== -1 && ib !== -1) return ia - ib;
      return a.localeCompare(b);
    });

  // --- create-stage modal ---
  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState('');
  const [newPos, setNewPos] = useState(''); // '' = append
  const [createBusy, setCreateBusy] = useState(false);
  const [createErr, setCreateErr] = useState('');

  // --- per-stage settings modal ---
  const [settingsStage, setSettingsStage] = useState(null);
  const [editName, setEditName] = useState('');
  const [reqApproval, setReqApproval] = useState(false);
  const [refIds, setRefIds] = useState([]); // stage_ids this stage references
  const [reassignTo, setReassignTo] = useState('');
  const [stageBusy, setStageBusy] = useState(false);
  const [stageErr, setStageErr] = useState('');
  const [stageNotice, setStageNotice] = useState('');

  const countFor = (name) => (groupedDocs[name] || []).length;
  // A single silent reload (docs + stages) — keeps the settings modal open.
  const refresh = async () => { await onStagesChanged?.(); };

  const openSettings = (stage) => {
    setSettingsStage(stage);
    setEditName(stage.name);
    setReqApproval(Boolean(stage.requires_approval));
    setRefIds(stage.references || []);
    setReassignTo('');
    setStageErr(''); setStageNotice('');
  };

  const handleCreate = async () => {
    const name = newName.trim();
    if (!name) return;
    setCreateBusy(true); setCreateErr('');
    try {
      const body = { name };
      if (newPos !== '') body.order_index = Number(newPos);
      await stagesApi.create(projectId, body);
      setCreateOpen(false); setNewName(''); setNewPos('');
      await refresh();
    } catch (err) {
      setCreateErr(err.message || 'Could not create the stage.');
    } finally {
      setCreateBusy(false);
    }
  };

  const patchStage = async (patch, successMsg) => {
    setStageBusy(true); setStageErr(''); setStageNotice('');
    try {
      const updated = await stagesApi.update(projectId, settingsStage.stage_id, patch);
      setSettingsStage(updated);
      setEditName(updated.name);
      setReqApproval(Boolean(updated.requires_approval));
      setRefIds(updated.references || []);
      if (successMsg) setStageNotice(successMsg);
      await refresh();
    } catch (err) {
      setStageErr(err.message || 'Could not update the stage.');
    } finally {
      setStageBusy(false);
    }
  };

  const toggleReference = async (targetId) => {
    const next = refIds.includes(targetId)
      ? refIds.filter((id) => id !== targetId)
      : [...refIds, targetId];
    setRefIds(next); // optimistic
    setStageBusy(true); setStageErr(''); setStageNotice('');
    try {
      const updated = await stagesApi.setReferences(projectId, settingsStage.stage_id, next);
      setSettingsStage(updated);
      setRefIds(updated.references || []);
      setStageNotice('References updated.');
      await refresh();
    } catch (err) {
      setRefIds(refIds); // roll back
      setStageErr(err.message || 'Could not update references.');
    } finally {
      setStageBusy(false);
    }
  };

  const handleDelete = async () => {
    const docs = countFor(settingsStage.name);
    if (docs > 0 && !reassignTo) {
      setStageErr(`This stage has ${docs} document(s). Choose a stage to move them to first.`);
      return;
    }
    if (!window.confirm(
      docs > 0
        ? `Move ${docs} document(s) to the selected stage and delete "${settingsStage.name}"?`
        : `Delete the stage "${settingsStage.name}"?`,
    )) return;
    setStageBusy(true); setStageErr(''); setStageNotice('');
    try {
      await stagesApi.remove(projectId, settingsStage.stage_id, reassignTo || undefined);
      setSettingsStage(null);
      await refresh();
    } catch (err) {
      setStageErr(err.message || 'Could not delete the stage.');
    } finally {
      setStageBusy(false);
    }
  };

  const moveStage = (dir) => {
    const idx = orderedStages.findIndex((s) => s.stage_id === settingsStage.stage_id);
    const target = idx + dir;
    if (target < 0 || target >= orderedStages.length) return;
    patchStage({ order_index: target }, 'Order updated.');
  };

  const reassignOptions = orderedStages
    .filter((s) => s.stage_id !== settingsStage?.stage_id)
    .map((s) => ({ label: s.name, value: s.stage_id }));
  const settingsIdx = settingsStage
    ? orderedStages.findIndex((s) => s.stage_id === settingsStage.stage_id)
    : -1;

  const hasAnything = orderedStages.length > 0 || extraNames.length > 0;

  return (
    <div className="flex flex-col bg-surface border-r border-border lg:min-h-full">
      <div className="p-4 border-b border-border/50">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-gray-100">Sources</h2>
          <div className="flex items-center gap-2">
            <span className="text-[10px] uppercase tracking-widest text-primary font-bold">Library</span>
            {canManageStages && (
              <KebabMenu
                label="Manage stages"
                items={[{ label: 'Create new stage', icon: Plus, onClick: () => { setCreateErr(''); setCreateOpen(true); } }]}
              />
            )}
          </div>
        </div>
        <p className="text-sm text-gray-400 mt-1">Project documents and evidence</p>
      </div>

      <div className="p-4">
        {!hasAnything ? (
          <div className="text-center py-8 text-gray-500 text-sm">
            {canManageStages ? 'No stages yet — create one from the ⋮ menu above.' : 'No documents found for this project.'}
          </div>
        ) : (
          <>
            {orderedStages.map((stage) => (
              <StageSection
                key={stage.stage_id}
                stage={stage.name}
                documents={groupedDocs[stage.name] || []}
                canReview={canReview}
                canDelete={canDelete}
                onChanged={onChanged}
                requiresApproval={stage.requires_approval}
                menu={canManageStages ? (
                  <KebabMenu
                    label={`Stage settings for ${stage.name}`}
                    items={[{ label: 'Stage settings', icon: Settings2, onClick: () => openSettings(stage) }]}
                  />
                ) : null}
              />
            ))}
            {extraNames.map((name) => (
              <StageSection
                key={name}
                stage={name}
                documents={groupedDocs[name] || []}
                canReview={canReview}
                canDelete={canDelete}
                onChanged={onChanged}
              />
            ))}
          </>
        )}
      </div>

      {/* Create stage */}
      <Modal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title="Create new stage"
        description="Stages structure the Sources library and drive stage-gated approval."
        footer={(
          <>
            <Button variant="ghost" onClick={() => setCreateOpen(false)}>Cancel</Button>
            <Button onClick={handleCreate} loading={createBusy} disabled={!newName.trim()}>Create stage</Button>
          </>
        )}
      >
        <div className="space-y-4">
          {createErr && <p className="text-sm text-red-400">{createErr}</p>}
          <Input
            label="Stage name" required autoFocus
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="e.g. Deployment"
          />
          <Dropdown
            label="Position"
            value={newPos}
            onChange={setNewPos}
            options={[
              { label: 'Add to the end', value: '' },
              ...orderedStages.map((s, i) => ({ label: `Before "${s.name}" (position ${i + 1})`, value: String(i) })),
            ]}
          />
        </div>
      </Modal>

      {/* Per-stage settings */}
      <Modal
        open={Boolean(settingsStage)}
        onClose={() => setSettingsStage(null)}
        title={settingsStage ? `Stage: ${settingsStage.name}` : 'Stage'}
        description="Rename, reorder, toggle approval, or delete this stage."
        footer={<Button variant="ghost" onClick={() => setSettingsStage(null)}>Close</Button>}
      >
        {settingsStage && (
          <div className="space-y-5">
            {stageNotice && <p className="text-sm text-emerald-400">{stageNotice}</p>}
            {stageErr && <p className="text-sm text-red-400">{stageErr}</p>}

            {/* Rename */}
            <div className="flex items-end gap-2">
              <Input
                label="Name"
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
              />
              <Button
                variant="secondary" icon={Pencil} loading={stageBusy}
                disabled={!editName.trim() || editName.trim() === settingsStage.name}
                onClick={() => patchStage({ name: editName.trim() }, 'Stage renamed.')}
              >
                Rename
              </Button>
            </div>

            {/* Reorder */}
            <div>
              <p className="text-sm font-medium text-gray-300 mb-1.5">
                Order — position {settingsIdx + 1} of {orderedStages.length}
              </p>
              <div className="flex gap-2">
                <Button variant="secondary" icon={ArrowUp} disabled={stageBusy || settingsIdx <= 0} onClick={() => moveStage(-1)}>
                  Move up
                </Button>
                <Button variant="secondary" icon={ArrowDown} disabled={stageBusy || settingsIdx === orderedStages.length - 1} onClick={() => moveStage(1)}>
                  Move down
                </Button>
              </div>
            </div>

            {/* requires_approval */}
            <div className="flex items-start justify-between gap-3 rounded-lg border border-border bg-background px-3 py-2.5">
              <div className="min-w-0">
                <p className="text-sm text-gray-200 flex items-center gap-1.5">
                  <ShieldCheck size={14} className="text-amber-400" /> Require approval
                </p>
                <p className="text-xs text-gray-500 mt-0.5">
                  Documents uploaded to this stage must be submitted and approved (draft → pending → approved).
                </p>
              </div>
              <button
                type="button"
                role="switch"
                aria-checked={reqApproval}
                disabled={stageBusy}
                onClick={() => patchStage({ requires_approval: !reqApproval }, `Approval ${!reqApproval ? 'enabled' : 'disabled'} for this stage.`)}
                className={`mt-0.5 shrink-0 relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${reqApproval ? 'bg-primary' : 'bg-border'} disabled:opacity-50`}
              >
                <span className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${reqApproval ? 'translate-x-4' : 'translate-x-0.5'}`} />
              </button>
            </div>

            {/* Stage references */}
            <div className="border-t border-border/60 pt-4">
              <p className="text-sm font-medium text-gray-300 flex items-center gap-1.5">
                <Link2 size={14} className="text-primary" /> References
              </p>
              <p className="text-xs text-gray-500 mt-0.5 mb-2">
                Other stages this one depends on. Retrieval scoped to
                &ldquo;{settingsStage.name}&rdquo; also pulls from these (one-way).
              </p>
              {orderedStages.filter((s) => s.stage_id !== settingsStage.stage_id).length === 0 ? (
                <p className="text-xs text-gray-600">No other stages in this project.</p>
              ) : (
                <div className="space-y-1">
                  {orderedStages
                    .filter((s) => s.stage_id !== settingsStage.stage_id)
                    .map((s) => (
                      <label
                        key={s.stage_id}
                        className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-gray-300 hover:bg-surface-hover cursor-pointer"
                      >
                        <input
                          type="checkbox"
                          className="accent-primary"
                          disabled={stageBusy}
                          checked={refIds.includes(s.stage_id)}
                          onChange={() => toggleReference(s.stage_id)}
                        />
                        {s.name}
                      </label>
                    ))}
                </div>
              )}
            </div>

            {/* Delete */}
            <div className="border-t border-border/60 pt-4 space-y-2">
              <p className="text-sm font-medium text-gray-300">Delete stage</p>
              {countFor(settingsStage.name) > 0 ? (
                <>
                  <p className="text-xs text-gray-500">
                    This stage has {countFor(settingsStage.name)} document(s). Pick a stage to move them to,
                    then delete.
                  </p>
                  <Dropdown
                    options={reassignOptions}
                    value={reassignTo}
                    onChange={setReassignTo}
                    placeholder={reassignOptions.length ? 'Reassign documents to…' : 'No other stage available'}
                  />
                </>
              ) : (
                <p className="text-xs text-gray-500">This stage has no documents and can be deleted.</p>
              )}
              <Button
                variant="danger" icon={Trash2} loading={stageBusy}
                disabled={orderedStages.length <= 1}
                onClick={handleDelete}
              >
                Delete stage
              </Button>
              {orderedStages.length <= 1 && (
                <p className="text-xs text-amber-400">A project must keep at least one stage.</p>
              )}
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
};

export default SourcePanel;
