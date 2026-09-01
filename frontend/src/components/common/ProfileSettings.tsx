import React, { useRef, useState } from 'react';
import { X, Camera, Trash2, Loader2, User, Mail, KeyRound, Check } from 'lucide-react';
import { apiClient } from '../../utils/apiClient';
import { useNotify } from '../../context/NotificationContext';
import { Avatar } from './Avatar';

/**
 * Your own account: picture, details, email address, password.
 *
 * Everything here is about the signed-in person and nothing else. Role is
 * deliberately absent — being able to change your own role through a settings
 * panel is how anyone could become an admin through the sign-up form, and the
 * same mistake is not worth repeating in a nicer wrapper.
 */

interface Props {
  user: { id: string; name?: string; email?: string; avatar?: string | null;
          club?: string; city?: string; phone?: string; role?: string };
  onClose: () => void;
  onSaved: () => void;
}

/**
 * Shrink a chosen file to something sensible before it is sent.
 *
 * The picture is stored on the profile row and travels with every read of it,
 * so a 4 MB phone photo would be paid for on every page load by everyone. A
 * 256px square JPEG is about 10 KB and is more than the interface ever shows.
 */
function resizeToDataUrl(file: File, max = 256): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('Could not read that file.'));
    reader.onload = () => {
      const img = new Image();
      img.onerror = () => reject(new Error('That file is not an image.'));
      img.onload = () => {
        // Square crop from the centre, so portraits are not squashed.
        const side = Math.min(img.width, img.height);
        const canvas = document.createElement('canvas');
        canvas.width = canvas.height = Math.min(side, max);
        const ctx = canvas.getContext('2d');
        if (!ctx) { reject(new Error('Could not process that image.')); return; }
        ctx.drawImage(
          img,
          (img.width - side) / 2, (img.height - side) / 2, side, side,
          0, 0, canvas.width, canvas.height
        );
        resolve(canvas.toDataURL('image/jpeg', 0.85));
      };
      img.src = String(reader.result);
    };
    reader.readAsDataURL(file);
  });
}

type Tab = 'profile' | 'email' | 'password';

