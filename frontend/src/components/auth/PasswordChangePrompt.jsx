import React, { useState } from 'react';
import { authApi } from '../../lib/api';
import Button from '../ui/Button';
import Input from '../ui/Input';

const PasswordChangePrompt = ({ onComplete }) => {
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (event) => {
    event.preventDefault();
    if (password.length < 8 || password !== confirmation) {
      setError('Use at least 8 characters and make both passwords match.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      await authApi.changePassword(null, password);
      onComplete();
    } catch (err) {
      setError(err.message || 'Could not change your password.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-5">
      <div className="w-full max-w-md rounded-xl border border-border bg-surface p-6 shadow-2xl">
        <h2 className="text-lg font-semibold text-gray-100">Change your temporary password</h2>
        <p className="mt-2 text-sm text-gray-400">Choose a new password before continuing.</p>
        <form onSubmit={submit} className="mt-5 space-y-4">
          <Input label="New password" type="password" required value={password} onChange={(event) => setPassword(event.target.value)} />
          <Input label="Confirm password" type="password" required value={confirmation} onChange={(event) => setConfirmation(event.target.value)} />
          {error && <p className="text-sm text-red-400">{error}</p>}
          <Button type="submit" className="w-full" loading={busy}>Save password</Button>
        </form>
      </div>
    </div>
  );
};

export default PasswordChangePrompt;
