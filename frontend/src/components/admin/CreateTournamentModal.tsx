import React, { useState } from 'react';
import { 
  X, 
  Trophy, 
  Calendar, 
  MapPin, 
  Layers, 
  Clock, 
  Award, 
  Settings2, 
  Check, 
  Sparkles, 
  HelpCircle,
  ShieldCheck
} from 'lucide-react';
import { TournamentFormat, MatchType, TournamentRules } from '../../types/tournament';
import { useTournament } from '../../context/TournamentContext';

interface CreateTournamentModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export const CreateTournamentModal: React.FC<CreateTournamentModalProps> = ({
  isOpen,
  onClose
}) => {
  const { createTournament, publishTournament } = useTournament();

  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [category, setCategory] = useState<'singles' | 'doubles' | 'both'>('both');
  const [format, setFormat] = useState<TournamentFormat>('league_knockout');
  const [venue, setVenue] = useState('City Sports Arena');
  const [city, setCity] = useState('Pune');
  const [numberOfBoards, setNumberOfBoards] = useState(4);
  const [entryFee, setEntryFee] = useState(500);
  const [prizePool, setPrizePool] = useState('₹50,000 + Trophies');

  // Dates
  const [regStart, setRegStart] = useState('2026-08-25');
  const [regEnd, setRegEnd] = useState('2026-09-05');
  const [tourStart, setTourStart] = useState('2026-09-10');
  const [tourEnd, setTourEnd] = useState('2026-09-15');

  // Rules
  const [pointsForWin, setPointsForWin] = useState(2);
  const [pointsForDraw, setPointsForDraw] = useState(1);
  const [pointsForLoss, setPointsForLoss] = useState(0);
  const [maxBoards, setMaxBoards] = useState(3);
  const [targetScore, setTargetScore] = useState(29);
  const [queenPoints, setQueenPoints] = useState(3);
  const [matchDuration, setMatchDuration] = useState(30);
  const [restTime, setRestTime] = useState(10);

  const [activeTab, setActiveTab] = useState<'basic' | 'rules'>('basic');

  if (!isOpen) return null;

  const handleSave = async (publishImmediately: boolean = false) => {
    if (!name.trim()) {
      alert('Please enter a tournament name.');
      return;
    }

    const rules: TournamentRules = {
      pointsForWin,
      pointsForDraw,
      pointsForLoss,
      maxBoardsPerMatch: maxBoards,
      targetScore,
      queenPoints,
      matchDurationMinutes: matchDuration,
      restTimeMinutes: restTime,
      tiebreakerRules: ['points', 'board_difference', 'net_score_difference', 'head_to_head']
    };

    const newId = await createTournament({
      name,
      description: description || 'Official Carrom Championship tournament featuring automated scoring, fixtures, and standings.',
      category,
      format,
      registrationStartDate: regStart,
      registrationEndDate: regEnd,
      tournamentStartDate: tourStart,
      tournamentEndDate: tourEnd,
      venue,
      city,
      numberOfBoards,
      entryFee,
      prizePool,
      rules,
      status: publishImmediately ? 'registration_open' : 'draft'
    });

    if (publishImmediately) {
      await publishTournament(newId);
    }

    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-black/60 backdrop-blur-xs flex items-center justify-center p-4 animate-in fade-in duration-150">
      <div className="relative bg-white rounded-2xl max-w-3xl w-full max-h-[90vh] flex flex-col shadow-2xl border border-gray-100 overflow-hidden">
        
        {/* Header */}
        <div className="px-6 py-4 bg-[#0B5D3B] text-white flex items-center justify-between border-b border-emerald-800">
          <div className="flex items-center space-x-2">
            <div className="p-1.5 bg-[#D4A72C] rounded-lg text-[#202522]">
              <Trophy className="w-5 h-5" />
            </div>
            <div>
              <h2 className="font-serif font-bold text-lg">Create New Tournament</h2>
              <p className="text-xs text-emerald-100">Configure parameters, boards, formats, and official scoring rules</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded-lg text-emerald-200 hover:text-white hover:bg-emerald-900 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Tab Toggle */}
        <div className="px-6 pt-3 bg-gray-50 border-b border-gray-200 flex space-x-3">
          <button
            type="button"
            onClick={() => setActiveTab('basic')}
            className={`pb-2 text-xs font-bold border-b-2 transition-all ${
              activeTab === 'basic'
                ? 'border-[#0B5D3B] text-[#0B5D3B]'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            1. Details & Schedule Setup
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('rules')}
            className={`pb-2 text-xs font-bold border-b-2 transition-all ${
              activeTab === 'rules'
                ? 'border-[#0B5D3B] text-[#0B5D3B]'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            2. Carrom Federation Scoring & Rules
          </button>
        </div>

        {/* Form Body */}
        <div className="flex-1 overflow-y-auto p-6 space-y-5">
          {activeTab === 'basic' ? (
            <div className="space-y-4">
              
              {/* Tournament Name */}
              <div>
                <label className="block text-xs font-bold text-gray-700 mb-1">
                  Tournament Name *
                </label>
                <input
                  type="text"
                  value={name}
                  onChange={e => setName(e.target.value)}
                  className="w-full text-xs px-3 py-2.5 border border-gray-200 rounded-xl focus:ring-2 focus:ring-[#0B5D3B] focus:border-transparent font-medium"
                  placeholder="e.g. Pune Carrom Championship 2026"
                  required
                />
              </div>

              {/* Description */}
              <div>
                <label className="block text-xs font-bold text-gray-700 mb-1">
                  Tournament Description
                </label>
                <textarea
                  value={description}
                  onChange={e => setDescription(e.target.value)}
                  rows={2}
                  className="w-full text-xs px-3 py-2 border border-gray-200 rounded-xl focus:ring-2 focus:ring-[#0B5D3B]"
                  placeholder="Describe the championship, ranking points, prize pool, or eligibility..."
                />
              </div>

              {/* Category & Format */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-bold text-gray-700 mb-1">
                    Event Category
                  </label>
                  <select
                    value={category}
                    onChange={e => setCategory(e.target.value as any)}
                    className="w-full text-xs px-3 py-2.5 border border-gray-200 rounded-xl focus:ring-2 focus:ring-[#0B5D3B] bg-white font-medium"
                  >
                    <option value="singles">Singles Only</option>
                    <option value="doubles">Doubles Only</option>
                    <option value="both">Singles & Doubles</option>
                  </select>
                </div>

                <div>
                  <label className="block text-xs font-bold text-gray-700 mb-1">
                    Tournament Format
                  </label>
                  <select
                    value={format}
                    onChange={e => setFormat(e.target.value as any)}
                    className="w-full text-xs px-3 py-2.5 border border-gray-200 rounded-xl focus:ring-2 focus:ring-[#0B5D3B] bg-white font-medium"
                  >
                    <option value="league_knockout">League + Knockout (Hybrid)</option>
                    <option value="round_robin">League / Round Robin</option>
                    <option value="knockout">Single Elimination Knockout</option>
                  </select>
                </div>
              </div>

              {/* Venue & City */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-bold text-gray-700 mb-1">
                    Venue Name
                  </label>
                  <div className="relative">
                    <MapPin className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
                    <input
                      type="text"
                      value={venue}
                      onChange={e => setVenue(e.target.value)}
                      className="w-full text-xs pl-9 pr-3 py-2.5 border border-gray-200 rounded-xl focus:ring-2 focus:ring-[#0B5D3B]"
                      placeholder="e.g. City Sports Arena, Hall B"
                    />
                  </div>
                </div>

                <div>
                  <label className="block text-xs font-bold text-gray-700 mb-1">
                    City / Location
                  </label>
                  <input
                    type="text"
                    value={city}
                    onChange={e => setCity(e.target.value)}
                    className="w-full text-xs px-3 py-2.5 border border-gray-200 rounded-xl focus:ring-2 focus:ring-[#0B5D3B]"
                    placeholder="e.g. Pune"
                  />
                </div>
              </div>

              {/* Number of Boards & Match Duration */}
              <div className="grid grid-cols-1 sm:grid-cols-4 gap-3 bg-emerald-50/50 p-3.5 rounded-xl border border-emerald-100">
                <div>
                  <label className="block text-[11px] font-bold text-emerald-950 mb-1">
                    Available Boards
                  </label>
                  <input
                    type="number"
                    min={1}
                    max={16}
                    value={numberOfBoards}
                    onChange={e => setNumberOfBoards(Math.max(1, parseInt(e.target.value) || 1))}
                    className="w-full text-xs px-3 py-2 border border-emerald-200 rounded-lg bg-white font-bold text-emerald-900"
                  />
                </div>

                <div>
                  <label className="block text-[11px] font-bold text-emerald-950 mb-1">
                    Match Duration (min)
                  </label>
                  <input
                    type="number"
                    min={10}
                    max={120}
                    value={matchDuration}
                    onChange={e => setMatchDuration(parseInt(e.target.value) || 30)}
                    className="w-full text-xs px-3 py-2 border border-emerald-200 rounded-lg bg-white font-bold text-emerald-900"
                  />
                </div>

                <div>
                  <label className="block text-[11px] font-bold text-emerald-950 mb-1">
                    Entry Fee (₹)
                  </label>
                  <input
                    type="number"
                    min={0}
                    value={entryFee}
                    onChange={e => setEntryFee(parseInt(e.target.value) || 0)}
                    className="w-full text-xs px-3 py-2 border border-emerald-200 rounded-lg bg-white font-bold text-emerald-900"
                  />
                </div>

                <div>
                  <label className="block text-[11px] font-bold text-emerald-950 mb-1">
                    Prize Pool
                  </label>
                  <input
                    type="text"
                    value={prizePool}
                    onChange={e => setPrizePool(e.target.value)}
                    className="w-full text-xs px-3 py-2 border border-emerald-200 rounded-lg bg-white font-bold text-emerald-900"
                    placeholder="e.g. ₹50,000"
                  />
                </div>
              </div>

              {/* Dates */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 pt-2">
                <div className="p-3 bg-gray-50 rounded-xl border border-gray-200">
                  <div className="text-xs font-bold text-gray-800 mb-2 flex items-center gap-1.5">
                    <Calendar className="w-3.5 h-3.5 text-blue-600" />
                    Registration Window
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <span className="text-[10px] text-gray-500">Opens</span>
                      <input
                        type="date"
                        value={regStart}
                        onChange={e => setRegStart(e.target.value)}
                        className="w-full text-xs p-1.5 border border-gray-200 rounded-lg bg-white"
                      />
                    </div>
                    <div>
                      <span className="text-[10px] text-gray-500">Closes</span>
                      <input
                        type="date"
                        value={regEnd}
                        onChange={e => setRegEnd(e.target.value)}
                        className="w-full text-xs p-1.5 border border-gray-200 rounded-lg bg-white"
                      />
                    </div>
                  </div>
                </div>

                <div className="p-3 bg-gray-50 rounded-xl border border-gray-200">
                  <div className="text-xs font-bold text-gray-800 mb-2 flex items-center gap-1.5">
                    <Trophy className="w-3.5 h-3.5 text-[#D4A72C]" />
                    Tournament Window
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <span className="text-[10px] text-gray-500">Starts</span>
                      <input
                        type="date"
                        value={tourStart}
                        onChange={e => setTourStart(e.target.value)}
                        className="w-full text-xs p-1.5 border border-gray-200 rounded-lg bg-white"
                      />
                    </div>
                    <div>
                      <span className="text-[10px] text-gray-500">Ends</span>
                      <input
                        type="date"
                        value={tourEnd}
                        onChange={e => setTourEnd(e.target.value)}
                        className="w-full text-xs p-1.5 border border-gray-200 rounded-lg bg-white"
                      />
                    </div>
                  </div>
                </div>
              </div>

            </div>
          ) : (
            <div className="space-y-4">
              
              {/* Official Carrom Points System */}
              <div className="bg-amber-50/70 p-4 rounded-xl border border-amber-200/80">
                <div className="flex items-center space-x-2 text-amber-950 font-bold text-xs mb-1">
                  <ShieldCheck className="w-4 h-4 text-[#0B5D3B]" />
                  <span>Official AICF Standard Points Rules</span>
                </div>
                <p className="text-[11px] text-amber-800 mb-3">
                  Match points are awarded to determine league standings and tiebreaker seeds.
                </p>

                <div className="grid grid-cols-3 gap-3">
                  <div>
                    <label className="block text-[10px] font-bold text-gray-700 mb-1">
                      Points for Win
                    </label>
                    <input
                      type="number"
                      value={pointsForWin}
                      onChange={e => setPointsForWin(parseInt(e.target.value) || 0)}
                      className="w-full text-xs px-3 py-2 border border-gray-200 rounded-lg bg-white font-bold"
                    />
                  </div>
                  <div>
                    <label className="block text-[10px] font-bold text-gray-700 mb-1">
                      Points for Draw
                    </label>
                    <input
                      type="number"
                      value={pointsForDraw}
                      onChange={e => setPointsForDraw(parseInt(e.target.value) || 0)}
                      className="w-full text-xs px-3 py-2 border border-gray-200 rounded-lg bg-white font-bold"
                    />
                  </div>
                  <div>
                    <label className="block text-[10px] font-bold text-gray-700 mb-1">
                      Points for Loss
                    </label>
                    <input
                      type="number"
                      value={pointsForLoss}
                      onChange={e => setPointsForLoss(parseInt(e.target.value) || 0)}
                      className="w-full text-xs px-3 py-2 border border-gray-200 rounded-lg bg-white font-bold"
                    />
                  </div>
                </div>
              </div>

              {/* Board Configuration */}
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div className="p-3 bg-gray-50 rounded-xl border border-gray-200">
                  <label className="block text-xs font-bold text-gray-700 mb-1">
                    Boards per Match
                  </label>
                  <select
                    value={maxBoards}
                    onChange={e => setMaxBoards(parseInt(e.target.value) || 3)}
                    className="w-full text-xs px-3 py-2 border border-gray-200 rounded-lg bg-white"
                  >
                    <option value={1}>1 Board</option>
                    <option value={3}>Best of 3 Boards</option>
                    <option value={5}>Best of 5 Boards</option>
                    <option value={8}>8 Boards (Federation Limit)</option>
                  </select>
                </div>

                <div className="p-3 bg-gray-50 rounded-xl border border-gray-200">
                  <label className="block text-xs font-bold text-gray-700 mb-1">
                    Target Score Limit
                  </label>
                  <select
                    value={targetScore}
                    onChange={e => setTargetScore(parseInt(e.target.value) || 29)}
                    className="w-full text-xs px-3 py-2 border border-gray-200 rounded-lg bg-white"
                  >
                    <option value={29}>29 Points (Standard)</option>
                    <option value={25}>25 Points</option>
                    <option value={21}>21 Points</option>
                  </select>
                </div>

                <div className="p-3 bg-gray-50 rounded-xl border border-gray-200">
                  <label className="block text-xs font-bold text-gray-700 mb-1">
                    Queen Value
                  </label>
                  <select
                    value={queenPoints}
                    onChange={e => setQueenPoints(parseInt(e.target.value) || 3)}
                    className="w-full text-xs px-3 py-2 border border-gray-200 rounded-lg bg-white"
                  >
                    <option value={3}>3 Points (If under 21 pts)</option>
                    <option value={1}>1 Point</option>
                  </select>
                </div>
              </div>

              {/* Tiebreaker Rules Hierarchy */}
              <div className="p-4 bg-gray-50 rounded-xl border border-gray-200">
                <h4 className="text-xs font-bold text-gray-800 mb-1.5 flex items-center gap-1">
                  <Settings2 className="w-3.5 h-3.5 text-emerald-700" />
                  Deterministic Standings & Tiebreaker Hierarchy
                </h4>
                <p className="text-[11px] text-gray-600 mb-2">
                  When two or more players have equal match points, standings are automatically resolved in strict order:
                </p>
                <ol className="list-decimal list-inside text-xs text-gray-700 space-y-1 font-medium bg-white p-3 rounded-lg border border-gray-200">
                  <li>Total Tournament Match Points</li>
                  <li>Board Wins Difference (Boards Won - Boards Lost)</li>
                  <li>Net Score Difference (Total Points For - Total Points Against)</li>
                  <li>Head-to-Head match outcome</li>
                </ol>
              </div>

            </div>
          )}
        </div>

        {/* Footer Actions */}
        <div className="px-6 py-4 bg-gray-50 border-t border-gray-200 flex items-center justify-between">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 text-xs font-bold text-gray-700 hover:bg-gray-200 rounded-xl transition-colors"
          >
            Cancel
          </button>

          <div className="flex items-center space-x-3">
            <button
              type="button"
              onClick={() => handleSave(false)}
              className="px-4 py-2 text-xs font-bold text-[#0B5D3B] border border-[#0B5D3B] hover:bg-emerald-50 rounded-xl transition-all"
            >
              Save Draft
            </button>

            <button
              type="button"
              onClick={() => handleSave(true)}
              className="px-5 py-2 text-xs font-bold bg-[#0B5D3B] hover:bg-[#08472d] text-white rounded-xl shadow-md transition-all flex items-center gap-1.5"
            >
              <Check className="w-4 h-4 text-[#D4A72C]" />
              <span>Publish Tournament</span>
            </button>
          </div>
        </div>

      </div>
    </div>
  );
};
