import React, { useEffect, useState } from 'react';
import { KeyRound, Loader2, CheckCircle2, AlertTriangle } from 'lucide-react';
import { apiClient } from '../../utils/apiClient';

/**
 * Where a reset link lands.
 *
 * Supabase puts the recovery token in the URL FRAGMENT, not the query string —
 * `#access_token=...&type=recovery` — so it never reaches a server, which is
 * the point. Normally the browser's own Supabase client would pick it up and
 * call updateUser. This deployment has no such client (the VITE_SUPABASE_*
 * variables are not set in the build), so the token is read here and handed to
 * the API, which verifies it and makes the change with the service role.
 *
 * Rendered outside the tournament provider: someone locked out has no session,
 * and this page must not need one.
 */
export const ResetPassword: React.FC = () => {
  const [token, setToken] = useState<string | null>(null);
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    // The fragment carries both the route and the token:
    //   #/reset-password#access_token=...   or   #access_token=...&type=recovery
    const raw = window.location.hash || '';
    const afterHash = raw.replace(/^#/, '');
    const query = afterHash.includes('access_token=')
      ? afterHash.slice(afterHash.indexOf('access_token='))
      : '';
    const found = new URLSearchParams(query).get('access_token');
    if (found) {
      setToken(found);
      // Take it out of the address bar: a recovery token in browser history or
      // a shared screenshot is a way into the account.
      window.history.replaceState(null, '', window.location.pathname + '#/reset-password');
    } else {
      setError('This link is missing its token. Ask for a new reset email.');
    }
  }, []);

  const submit = async () => {
    if (password !== confirm) { setError('The two passwords do not match.'); return; }
    if (password.length < 6) { setError('Use at least 6 characters.'); return; }
    setBusy(true);
    setError('');
    try {
      await apiClient.post('/auth/reset-password', {
        accessToken: token, newPassword: password,
      });
      setDone(true);
    } catch (e: any) {
      setError(e?.message || 'Could not set that password.');
    } finally {
      setBusy(false);
    }
  };

  const field = 'w-full text-sm px-3 py-2.5 border border-gray-200 rounded-lg bg-white focus:border-[#0B5D3B] focus:outline-hidden';

  return (
    <div className="min-h-screen bg-[#F8F6F0] flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-md overflow-hidden">
        <div className="px-5 py-4 bg-[#0B5D3B] text-white flex items-center gap-3">
          <KeyRound className="w-5 h-5 text-[#D4A72C]" />
          <h1 className="font-serif font-bold">Set a new password</h1>
        </div>

        <div className="p-5 space-y-4">
          {done ? (
            <>
              <div className="flex items-start gap-2 p-3 rounded-xl bg-emerald-50 border border-emerald-200">
                <CheckCircle2 className="w-5 h-5 text-emerald-600 mt-0.5 shrink-0" />
                <p className="text-sm text-emerald-900">
                  Password set. Sign in with it from here on.
                </p>
              </div>
              <a
                href="#/"
                onClick={() => { window.location.hash = '#/'; window.location.reload(); }}
                className="block w-full text-center px-4 py-2.5 text-sm font-bold bg-[#0B5D3B] hover:bg-[#08472d] text-white rounded-xl"
              >
                Go to sign in
              </a>
            </>
          ) : (
            <>
              <p className="text-xs text-gray-600">
                Choose a password for your account. You will use it to sign in.
              </p>
              <div>
                <label className="block text-xs font-bold text-gray-700 mb-1.5">New password</label>
                <input className={field} type="password" value={password}
                       disabled={!token || busy}
                       onChange={e => setPassword(e.target.value)} />
              </div>
              <div>
                <label className="block text-xs font-bold text-gray-700 mb-1.5">Confirm password</label>
                <input className={field} type="password" value={confirm}
                       disabled={!token || busy}
                       onChange={e => setConfirm(e.target.value)} />
              </div>

              {error && (
                <div className="flex items-start gap-2 p-3 rounded-xl bg-red-50 border border-red-200 text-xs text-red-800">
                  <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
                  <span>{error}</span>
                </div>
              )}

              <button
                onClick={submit}
                disabled={!token || busy || !password || password !== confirm}
                className="w-full px-4 py-2.5 text-sm font-bold bg-[#0B5D3B] hover:bg-[#08472d] text-white rounded-xl shadow-md flex items-center justify-center gap-1.5 disabled:opacity-40"
              >
                {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <KeyRound className="w-4 h-4" />}
                Set password
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
};
