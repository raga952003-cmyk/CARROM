import React, { useState } from 'react';
import { apiClient } from '../../utils/apiClient';
import { 
  Trophy, 
  ShieldCheck, 
  Mail, 
  Lock, 
  User, 
  Phone, 
  MapPin, 
  Building, 
  KeyRound, 
  Database,
  ArrowRight,
  UserPlus,
  Unlock,
  AlertTriangle
} from 'lucide-react';
import { useTournament } from '../../context/TournamentContext';

export const AuthPortal: React.FC = () => {
  const {
    isConfigured, signInUser, signUpUser, allPlayers,
    sessionNotice, clearSessionNotice
  } = useTournament();

  const [activeRole, setActiveRole] = useState<'player' | 'admin'>('player');
  const [authMode, setAuthMode] = useState<'signin' | 'signup'>('signin');
  const [errorMsg, setErrorMsg] = useState('');
  const [loading, setLoading] = useState(false);
  const [forgotSent, setForgotSent] = useState(false);
  const [forgotNote, setForgotNote] = useState('');

  // Form Fields
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [phone, setPhone] = useState('');
  const [club, setClub] = useState('');
  const [city, setCity] = useState('');

  const handleAuthSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMsg('');
    setLoading(true);

    try {
      if (authMode === 'signin') {
        const res = await signInUser(email, password, activeRole);
        if (!res.success) {
          setErrorMsg(res.error || 'Invalid credentials');
        }
      } else {
        // The chosen tab is the role, and it is sent. There used to be a
        // security key checked here against a literal in this very file --
        // which is not a check: the string shipped in the bundle, the
        // comparison passed on an EMPTY key anyway, and the role it was
        // guarding was discarded before the request went out, so registering
        // as an admin produced a player account and signed you into it
        // without a word.
        const metadata = {
          name,
          phone,
          club,
          city
        };

        const res = await signUpUser(email, password, activeRole, metadata);
        if (res.success) {
          // Signing up signs you in; it always did, and the message telling
          // people to go and log in was never reachable -- the app had already
          // moved to their dashboard behind it.
          setPassword('');
        } else {
          setErrorMsg(res.error || 'Failed to create account');
        }
      }
    } catch (err: any) {
      setErrorMsg(err.message || 'An unexpected error occurred');
    } finally {
      setLoading(false);
    }
  };

  // Helper to quickly fill sample credentials
  const fillSampleCredentials = (roleType: 'admin' | 'player', sampleEmail?: string) => {
    setErrorMsg('');
    setActiveRole(roleType);
    setAuthMode('signin');
    if (roleType === 'admin') {
      setEmail('admin@carrom.com');
      setPassword('admin');
    } else {
      setEmail(sampleEmail || 'rohit.deshmukh@sports.in');
      setPassword('player123'); // standard mock password
    }
  };

  return (
    <div className="flex-1 flex flex-col items-center justify-center py-12 px-4 sm:px-6 lg:px-8 bg-[#F8F6F0]">
      <div className="max-w-md w-full space-y-6">
        
        {/* Brand Banner */}
        <div className="text-center">
          <div className="mx-auto w-12 h-12 rounded-full bg-[#D4A72C] flex items-center justify-center text-[#0B5D3B] font-black text-2xl shadow-md border-2 border-white transform -rotate-3">
            C
          </div>
          <h2 className="mt-3 text-3xl font-serif font-bold text-gray-900 tracking-tight">
            Carrom Pro Tournament
          </h2>
          <p className="mt-1 text-xs text-gray-500 font-medium">
            Official Scoring & Tournament Command Center
          </p>
        </div>

        {/* Shown only when the app is NOT wired up. A working connection is the
            expected state and needs no announcement; saying so on the sign-in
            page told the operator something they already assumed and put the
            word "Supabase" in front of players. The misconfigured case stays,
            because that one is actionable. */}
        {!isConfigured && (
          <div className="p-3.5 rounded-2xl border text-xs flex items-start gap-3 shadow-xs bg-amber-50 border-amber-200 text-amber-900">
            <AlertTriangle className="w-5 h-5 text-amber-600 shrink-0 mt-0.5" />
            <div>
              <span className="font-bold block">Running in Demo Mode (Local Storage)</span>
              Supabase URL &amp; Key not detected in `.env`. Credentials and changes are stored locally in your browser.
              <span className="block mt-1 text-[10px] text-amber-700 italic">
                To connect your Supabase project, write your keys in the `.env` file.
              </span>
            </div>
          </div>
        )}

        {/* Main Tabbed Card */}
        <div className="bg-white rounded-3xl border border-gray-200/80 shadow-lg overflow-hidden">
          
          {/* Tab Switcher: Player vs Admin */}
          <div className="flex border-b border-gray-200 bg-gray-50">
            <button
              onClick={() => {
                setActiveRole('player');
                setErrorMsg('');
              }}
              className={`flex-1 py-3 text-xs font-bold flex items-center justify-center gap-1.5 border-b-2 transition-all ${
                activeRole === 'player'
                  ? 'border-[#0B5D3B] text-[#0B5D3B] bg-white'
                  : 'border-transparent text-gray-500 hover:text-gray-800'
              }`}
            >
              <Trophy className="w-4 h-4 text-[#D4A72C]" />
              <span>Player Portal</span>
            </button>
            <button
              onClick={() => {
                setActiveRole('admin');
                setErrorMsg('');
              }}
              className={`flex-1 py-3 text-xs font-bold flex items-center justify-center gap-1.5 border-b-2 transition-all ${
                activeRole === 'admin'
                  ? 'border-[#0B5D3B] text-[#0B5D3B] bg-white'
                  : 'border-transparent text-gray-500 hover:text-gray-800'
              }`}
            >
              <ShieldCheck className="w-4 h-4 text-[#D4A72C]" />
              <span>Admin Console</span>
            </button>
          </div>

          <div className="p-6 sm:p-8 space-y-5">
            
            {/* Inner Header */}
            <div className="text-center">
              <h3 className="text-base font-bold text-gray-900 capitalize">
                {activeRole} {authMode === 'signin' ? 'Sign In' : 'Account Registration'}
              </h3>
              <p className="text-[11px] text-gray-500 mt-0.5">
                {activeRole === 'admin' 
                  ? 'Manage tournaments, update board scores, and publish schedules.' 
                  : 'Discover championships, register entries, and follow assigned boards.'}
              </p>
            </div>

            {/* Notifications alerts */}
            {sessionNotice && !errorMsg && (
                <div className="mb-3 p-2.5 rounded-xl bg-amber-50 border border-amber-200 text-[11px] text-amber-900 flex items-start gap-2">
                  <span className="font-bold shrink-0">Signed out:</span>
                  <span className="flex-1">{sessionNotice}</span>
                  <button
                    type="button"
                    onClick={clearSessionNotice}
                    className="text-amber-700 hover:text-amber-900 font-bold shrink-0"
                    aria-label="Dismiss"
                  >
                    ×
                  </button>
                </div>
              )}
              {errorMsg && (
              <div className="p-3 bg-red-50 border border-red-200 text-red-800 text-[11px] font-medium rounded-xl flex items-center gap-2">
                <span className="w-1.5 h-1.5 rounded-full bg-red-600 shrink-0" />
                <span>{errorMsg}</span>
              </div>
            )}

            {/* Authentication Form */}
            <form onSubmit={handleAuthSubmit} className="space-y-3.5 text-xs text-left">
              
              {authMode === 'signup' && (
                <>
                  <div>
                    <label className="block font-bold text-gray-700 mb-1">Full Name *</label>
                    <div className="relative">
                      <User className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
                      <input
                        type="text"
                        value={name}
                        onChange={e => setName(e.target.value)}
                        required
                        className="w-full pl-9 pr-3 py-2 border border-gray-200 rounded-xl focus:ring-2 focus:ring-[#0B5D3B]"
                        placeholder="Enter full name"
                      />
                    </div>
                  </div>

                  {activeRole === 'player' && (
                    <div className="grid grid-cols-2 gap-2.5">
                      <div>
                        <label className="block font-bold text-gray-700 mb-1">Carrom Club</label>
                        <div className="relative">
                          <Building className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
                          <input
                            type="text"
                            value={club}
                            onChange={e => setClub(e.target.value)}
                            className="w-full pl-9 pr-3 py-2 border border-gray-200 rounded-xl focus:ring-2 focus:ring-[#0B5D3B]"
                            placeholder="Deccan Club"
                          />
                        </div>
                      </div>
                      <div>
                        <label className="block font-bold text-gray-700 mb-1">City</label>
                        <div className="relative">
                          <MapPin className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
                          <input
                            type="text"
                            value={city}
                            onChange={e => setCity(e.target.value)}
                            className="w-full pl-9 pr-3 py-2 border border-gray-200 rounded-xl focus:ring-2 focus:ring-[#0B5D3B]"
                            placeholder="City"
                          />
                        </div>
                      </div>
                    </div>
                  )}

                  {activeRole === 'player' && (
                    <div>
                      <label className="block font-bold text-gray-700 mb-1">Contact Number</label>
                      <div className="relative">
                        <Phone className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
                        <input
                          type="tel"
                          value={phone}
                          onChange={e => setPhone(e.target.value)}
                          className="w-full pl-9 pr-3 py-2 border border-gray-200 rounded-xl focus:ring-2 focus:ring-[#0B5D3B]"
                          placeholder="e.g. 9876543210"
                        />
                      </div>
                    </div>
                  )}


                </>
              )}

              <div>
                <label className="block font-bold text-gray-700 mb-1">Email Address *</label>
                <div className="relative">
                  <Mail className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
                  <input
                    type="email"
                    value={email}
                    onChange={e => setEmail(e.target.value)}
                    required
                    autoComplete="email"
                    className="w-full pl-9 pr-3 py-2 border border-gray-200 rounded-xl focus:ring-2 focus:ring-[#0B5D3B]"
                    placeholder="name@example.com"
                  />
                </div>
              </div>

              <div>
                <label className="block font-bold text-gray-700 mb-1">Password *</label>
                <div className="relative">
                  <Lock className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
                  <input
                    type="password"
                    value={password}
                    onChange={e => setPassword(e.target.value)}
                    required
                    autoComplete={authMode === 'signin' ? 'current-password' : 'new-password'}
                    className="w-full pl-9 pr-3 py-2 border border-gray-200 rounded-xl focus:ring-2 focus:ring-[#0B5D3B]"
                    placeholder="Enter password"
                  />
                </div>
              </div>

              {/* Submit CTA */}
              <button
                type="submit"
                disabled={loading}
                className="w-full py-2.5 bg-[#0B5D3B] hover:bg-[#094e32] text-white text-xs font-bold rounded-xl shadow-md transition-all flex items-center justify-center gap-1.5"
              >
                {loading ? (
                  <span>Processing...</span>
                ) : (
                  <>
                    <span>{authMode === 'signin' ? 'Sign In' : 'Register Account'}</span>
                    <ArrowRight className="w-4 h-4" />
                  </>
                )}
              </button>
            </form>

            {/* Locked out. Every account made from a sheet import was given a
                random password nobody kept, so this is not an edge case here --
                it is the only way most people get in. */}
            {authMode === 'signin' && (
              <div className="text-center">
                {forgotSent ? (
                  <p className="text-[11px] text-emerald-800 bg-emerald-50 border border-emerald-200 rounded-lg px-3 py-2">
                    If that address has an account, a reset link is on its way.
                  </p>
                ) : (
                  <button
                    type="button"
                    onClick={async () => {
                      if (!email.trim()) { setForgotNote('Enter your email address first.'); return; }
                      setForgotNote('');
                      try {
                        await apiClient.post('/auth/forgot-password', { email: email.trim() });
                        setForgotSent(true);
                      } catch {
                        // The endpoint answers the same either way; a network
                        // failure is the only thing that reaches here.
                        setForgotNote('Could not send it. Check your connection and try again.');
                      }
                    }}
                    className="text-[11px] text-[#0B5D3B] hover:underline font-semibold"
                  >
                    Forgot your password?
                  </button>
                )}
                {forgotNote && (
                  <p className="text-[11px] text-amber-800 mt-1">{forgotNote}</p>
                )}
              </div>
            )}

            {/* Register Toggle Switch */}
            <div className="text-center text-[11px] text-gray-500 pt-2 border-t border-gray-100 flex items-center justify-between">
              <span>
                {authMode === 'signin' 
                  ? "Don't have an account?" 
                  : "Already have an account?"}
              </span>
              <button
                type="button"
                onClick={() => {
                  setAuthMode(authMode === 'signin' ? 'signup' : 'signin');
                  setErrorMsg('');
                }}
                className="text-[#0B5D3B] font-bold hover:underline capitalize"
              >
                {authMode === 'signin' ? 'Register here' : 'Sign in here'}
              </button>
            </div>



          </div>

        </div>

      </div>
    </div>
  );
};
