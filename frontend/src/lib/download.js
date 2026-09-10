// Trigger a browser "Save as" for an in-memory Blob with a chosen filename.
// The object URL is revoked on a delay — revoking it synchronously right after
// a.click() races the browser's read of the blob and makes Chrome fall back to
// a random <uuid>.tmp filename.
export function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename || 'download';
  a.rel = 'noopener';
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}
