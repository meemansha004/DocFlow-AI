// Mirrors app/services/document_workflow.py::DOCUMENT_TYPE_ALIASES keys on the backend.
export const DOC_TYPES = [
  'PRD',
  'BRD',
  'SRS',
  'ADR',
  'API Specification',
  'Project Plan',
  'Design Document',
  'Risk Register',
  'Test Plan',
  'Runbook',
  'Release Notes',
  'Proposal',
  'Meeting Notes',
  'Other',
];

export const SENSITIVITY_LEVELS = [
  { label: 'Public', value: 0 },
  { label: 'Internal', value: 1 },
  { label: 'Confidential', value: 2 },
  { label: 'Restricted', value: 3 },
];

export const sensitivityLabel = (level) =>
  SENSITIVITY_LEVELS.find((s) => s.value === level)?.label || 'Internal';