export const ProfileSettings: React.FC<Props> = ({ user, onClose, onSaved }) => {
  const notify = useNotify();
  const fileRef = useRef<HTMLInputElement>(null);
  const [tab, setTab] = useState<Tab>('profile');
  const [busy, setBusy] = useState(false);

  const [name, setName] = useState(user.name || '');
  const [club, setClub] = useState(user.club || '');
  const [city, setCity] = useState(user.city || '');
  const [phone, setPhone] = useState(user.phone || '');
  const [avatar, setAvatar] = useState<string | null>(user.avatar || null);

  const [newEmail, setNewEmail] = useState('');
  const [emailPassword, setEmailPassword] = useState('');

  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');

  const pickFile = async (file?: File | null) => {
    if (!file) return;
    try {
      setAvatar(await resizeToDataUrl(file));
    } catch (e) {
      notify.report(e, 'Could not use that picture.');
    }
  };

  const saveProfile = async () => {
    if (!name.trim()) { notify.error('A name is required.'); return; }
    setBusy(true);
    try {
      await apiClient.put('/auth/me', {
        name: name.trim(), club: club.trim(), city: city.trim(), phone: phone.trim(),
        // '' is how the API is told to remove the picture, which is different
        // from omitting the field and leaving it alone.
        avatar: avatar === null ? '' : avatar,
      });
      notify.success('Profile saved.');
      onSaved();
    } catch (e) {
      notify.report(e, 'Could not save your profile.');
    } finally {
      setBusy(false);
    }
  };

  const saveEmail = async () => {
    setBusy(true);
    try {
      const res: any = await apiClient.post('/auth/email', {
        currentPassword: emailPassword, newEmail: newEmail.trim(),
      });
      notify.success(res?.message || 'Email changed.');
      setNewEmail(''); setEmailPassword('');
      onSaved();
    } catch (e) {
      notify.report(e, 'Could not change your email.');
    } finally {
      setBusy(false);
    }
  };

  const savePassword = async () => {
    if (newPassword !== confirmPassword) {
      notify.error('The two new passwords do not match.');
      return;
    }
    setBusy(true);
    try {
      const res: any = await apiClient.post('/auth/password', { currentPassword, newPassword });
      notify.success(res?.message || 'Password changed.');
      setCurrentPassword(''); setNewPassword(''); setConfirmPassword('');
    } catch (e) {
      notify.report(e, 'Could not change your password.');
    } finally {
      setBusy(false);
    }
  };

  const field = 'w-full text-sm px-3 py-2.5 border border-gray-200 rounded-lg bg-white focus:border-[#0B5D3B] focus:outline-hidden';
  const label = 'block text-xs font-bold text-gray-700 mb-1.5';

  const TABS: { id: Tab; label: string; icon: typeof User }[] = [
    { id: 'profile', label: 'Profile', icon: User },
    { id: 'email', label: 'Email', icon: Mail },
    { id: 'password', label: 'Password', icon: KeyRound },
  ];

  return (
    <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-xs flex items-start sm:items-center justify-center p-3 overflow-y-auto">
      <div className="bg-white rounded-2xl w-full max-w-lg shadow-2xl my-6">
        <div className="px-5 py-4 bg-[#0B5D3B] text-white rounded-t-2xl flex items-center gap-3">
          <Avatar name={user.name} src={avatar} size={40} ring />
          <div className="flex-1 min-w-0">
            <h3 className="font-serif font-bold truncate">{user.name || 'Your account'}</h3>
            <p className="text-xs text-emerald-200 truncate">{user.email}</p>
          </div>
          <button onClick={onClose} aria-label="Close" className="p-1 rounded-lg hover:bg-white/10">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="flex border-b border-gray-200 bg-gray-50 px-3">
          {TABS.map(t => {
            const Icon = t.icon;
            return (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={`px-3 py-2.5 text-xs font-bold border-b-2 flex items-center gap-1.5 transition-colors ${
                  tab === t.id
                    ? 'border-[#0B5D3B] text-[#0B5D3B]'
                    : 'border-transparent text-gray-500 hover:text-gray-800'
                }`}
              >
                <Icon className="w-3.5 h-3.5" />
                {t.label}
              </button>
            );
          })}
        </div>

        <div className="p-5 space-y-4">
          {tab === 'profile' && (
            <>
              <div className="flex items-center gap-4">
                <Avatar name={name} src={avatar} size={72} />
                <div className="flex-1 space-y-1.5">
                  <input
                    ref={fileRef}
                    type="file"
                    accept="image/*"
                    className="hidden"
                    onChange={e => pickFile(e.target.files?.[0])}
                  />
                  <button
                    onClick={() => fileRef.current?.click()}
                    className="w-full px-3 py-2 text-xs font-bold bg-[#0B5D3B] hover:bg-[#08472d] text-white rounded-lg flex items-center justify-center gap-1.5"
                  >
                    <Camera className="w-3.5 h-3.5" />
                    {avatar ? 'Change picture' : 'Upload picture'}
                  </button>
                  {avatar && (
                    <button
                      onClick={() => setAvatar(null)}
                      className="w-full px-3 py-2 text-xs font-bold text-red-700 hover:bg-red-50 rounded-lg border border-red-200 flex items-center justify-center gap-1.5"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                      Remove
                    </button>
                  )}
                  <p className="text-[11px] text-gray-500 leading-snug">
                    Squared and shrunk to 256px before it is saved.
                  </p>
                </div>
              </div>

              <div>
                <label className={label}>Name</label>
                <input className={field} value={name} onChange={e => setName(e.target.value)} />
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label className={label}>Club</label>
                  <input className={field} value={club} onChange={e => setClub(e.target.value)} />
                </div>
                <div>
                  <label className={label}>City</label>
                  <input className={field} value={city} onChange={e => setCity(e.target.value)} />
                </div>
              </div>
              <div>
                <label className={label}>Phone</label>
                <input className={field} value={phone} onChange={e => setPhone(e.target.value)} />
              </div>

              <button
                onClick={saveProfile}
                disabled={busy}
                className="w-full px-4 py-2.5 text-sm font-bold bg-[#0B5D3B] hover:bg-[#08472d] text-white rounded-xl shadow-md flex items-center justify-center gap-1.5 disabled:opacity-40"
              >
                {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
                Save changes
              </button>
            </>
          )}

          {tab === 'email' && (
            <>
              <p className="text-xs text-gray-600">
                You currently sign in as <span className="font-bold">{user.email}</span>.
                Changing it changes the address you sign in with.
              </p>
              <div>
                <label className={label}>New email address</label>
                <input className={field} type="email" value={newEmail}
                       onChange={e => setNewEmail(e.target.value)} placeholder="name@example.com" />
              </div>
              <div>
                <label className={label}>Your current password</label>
                <input className={field} type="password" value={emailPassword}
                       onChange={e => setEmailPassword(e.target.value)} />
              </div>
              <button
                onClick={saveEmail}
                disabled={busy || !newEmail.trim() || !emailPassword}
                className="w-full px-4 py-2.5 text-sm font-bold bg-[#0B5D3B] hover:bg-[#08472d] text-white rounded-xl shadow-md flex items-center justify-center gap-1.5 disabled:opacity-40"
              >
                {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Mail className="w-4 h-4" />}
                Change email
              </button>
            </>
          )}

          {tab === 'password' && (
            <>
              <div>
                <label className={label}>Current password</label>
                <input className={field} type="password" value={currentPassword}
                       onChange={e => setCurrentPassword(e.target.value)} />
              </div>
              <div>
                <label className={label}>New password</label>
                <input className={field} type="password" value={newPassword}
                       onChange={e => setNewPassword(e.target.value)} />
                <p className="text-[11px] text-gray-500 mt-1">At least 6 characters.</p>
              </div>
              <div>
                <label className={label}>Confirm new password</label>
                <input className={field} type="password" value={confirmPassword}
                       onChange={e => setConfirmPassword(e.target.value)} />
                {confirmPassword && newPassword !== confirmPassword && (
                  <p className="text-[11px] text-red-600 mt-1">These do not match.</p>
                )}
              </div>
              <button
                onClick={savePassword}
                disabled={busy || !currentPassword || newPassword.length < 6 || newPassword !== confirmPassword}
                className="w-full px-4 py-2.5 text-sm font-bold bg-[#0B5D3B] hover:bg-[#08472d] text-white rounded-xl shadow-md flex items-center justify-center gap-1.5 disabled:opacity-40"
              >
                {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <KeyRound className="w-4 h-4" />}
                Change password
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
};
