import React, { useEffect, useState } from 'react';
import {
  ChevronLeft, Check, RotateCw, Target, Grid3x3, Trophy,
  Play, Timer, ArrowRight, Loader2, Calendar, Layers, Users,
} from 'lucide-react';
import { Tournament, Match } from '../../types/tournament';
import { useTournament } from '../../context/TournamentContext';
import { MatchTimer } from './MatchTimer';

type Step = 'intro' | 'spin' | 'result' | 'winner' | 'choice' | 'ready';
type Coin = 'black' | 'white';
type Choice = 'strike' | 'side';

interface MatchTossControlProps {
  tournament: Tournament;
  match: Match;
  onBack: () => void;
  onStarted: (warning?: string) => void;
}

const STEPS: { key: Step; label: string }[] = [
  { key: 'spin', label: 'Toss Time' },
  { key: 'winner', label: 'Winner' },
  { key: 'choice', label: 'Choice' },
  { key: 'ready', label: 'Start Match' },
];

/**
 * Umpire flow that runs before the first board: toss the coin, record who won
 * it and what they chose, then start the match and its timer.
 *
 * The choice is recorded rather than assumed because it decides who breaks,
 * and afterwards nobody can reconstruct it from the scores alone.
 */
export const MatchTossControl: React.FC<MatchTossControlProps> = ({
  tournament, match, onBack, onStarted,
}) => {
  const { recordToss, startMatch, refreshData } = useTournament();

  const [step, setStep] = useState<Step>('intro');
  const [spinning, setSpinning] = useState(false);
  const [coin, setCoin] = useState<Coin | null>(null);
  const [winnerId, setWinnerId] = useState<string>('');
  const [choice, setChoice] = useState<Choice>('strike');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const sides = [
    { id: match.player1Id, name: match.player1Name, badge: 'T1', tone: 'bg-[#1E3A8A]' },
    { id: match.player2Id, name: match.player2Name, badge: 'T2', tone: 'bg-[#BE185D]' },
  ];
  const winner = sides.find(s => s.id === winnerId);

  // Reopening a match whose toss is already recorded goes straight to the end.
  useEffect(() => {
    if (match.tossWinnerId) {
      setWinnerId(match.tossWinnerId);
      setChoice((match.tossChoice as Choice) || 'strike');
      setCoin((match.tossCoinResult as Coin) || null);
      setStep('ready');
    }
  }, [match.id]);

  const stepIndex = STEPS.findIndex(s => s.key === step);
  const doneThrough = (key: Step) => {
    const i = STEPS.findIndex(s => s.key === key);
    return stepIndex > i || step === 'ready' && key !== 'ready';
  };

  const spin = () => {
    setSpinning(true);
    setError('');
    // Settle on a side after the animation, so the result and the coin agree.
    setTimeout(() => {
      setCoin(Math.random() < 0.5 ? 'black' : 'white');
      setSpinning(false);
      setStep('result');
    }, 1400);
  };

  const saveAndStart = async () => {
    setBusy(true);
    setError('');
    let tossWarning = '';
    try {
      try {
        await recordToss(match.id, {
          coinResult: coin,
          tossWinnerId: winnerId || null,
          tossWinnerName: winner?.name || null,
          choice,
        });
      } catch (e: any) {
        // A database without migration 004 cannot store the toss. That is a
        // reason to lose the record, not a reason to block the match.
        if (!/004_match_toss|cannot be saved on this database/i.test(e?.message || '')) throw e;
        tossWarning = 'The toss could not be saved (migration 004 is not applied), '
                    + 'but the match has been started.';
      }
      if (match.status !== 'live') {
        await startMatch(tournament.id, match.id);
      } else {
        await refreshData();
      }
      onStarted(tossWarning || undefined);
    } catch (e: any) {
      setError(e?.message || 'Could not start the match.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="bg-white rounded-2xl border border-gray-200/80 shadow-xs overflow-hidden max-w-2xl mx-auto">
      {/* Header */}
      <div className="px-4 py-3 border-b border-gray-200 flex items-center gap-3">
        <button onClick={onBack} aria-label="Back" className="p-1 -ml-1 rounded-lg hover:bg-gray-100">
          <ChevronLeft className="w-5 h-5 text-gray-600" />
        </button>
        <h3 className="font-bold text-gray-900">Match #{match.matchNumber}</h3>
        <span className="ml-auto px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider bg-emerald-50 text-emerald-800 border border-emerald-200">
          {match.status}
        </span>
      </div>

      {/* Stepper — hidden on the intro screen, as in the flow */}
      {step !== 'intro' && (
        <div className="px-4 py-3 border-b border-gray-100 flex items-center gap-1 overflow-x-auto">
          {STEPS.map((s, i) => {
            const done = doneThrough(s.key);
            const active = s.key === step || (step === 'result' && s.key === 'winner');
            return (
              <div key={s.key} className="flex items-center gap-1 shrink-0">
                <span className={`w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold ${
                  done ? 'bg-emerald-600 text-white'
                       : active ? 'bg-[#0B5D3B] text-white'
                                : 'bg-gray-200 text-gray-500'
                }`}>
                  {done ? <Check className="w-3 h-3" /> : i + 1}
                </span>
                <span className={`text-[11px] font-semibold ${
                  done || active ? 'text-gray-900' : 'text-gray-400'
                }`}>{s.label}</span>
                {i < STEPS.length - 1 && <span className="w-4 h-px bg-gray-200 mx-1" />}
              </div>
            );
          })}
        </div>
      )}

      <div className="p-5">
        {error && (
          <div className="mb-4 p-3 rounded-xl bg-red-50 border border-red-200 text-xs text-red-800">
            {error}
          </div>
        )}

        {/* ---------------- intro ---------------- */}
        {step === 'intro' && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-gray-500">
              <span className="flex items-center gap-1">
                <Calendar className="w-3.5 h-3.5" />
                {match.scheduledDate || 'Today'} · {match.scheduledTime || '—'}
              </span>
              <span className="flex items-center gap-1"><Layers className="w-3.5 h-3.5" />Board {match.boardNumber}</span>
              <span className="flex items-center gap-1 capitalize"><Users className="w-3.5 h-3.5" />{match.type}</span>
            </div>

            <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-3">
              {[sides[0], null, sides[1]].map((side, i) =>
                side ? (
                  <div key={side.badge} className="p-3 rounded-xl bg-gray-50 border border-gray-200 text-center">
                    <div className={`w-9 h-9 rounded-full ${side.tone} text-white font-bold text-xs flex items-center justify-center mx-auto`}>
                      {side.badge}
                    </div>
                    <div className="mt-1.5 text-sm font-bold text-gray-900 truncate">{side.name}</div>
                  </div>
                ) : (
                  <span key="vs" className="text-xs font-bold text-gray-400">VS</span>
                )
              )}
            </div>

            <div className="p-4 rounded-xl bg-emerald-50/70 border border-emerald-200">
              <div className="flex items-start gap-2">
                <span className="w-5 h-5 rounded-full bg-[#0B5D3B] text-white text-[10px] font-bold flex items-center justify-center shrink-0">1</span>
                <div>
                  <div className="text-sm font-bold text-gray-900">Start Match</div>
                  <p className="text-[11px] text-gray-600">Begin the toss process to decide the first turn.</p>
                </div>
              </div>
              <button
                onClick={() => setStep('spin')}
                className="mt-3 w-full py-2.5 rounded-xl bg-[#0B5D3B] hover:bg-[#08472d] text-white text-sm font-bold flex items-center justify-center gap-2"
              >
                <Trophy className="w-4 h-4 text-[#D4A72C]" /> Start Toss
              </button>
            </div>
          </div>
        )}

        {/* ---------------- spin ---------------- */}
        {step === 'spin' && (
          <div className="text-center space-y-4 py-2">
            <div>
              <h4 className="text-xl font-serif font-bold text-gray-900">Toss Time</h4>
              <p className="text-xs text-gray-500">Click the coin to spin and get the result.</p>
            </div>
            <button
              onClick={spin}
              disabled={spinning}
              aria-label="Spin the coin"
              className="mx-auto block rounded-full focus:outline-hidden focus:ring-4 focus:ring-emerald-200"
            >
              <div
                className={`w-32 h-32 rounded-full border-4 border-gray-200 shadow-lg overflow-hidden ${
                  spinning ? 'animate-spin' : ''
                }`}
                style={{ background: 'linear-gradient(90deg, #202522 0 50%, #f5f5f4 50% 100%)' }}
              />
            </button>
            <p className="text-[11px] text-gray-500">{spinning ? 'Spinning…' : 'Tap to spin'}</p>
            <button
              onClick={spin}
              disabled={spinning}
              className="px-5 py-2.5 rounded-xl bg-[#0B5D3B] hover:bg-[#08472d] text-white text-sm font-bold inline-flex items-center gap-2 disabled:opacity-60"
            >
              {spinning ? <Loader2 className="w-4 h-4 animate-spin" /> : <RotateCw className="w-4 h-4" />}
              Spin Coin
            </button>
          </div>
        )}

        {/* ---------------- result ---------------- */}
        {step === 'result' && coin && (
          <div className="text-center space-y-4 py-2">
            <div>
              <h4 className="text-xl font-serif font-bold text-gray-900">Toss Result</h4>
              <p className="text-xs text-gray-500">The coin landed on…</p>
            </div>
            <div
              className="w-28 h-28 rounded-full mx-auto border-4 border-gray-200 shadow-lg"
              style={{ background: coin === 'black' ? '#202522' : '#f5f5f4' }}
            />
            <div className="text-2xl font-serif font-bold capitalize text-gray-900">{coin}</div>

            <div className="p-3 rounded-xl bg-emerald-50 border border-emerald-200 text-left flex items-start gap-2">
              <Trophy className="w-4 h-4 text-emerald-700 mt-0.5 shrink-0" />
              <div>
                <div className="text-xs font-bold text-emerald-900">
                  <span className="capitalize">{coin}</span> won the toss
                </div>
                <p className="text-[11px] text-emerald-800">Now select the player who won the toss.</p>
              </div>
            </div>

            <div className="flex justify-end gap-2">
              <button onClick={spin} className="px-3 py-2 text-xs font-semibold text-gray-600 hover:bg-gray-100 rounded-lg">
                Spin again
              </button>
              <button
                onClick={() => setStep('winner')}
                className="px-4 py-2 rounded-xl bg-[#0B5D3B] hover:bg-[#08472d] text-white text-sm font-bold inline-flex items-center gap-1.5"
              >
                Next <ArrowRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}

        {/* ---------------- winner ---------------- */}
        {step === 'winner' && (
          <div className="space-y-4">
            <div className="text-center">
              <h4 className="text-lg font-serif font-bold text-gray-900">Select Player Who Won the Toss</h4>
              <p className="text-xs text-gray-500">Choose the player/team who won the toss.</p>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {sides.map(side => {
                const selected = winnerId === side.id;
                return (
                  <button
                    key={side.badge}
                    type="button"
                    disabled={!side.id}
                    onClick={() => side.id && setWinnerId(side.id)}
                    className={`relative p-4 rounded-xl border-2 text-center transition-colors disabled:opacity-40 ${
                      selected ? 'border-[#0B5D3B] bg-emerald-50/60' : 'border-gray-200 hover:border-gray-300'
                    }`}
                  >
                    <span className={`absolute top-2.5 right-2.5 w-4 h-4 rounded-full border-2 ${
                      selected ? 'border-[#0B5D3B] bg-[#0B5D3B]' : 'border-gray-300'
                    }`} />
                    <div className={`w-11 h-11 rounded-full ${side.tone} text-white font-bold text-sm flex items-center justify-center mx-auto`}>
                      {side.badge}
                    </div>
                    <div className="mt-2 text-sm font-bold text-gray-900 truncate">{side.name}</div>
                  </button>
                );
              })}
            </div>

            <div className="flex justify-end">
              <button
                onClick={() => setStep('choice')}
                disabled={!winnerId}
                className="px-4 py-2 rounded-xl bg-[#0B5D3B] hover:bg-[#08472d] text-white text-sm font-bold inline-flex items-center gap-1.5 disabled:opacity-40"
              >
                Next <ArrowRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}

        {/* ---------------- choice ---------------- */}
        {step === 'choice' && (
          <div className="space-y-4">
            <div className="text-center">
              <h4 className="text-lg font-serif font-bold text-gray-900">Choose Strike or Side</h4>
              <p className="text-xs text-gray-500">Select what the winning team wants.</p>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {([
                { key: 'strike' as Choice, icon: Target,
                  title: 'Strike', body: 'Take the striker and break first.' },
                { key: 'side' as Choice, icon: Grid3x3,
                  title: 'Side', body: 'Choose the side (first shot from side).' },
              ]).map(opt => {
                const selected = choice === opt.key;
                const Icon = opt.icon;
                return (
                  <button
                    key={opt.key}
                    type="button"
                    onClick={() => setChoice(opt.key)}
                    className={`relative p-4 rounded-xl border-2 text-center transition-colors ${
                      selected ? 'border-[#0B5D3B] bg-emerald-50/60' : 'border-gray-200 hover:border-gray-300'
                    }`}
                  >
                    <span className={`absolute top-2.5 right-2.5 w-4 h-4 rounded-full border-2 ${
                      selected ? 'border-[#0B5D3B] bg-[#0B5D3B]' : 'border-gray-300'
                    }`} />
                    <Icon className={`w-8 h-8 mx-auto ${selected ? 'text-[#0B5D3B]' : 'text-gray-400'}`} />
                    <div className="mt-2 text-sm font-bold text-gray-900">{opt.title}</div>
                    <p className="text-[11px] text-gray-500 mt-0.5">{opt.body}</p>
                  </button>
                );
              })}
            </div>

            <div className="flex justify-end">
              <button
                onClick={() => setStep('ready')}
                className="px-4 py-2 rounded-xl bg-[#0B5D3B] hover:bg-[#08472d] text-white text-sm font-bold inline-flex items-center gap-1.5"
              >
                Next <ArrowRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}

        {/* ---------------- ready ---------------- */}
        {step === 'ready' && (
          <div className="space-y-4">
            <div className="text-center">
              <h4 className="text-lg font-serif font-bold text-gray-900">Match Ready</h4>
              <p className="text-xs text-gray-500">Toss completed. The match will begin now.</p>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="p-3 rounded-xl bg-gray-50 border border-gray-200">
                <div className="text-[10px] font-bold uppercase tracking-wider text-gray-500 text-center">Toss Winner</div>
                <div className="mt-1.5 flex items-center justify-center gap-2">
                  <span className={`w-7 h-7 rounded-full ${winner?.tone || 'bg-gray-400'} text-white text-[10px] font-bold flex items-center justify-center`}>
                    {winner?.badge || '—'}
                  </span>
                  <span className="text-xs font-bold text-gray-900 truncate">{winner?.name || 'Not recorded'}</span>
                </div>
              </div>
              <div className="p-3 rounded-xl bg-gray-50 border border-gray-200">
                <div className="text-[10px] font-bold uppercase tracking-wider text-gray-500 text-center">Choice</div>
                <div className="mt-1.5 flex items-center justify-center gap-2">
                  {choice === 'strike'
                    ? <Target className="w-5 h-5 text-[#0B5D3B]" />
                    : <Grid3x3 className="w-5 h-5 text-[#0B5D3B]" />}
                  <span className="text-xs font-bold text-gray-900 capitalize">{choice}</span>
                </div>
              </div>
            </div>

            <div className="p-4 rounded-xl bg-emerald-50/70 border border-emerald-200 text-center">
              <div className="text-[11px] font-bold uppercase tracking-wider text-emerald-800 flex items-center justify-center gap-1.5">
                <Timer className="w-3.5 h-3.5" /> Match Timer
              </div>
              <MatchTimer match={match} className="text-3xl font-black text-gray-900 tabular-nums mt-1" />
              <button
                onClick={saveAndStart}
                disabled={busy}
                className="mt-3 w-full py-3 rounded-xl bg-[#0B5D3B] hover:bg-[#08472d] text-white text-sm font-bold flex items-center justify-center gap-2 disabled:opacity-50"
              >
                {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
                {match.status === 'live' ? 'Go to scoring' : 'Start Match'}
              </button>
            </div>

            <button
              onClick={() => setStep('spin')}
              className="w-full text-[11px] text-gray-500 hover:text-gray-700"
            >
              Redo the toss
            </button>
          </div>
        )}
      </div>
    </div>
  );
};
