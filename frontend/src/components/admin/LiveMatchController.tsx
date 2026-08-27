import React, { useState } from 'react';
import { 
  Play, 
  Pause, 
  RotateCcw, 
  CheckCircle2, 
  Clock, 
  Trophy, 
  AlertCircle, 
  AlertTriangle,
  ShieldAlert, 
  Sparkles, 
  History, 
  Check, 
  Edit3, 
  X, 
  ArrowLeft,
  Crown,
  Flame,
  Award,
  Plus
} from 'lucide-react';
import confetti from 'canvas-confetti';
import { Tournament, Match, BoardScore } from '../../types/tournament';
import { useTournament } from '../../context/TournamentContext';
import { ConfirmationModal } from '../common/ConfirmationModal';
import { MatchTimer } from './MatchTimer';
import { BoardResultForm, BoardObservation, emptyObservation, previewBoard } from './BoardResultForm';

interface LiveMatchControllerProps {
  /** Something the umpire needs to know that happened before this view opened. */
  notice?: string;
  onDismissNotice?: () => void;
  tournament: Tournament;
  match: Match;
  onBack: () => void;
}

export const LiveMatchController: React.FC<LiveMatchControllerProps> = ({
  tournament,
  match,
  onBack,
  notice,
  onDismissNotice
}) => {
  const { 
    startMatch, 
    pauseMatch, 
    resumeMatch, 
    addBoardToMatch,
    submitBoardScore, 
    updateBoardScore, 
    confirmMatchResult,
    role
  } = useTournament();

  const [activeBoardNumber, setActiveBoardNumber] = useState<number>(() => {
    const inProgress = match.boards.find(b => b.status === 'in_progress');
    if (inProgress) return inProgress.boardNumber;
    const firstPending = match.boards.find(b => b.status === 'pending');
    if (firstPending) return firstPending.boardNumber;
    return 1;
  });

  // Modal states
  const [isSubmitScoreModalOpen, setIsSubmitScoreModalOpen] = useState(false);
  const [isEditAuditModalOpen, setIsEditAuditModalOpen] = useState(false);
  const [isConfirmResultModalOpen, setIsConfirmResultModalOpen] = useState(false);
  const [selectedBoardForScore, setSelectedBoardForScore] = useState<number>(1);

  // Score Input form state
  const [p1InputScore, setP1InputScore] = useState<number>(21);
  const [p2InputScore, setP2InputScore] = useState<number>(15);
  const [queenClaimed, setQueenClaimed] = useState<'player1' | 'player2' | 'none'>('player1');
  const [queenCovered, setQueenCovered] = useState<boolean>(true);
  const [whiteCoins, setWhiteCoins] = useState<number>(7);
  const [blackCoins, setBlackCoins] = useState<number>(4);
  const [auditReason, setAuditReason] = useState<string>('Official board completion');
  const [finishedBy, setFinishedBy] = useState<'player1' | 'player2' | 'none'>('none');
  const [observation, setObservation] = useState<BoardObservation>(emptyObservation);

  const rules = tournament.rules || ({} as any);
  // Tournaments created before remaining-coins scoring existed keep the old
  // model, so their confirmed results are not rewritten underneath them.
  const usesRemainingCoins = rules.scoringMode === 'remaining_coins';

  const isLive = match.status === 'live';
  const isPaused = match.status === 'paused';
  const isCompleted = match.status === 'completed';

  const handleOpenScoreModal = (boardNum: number, isCorrection: boolean = false) => {
    const currentBoard = match.boards.find(b => b.boardNumber === boardNum);
    setSelectedBoardForScore(boardNum);
    if (currentBoard) {
      const p1S = currentBoard.player1Score || 0;
      const p2S = currentBoard.player2Score || 0;
      setP1InputScore(p1S);
      setP2InputScore(p2S);
      setQueenClaimed(currentBoard.queenClaimedBy || 'none');
      setQueenCovered(currentBoard.queenCovered || false);
      setWhiteCoins(currentBoard.whiteCoinsPocketed || 0);
      setBlackCoins(currentBoard.blackCoinsPocketed || 0);
      
      if (p1S > 0) {
        setFinishedBy('player1');
      } else if (p2S > 0) {
        setFinishedBy('player2');
      } else {
        setFinishedBy('none');
      }

      setObservation({
        winner: currentBoard.boardWinner || 'none',
        queenPocketedBy: currentBoard.queenPocketedBy || currentBoard.queenClaimedBy || 'none',
        queenCoveredBy: currentBoard.queenCoveredBy || 'none',
        coinsRemainingWith: currentBoard.coinsRemainingWith || 'none',
        coinsRemaining: currentBoard.coinsRemaining ?? 0,
        p1Penalty: currentBoard.p1Penalty ?? 0,
        p2Penalty: currentBoard.p2Penalty ?? 0,
      });
    } else {
      setFinishedBy('none');
      setObservation(emptyObservation);
    }
    if (isCorrection) {
      setAuditReason('Score correction after referee review');
      setIsEditAuditModalOpen(true);
    } else {
      setAuditReason('Board finalized');
      setIsSubmitScoreModalOpen(true);
    }
  };

  const handleSaveBoardScore = () => {
    const payload = usesRemainingCoins
      ? (() => {
          const preview = previewBoard(observation, rules);
          return {
            // The server recomputes these; they are sent so the request is
            // meaningful to anything reading the raw payload (audit, replay).
            p1Score: preview.p1,
            p2Score: preview.p2,
            boardWinner: observation.winner,
            coinsRemainingWith: observation.coinsRemainingWith,
            coinsRemaining: observation.coinsRemaining,
            queenPocketedBy: observation.queenPocketedBy,
            queenCoveredBy: observation.queenCoveredBy,
            p1Penalty: observation.p1Penalty,
            p2Penalty: observation.p2Penalty,
            auditReason,
          };
        })()
      : {
          p1Score: p1InputScore,
          p2Score: p2InputScore,
          queenClaimedBy: queenClaimed,
          queenCovered,
          auditReason,
        };

    submitBoardScore(tournament.id, match.id, selectedBoardForScore, payload);
    setIsSubmitScoreModalOpen(false);
  };

  const handleSaveScoreCorrection = () => {
    // A correction restates the observations, the same shape the original
    // submission used. Sending the two score numbers instead left the server
    // with nothing to re-score from, so the edit appeared to do nothing.
    const preview = usesRemainingCoins ? previewBoard(observation, rules) : null;
    updateBoardScore(
      tournament.id,
      match.id,
      selectedBoardForScore,
      usesRemainingCoins
        ? {
            boardNumber: selectedBoardForScore,
            status: 'completed',
            player1Score: preview!.p1,
            player2Score: preview!.p2,
            boardWinner: observation.winner,
            coinsRemainingWith: observation.coinsRemainingWith,
            coinsRemaining: observation.coinsRemaining,
            queenPocketedBy: observation.queenPocketedBy,
            queenCoveredBy: observation.queenCoveredBy,
            p1Penalty: observation.p1Penalty,
            p2Penalty: observation.p2Penalty,
          } as any
        : {
            boardNumber: selectedBoardForScore,
            status: 'completed',
            player1Score: p1InputScore,
            player2Score: p2InputScore,
            queenClaimedBy: queenClaimed,
            queenCovered,
            whiteCoinsPocketed: whiteCoins,
            blackCoinsPocketed: blackCoins
          } as any,
      auditReason,
      // Opening the correction screen and writing a reason IS the deliberate
      // act that a confirmed board requires.
      true
    );
    setIsEditAuditModalOpen(false);
  };

  const handleExecuteConfirmResult = () => {
    confirmMatchResult(tournament.id, match.id);
    // Fire festive victory confetti
    try {
      confetti({
        particleCount: 100,
        spread: 70,
        origin: { y: 0.6 }
      });
    } catch (e) {}
  };

  return (
    <div id="live-match-controller" className="space-y-6 max-w-5xl mx-auto">
      
      {/* Top Navigation Bar */}
      <div className="flex items-center justify-between">
        <button
          onClick={onBack}
          className="flex items-center space-x-1.5 text-xs font-bold text-[#0B5D3B] hover:text-[#08472d] bg-white px-3 py-1.5 rounded-xl border border-gray-200 shadow-2xs hover:bg-gray-50 transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
          <span>Back to All Matches</span>
        </button>

        <div className="flex items-center space-x-2">
          {role === 'admin' ? (
            <span className="inline-flex items-center gap-1 px-3 py-1 rounded-full text-xs font-bold bg-[#D4A72C] text-[#202522] shadow-xs">
              <ShieldAlert className="w-3.5 h-3.5" />
              Official Admin Scorekeeper Mode
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 px-3 py-1 rounded-full text-xs font-semibold bg-gray-100 text-gray-700">
              Player Live Spectator Mode (Read-Only)
            </span>
          )}
        </div>
      </div>

      {/* Main Scoreboard & Live Timer Hero Card */}
      <div className="bg-white rounded-2xl border border-gray-200/80 shadow-md overflow-hidden">
        
        {/* Match Header Bar */}
        <div className="bg-[#202522] text-white px-6 py-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center space-x-3">
            <span className={`px-2 py-0.5 rounded text-[10px] font-black uppercase tracking-wider ${
              isLive
                ? 'bg-[#FF4444] text-white animate-pulse'
                : isCompleted
                ? 'bg-gray-700 text-gray-200'
                : 'bg-blue-600 text-white'
            }`}>
              {isLive ? 'LIVE' : match.status.toUpperCase()}
            </span>
            <span className="font-bold text-sm sm:text-base tracking-tight text-white">
              Match #{match.matchNumber} — {match.roundName} — Board #{match.boardNumber}
            </span>
          </div>

          <div className="flex items-center space-x-3 font-mono text-2xl font-bold text-[#D4A72C]">
            <Clock className="w-5 h-5 text-[#D4A72C]" />
            <MatchTimer match={match} />
          </div>
        </div>

        {notice && (
          <div className="px-4 sm:px-6 py-2 bg-amber-50 border-b border-amber-200 flex items-start gap-2 text-[11px] text-amber-900">
            <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
            <span className="flex-1">{notice}</span>
            {onDismissNotice && (
              <button onClick={onDismissNotice} className="font-bold underline shrink-0">Dismiss</button>
            )}
          </div>
        )}

        {match.tossWinnerName && (
          <div className="px-4 sm:px-6 py-2 bg-emerald-50 border-b border-emerald-200 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-emerald-900">
            <span className="font-bold uppercase tracking-wider text-[10px]">Toss</span>
            <span><strong>{match.tossWinnerName}</strong> won{match.tossCoinResult ? ` (${match.tossCoinResult})` : ''}</span>
            <span className="capitalize">and chose <strong>{match.tossChoice || 'strike'}</strong></span>
          </div>
        )}

        {/* Players & Central Score Display */}
        <div className="p-6 bg-white">
          <div className="grid grid-cols-1 md:grid-cols-7 gap-6 items-center">
            
            {/* Player 1 Card (3 cols) */}
            <div className={`md:col-span-3 p-5 rounded-2xl border text-center transition-all ${
              match.winnerId === match.player1Id ? 'border-[#D4A72C] bg-amber-50/30 shadow-sm' : 'border-gray-200 bg-gray-50/50'
            }`}>
              <div className="w-12 h-12 rounded-full bg-[#0B5D3B] text-white flex items-center justify-center font-bold text-lg mx-auto mb-2 shadow-sm border-2 border-white">
                1
              </div>
              <h3 className="font-bold text-lg sm:text-xl text-gray-900 truncate">
                {match.player1Name}
              </h3>
              <p className="text-xs text-gray-500 mt-0.5">
                White Striker {match.player1Details ? `· ${(match.player1Details as any).club || 'Independent'}` : ''}
              </p>

              <div className="mt-4 flex items-center justify-center space-x-6 pt-3 border-t border-gray-200">
                <div>
                  <div className="text-[10px] uppercase font-bold text-gray-400">Board Wins</div>
                  <div className="text-3xl font-black text-[#0B5D3B]">{match.player1BoardWins}</div>
                </div>
                <div className="h-8 w-px bg-gray-200" />
                <div>
                  <div className="text-[10px] uppercase font-bold text-gray-400">Total Points</div>
                  <div className="text-3xl font-black text-gray-900">{match.player1TotalPoints}</div>
                </div>
              </div>
            </div>

            {/* Center VS info (1 col) */}
            <div className="md:col-span-1 flex flex-col items-center justify-center text-center">
              <div className="w-10 h-10 rounded-full bg-[#F8F6F0] border border-[#D4A72C] flex items-center justify-center text-xs font-black text-[#0B5D3B] shadow-xs">
                VS
              </div>
              <div className="text-[10px] uppercase font-bold text-gray-400 mt-2">
                Best of {match.maxBoards} Boards
              </div>
              <div className="text-xs font-bold text-[#2E7D32] mt-1">
                {match.boards.filter(b => b.status === 'completed').length} Finished
              </div>
            </div>

            {/* Player 2 Card (3 cols) */}
            <div className={`md:col-span-3 p-5 rounded-2xl border text-center transition-all ${
              match.winnerId === match.player2Id ? 'border-[#D4A72C] bg-amber-50/30 shadow-sm' : 'border-gray-200 bg-gray-50/50'
            }`}>
              <div className="w-12 h-12 rounded-full bg-[#202522] text-white flex items-center justify-center font-bold text-lg mx-auto mb-2 shadow-sm border-2 border-white">
                2
              </div>
              <h3 className="font-bold text-lg sm:text-xl text-gray-900 truncate">
                {match.player2Name}
              </h3>
              <p className="text-xs text-gray-500 mt-0.5">
                Black Striker {match.player2Details ? `· ${(match.player2Details as any).club || 'Independent'}` : ''}
              </p>

              <div className="mt-4 flex items-center justify-center space-x-6 pt-3 border-t border-gray-200">
                <div>
                  <div className="text-[10px] uppercase font-bold text-gray-400">Board Wins</div>
                  <div className="text-3xl font-black text-[#0B5D3B]">{match.player2BoardWins}</div>
                </div>
                <div className="h-8 w-px bg-gray-200" />
                <div>
                  <div className="text-[10px] uppercase font-bold text-gray-400">Total Points</div>
                  <div className="text-3xl font-black text-gray-900">{match.player2TotalPoints}</div>
                </div>
              </div>
            </div>

          </div>
        </div>

        {/* Action Controls Footer */}
        {role === 'admin' && (
          <div className="bg-[#F8F6F0] p-4 border-t border-gray-200 flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center space-x-2">
              {!isLive ? (
                <button
                  id="start-match-timer-btn"
                  onClick={() => startMatch(tournament.id, match.id)}
                  disabled={isCompleted}
                  className="px-5 py-2.5 bg-[#D4A72C] hover:opacity-90 text-[#0B5D3B] text-xs font-black rounded-lg shadow-md transition-all flex items-center gap-1.5 disabled:opacity-40"
                >
                  <Play className="w-4 h-4 fill-current" />
                  <span>{isPaused ? 'Resume Match' : 'Start Match & Timer'}</span>
                </button>
              ) : (
                <button
                  id="pause-match-timer-btn"
                  onClick={() => pauseMatch(tournament.id, match.id)}
                  className="px-5 py-2.5 bg-[#202522] hover:bg-black text-white text-xs font-bold rounded-lg shadow-md transition-all flex items-center gap-1.5"
                >
                  <Pause className="w-4 h-4" />
                  <span>Pause Timer</span>
                </button>
              )}

              <span className="text-xs text-gray-600 font-medium ml-2">
                {isLive ? '⏱️ Live timer active with auto-sync' : isPaused ? '⏸️ Match paused' : isCompleted ? '🏁 Match finalized' : 'Ready to start'}
              </span>
            </div>

            {/* Confirm Match Result CTA */}
            {!match.resultConfirmed && (
              <button
                id="confirm-match-result-btn"
                onClick={() => setIsConfirmResultModalOpen(true)}
                className="px-5 py-2.5 bg-[#0B5D3B] hover:bg-[#08472d] text-white text-xs font-bold rounded-lg shadow-md transition-all flex items-center gap-2"
              >
                <CheckCircle2 className="w-4 h-4 text-[#D4A72C]" />
                <span>Confirm Final Result</span>
              </button>
            )}
          </div>
        )}
      </div>

      {/* Winner Banner if match is completed */}
      {match.resultConfirmed && (
        <div className="bg-gradient-to-r from-amber-500 via-[#D4A72C] to-amber-600 text-[#202522] p-4 rounded-2xl shadow-md flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <div className="w-10 h-10 rounded-full bg-white/90 flex items-center justify-center">
              <Trophy className="w-6 h-6 text-amber-600" />
            </div>
            <div>
              <div className="text-[11px] font-extrabold uppercase tracking-wider text-amber-950">
                Official Result Confirmed
              </div>
              <div className="text-base font-bold">
                {match.winnerName} wins {Math.max(match.player1BoardWins, match.player2BoardWins)} boards to {Math.min(match.player1BoardWins, match.player2BoardWins)}!
              </div>
            </div>
          </div>
          <span className="px-3 py-1 bg-white/90 font-bold text-xs rounded-xl shadow-xs">
            Points Table & Brackets Updated
          </span>
        </div>
      )}

      {/* Board-by-Board Scoring Table Card */}
      <div className="bg-white rounded-2xl border border-gray-200/80 p-6 shadow-xs space-y-4">
        
        <div className="flex items-center justify-between border-b border-gray-100 pb-3">
          <div>
            <h3 className="font-serif font-bold text-gray-900 text-base">
              Board-by-Board Scores
            </h3>
            <p className="text-xs text-gray-500">
              Official scores for each carrom board. Standard target score: 29 pts or highest score.
            </p>
          </div>

          <div className="flex items-center space-x-2">
            <div className="text-xs font-semibold text-gray-600 bg-gray-50 px-3 py-1.5 rounded-xl border border-gray-200">
              {match.boards.filter(b => b.status === 'completed').length} / {match.maxBoards} Boards Completed
            </div>
            {role === 'admin' && !match.resultConfirmed && (
              <button
                onClick={() => addBoardToMatch(tournament.id, match.id)}
                className="px-3 py-1.5 bg-[#0B5D3B] hover:bg-[#08472d] text-white text-xs font-bold rounded-xl border border-transparent shadow-xs transition-colors flex items-center gap-1"
                title="Add a new board score record to this match"
              >
                <Plus className="w-3.5 h-3.5" />
                <span>Add Board</span>
              </button>
            )}
          </div>
        </div>

        {/* Boards List */}
        <div className="space-y-3">
          {match.boards.map((board) => {
            const isBoardCompleted = board.status === 'completed';
            const isBoardInProgress = board.status === 'in_progress';
            
            const boardWinner = isBoardCompleted
              ? board.player1Score > board.player2Score
                ? match.player1Name
                : board.player2Score > board.player1Score
                ? match.player2Name
                : 'Tie / Draw'
              : 'Pending';

            return (
              <div
                key={board.boardNumber}
                className={`rounded-2xl p-4 border transition-all ${
                  isBoardInProgress
                    ? 'border-amber-400 bg-amber-50/20 shadow-xs'
                    : isBoardCompleted
                    ? 'border-emerald-200/80 bg-emerald-50/10'
                    : 'border-gray-200 bg-gray-50/50 opacity-75'
                }`}
              >
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                  
                  {/* Board Title & Status */}
                  <div className="flex items-center space-x-3">
                    <div className={`w-8 h-8 rounded-xl flex items-center justify-center font-bold text-xs ${
                      isBoardCompleted ? 'bg-[#0B5D3B] text-white' :
                      isBoardInProgress ? 'bg-amber-500 text-white' :
                      'bg-gray-200 text-gray-600'
                    }`}>
                      #{board.boardNumber}
                    </div>

                    <div>
                      <div className="font-bold text-xs text-gray-900 flex items-center gap-2">
                        <span>Board {board.boardNumber}</span>
                        <span className={`text-[10px] px-2 py-0.2 rounded-md font-bold uppercase ${
                          isBoardCompleted ? 'bg-emerald-100 text-emerald-800' :
                          isBoardInProgress ? 'bg-amber-100 text-amber-800' :
                          'bg-gray-100 text-gray-600'
                        }`}>
                          {board.status.replace('_', ' ')}
                        </span>
                      </div>
                      <div className="text-[11px] text-gray-500 mt-0.5">
                        {isBoardCompleted ? (
                          <span className="text-emerald-800 font-semibold">
                            Winner: {boardWinner} · Diff: {Math.abs(board.player1Score - board.player2Score)} pts
                          </span>
                        ) : isBoardInProgress ? (
                          <span className="text-amber-700">Currently in play</span>
                        ) : (
                          <span>Awaiting previous boards</span>
                        )}
                      </div>
                    </div>
                  </div>

                  {/* Board Score Comparison Box */}
                  <div className="flex items-center space-x-4 bg-white px-4 py-2 rounded-xl border border-gray-200 self-start sm:self-auto">
                    
                    {/* P1 Score */}
                    <div className="text-center">
                      <div className="text-[9px] uppercase text-gray-400 font-bold truncate max-w-[80px]">
                        {match.player1Name.split(' ')[0]}
                      </div>
                      <div className={`text-lg font-black ${
                        board.player1Score > board.player2Score ? 'text-[#0B5D3B]' : 'text-gray-700'
                      }`}>
                        {board.player1Score}
                      </div>
                    </div>

                    <span className="text-xs font-bold text-gray-300">:</span>

                    {/* P2 Score */}
                    <div className="text-center">
                      <div className="text-[9px] uppercase text-gray-400 font-bold truncate max-w-[80px]">
                        {match.player2Name.split(' ')[0]}
                      </div>
                      <div className={`text-lg font-black ${
                        board.player2Score > board.player1Score ? 'text-[#0B5D3B]' : 'text-gray-700'
                      }`}>
                        {board.player2Score}
                      </div>
                    </div>

                    {/* Queen details */}
                    {board.queenClaimedBy && board.queenClaimedBy !== 'none' && (
                      <div className="border-l border-gray-100 pl-3 flex items-center gap-1 text-[10px] text-amber-800 font-bold">
                        <Crown className="w-3 h-3 text-[#D4A72C]" />
                        <span>Queen Covered (+3)</span>
                      </div>
                    )}
                  </div>

                  {/* Admin Actions */}
                  {role === 'admin' && (
                    <div className="flex items-center space-x-2 self-end sm:self-auto">
                      {!isBoardCompleted ? (
                        <button
                          onClick={() => handleOpenScoreModal(board.boardNumber, false)}
                          className="px-3 py-1.5 bg-[#0B5D3B] hover:bg-[#08472d] text-white text-xs font-bold rounded-lg shadow-xs transition-colors flex items-center gap-1"
                        >
                          <Edit3 className="w-3.5 h-3.5" />
                          <span>Submit Score</span>
                        </button>
                      ) : (
                        <button
                          onClick={() => handleOpenScoreModal(board.boardNumber, true)}
                          className="px-3 py-1.5 text-gray-700 hover:bg-gray-100 border border-gray-200 text-xs font-semibold rounded-lg transition-colors flex items-center gap-1"
                          title="Correct score with audit history"
                        >
                          <History className="w-3.5 h-3.5 text-gray-500" />
                          <span>Edit / Audit</span>
                        </button>
                      )}
                    </div>
                  )}

                </div>
              </div>
            );
          })}
        </div>

      </div>

      {/* Score Correction Audit Log Card */}
      {match.auditHistory && match.auditHistory.length > 0 && (
        <div className="bg-white rounded-2xl border border-gray-200/80 p-5 shadow-xs">
          <div className="flex items-center space-x-2 text-gray-900 font-serif font-bold text-sm mb-3">
            <History className="w-4 h-4 text-purple-600" />
            <span>Score Correction Audit History</span>
          </div>

          <div className="divide-y divide-gray-100 text-xs">
            {match.auditHistory.map(item => (
              <div key={item.id} className="py-2.5 flex items-center justify-between text-gray-600">
                <div>
                  <span className="font-semibold text-gray-900">Board {item.boardNumber}: </span>
                  <span>Changed from ({item.previousScore.player1} - {item.previousScore.player2}) to ({item.newScore.player1} - {item.newScore.player2})</span>
                  <div className="text-[10px] text-gray-400 mt-0.5">
                    Reason: "{item.reason}" · By {item.adminName}
                  </div>
                </div>
                <span className="text-[10px] text-gray-400">
                  {new Date(item.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Submit / Edit Board Score Modal */}
      {(isSubmitScoreModalOpen || isEditAuditModalOpen) && (
        <div className="fixed inset-0 z-50 overflow-y-auto bg-black/60 backdrop-blur-xs flex items-start sm:items-center justify-center p-2 sm:p-4">
          <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border border-gray-100 animate-in zoom-in-95 duration-150">
            
            <div className="flex items-center justify-between pb-3 border-b border-gray-100 mb-4">
              <div className="flex items-center space-x-2">
                <div className="p-1.5 bg-emerald-100 text-[#0B5D3B] rounded-lg">
                  <Edit3 className="w-4 h-4" />
                </div>
                <h3 className="font-bold text-gray-900 text-base">
                  {isEditAuditModalOpen ? 'Audit Score Correction' : `Submit Board #${selectedBoardForScore} Score`}
                </h3>
              </div>
              <button
                onClick={() => {
                  setIsSubmitScoreModalOpen(false);
                  setIsEditAuditModalOpen(false);
                }}
                className="text-gray-400 hover:text-gray-600"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="space-y-4 text-xs">
              
              {usesRemainingCoins ? (
                <BoardResultForm
                  match={match}
                  rules={rules}
                  value={observation}
                  onChange={setObservation}
                />
              ) : (
                <>
                  {/* Finished By / Board Winner Selector */}
                  <div className="p-3 bg-emerald-50/50 rounded-xl border border-emerald-100">
                    <label className="block font-bold text-gray-800 mb-1.5 flex items-center gap-1">
                      <Flame className="w-3.5 h-3.5 text-emerald-700" />
                      <span>Finished / Won By</span>
                    </label>
                    <div className="grid grid-cols-3 gap-1.5">
                      {([['player1', match.player1Name], ['player2', match.player2Name], ['none', 'None / Draw']] as const).map(([side, name]) => (
                        <button
                          key={side}
                          type="button"
                          onClick={() => setFinishedBy(side as any)}
                          className={`py-1.5 rounded-lg text-center font-bold text-[11px] border transition-all truncate ${
                            finishedBy === side
                              ? (side === 'none' ? 'bg-gray-800 text-white border-gray-800' : 'bg-[#0B5D3B] text-white border-[#0B5D3B]')
                              : 'bg-white text-gray-700 border-gray-200'
                          }`}
                        >
                          {side === 'none' ? name : name.split(' ')[0]}
                        </button>
                      ))}
                    </div>
                  </div>

                  {/* Score Input Fields — both always editable */}
                  <div className="grid grid-cols-2 gap-3 bg-gray-50 p-3.5 rounded-xl border border-gray-200">
                    <div>
                      <label className="block font-bold text-gray-800 mb-1 truncate">
                        {match.player1Name} (White)
                      </label>
                      <input
                        type="number"
                        min={0}
                        value={p1InputScore}
                        onChange={e => setP1InputScore(Math.max(0, parseInt(e.target.value) || 0))}
                        className="w-full text-base font-black text-center py-2 border border-gray-200 rounded-lg bg-white focus:ring-2 focus:ring-[#0B5D3B]"
                      />
                    </div>
                    <div>
                      <label className="block font-bold text-gray-800 mb-1 truncate">
                        {match.player2Name} (Black)
                      </label>
                      <input
                        type="number"
                        min={0}
                        value={p2InputScore}
                        onChange={e => setP2InputScore(Math.max(0, parseInt(e.target.value) || 0))}
                        className="w-full text-base font-black text-center py-2 border border-gray-200 rounded-lg bg-white focus:ring-2 focus:ring-[#0B5D3B]"
                      />
                    </div>
                  </div>

                  {/* Queen — records the queen only, never the winner */}
                  <div className="p-3 bg-amber-50/60 rounded-xl border border-amber-200">
                    <label className="block font-bold text-amber-950 mb-1.5 flex items-center gap-1">
                      <Crown className="w-3.5 h-3.5 text-[#D4A72C]" />
                      <span>Queen Pocketed &amp; Covered</span>
                    </label>
                    <div className="grid grid-cols-3 gap-1.5">
                      {([['player1', match.player1Name], ['player2', match.player2Name], ['none', 'None / Uncovered']] as const).map(([side, name]) => (
                        <button
                          key={side}
                          type="button"
                          onClick={() => {
                            setQueenClaimed(side as any);
                            setQueenCovered(side !== 'none');
                          }}
                          className={`py-1.5 rounded-lg text-center font-bold text-[11px] border transition-all truncate ${
                            queenClaimed === side
                              ? (side === 'none' ? 'bg-gray-800 text-white border-gray-800' : 'bg-[#0B5D3B] text-white border-[#0B5D3B]')
                              : 'bg-white text-gray-700 border-gray-200'
                          }`}
                        >
                          {side === 'none' ? name : `${name.split(' ')[0]} (+${rules.queenPoints ?? 3})`}
                        </button>
                      ))}
                    </div>
                  </div>
                </>
              )}

              {/* Audit reason if correction */}
              {isEditAuditModalOpen && (
                <div>
                  <label className="block font-bold text-gray-700 mb-1">
                    Audit Correction Reason *
                  </label>
                  <input
                    type="text"
                    value={auditReason}
                    onChange={e => setAuditReason(e.target.value)}
                    className="w-full p-2 border border-gray-200 rounded-lg bg-white"
                    placeholder="e.g. Referee recount on coin count"
                  />
                </div>
              )}

            </div>

            {/* Actions */}
            <div className="mt-6 pt-3 border-t border-gray-100 flex items-center justify-end space-x-2">
              <button
                type="button"
                onClick={() => {
                  setIsSubmitScoreModalOpen(false);
                  setIsEditAuditModalOpen(false);
                }}
                className="px-4 py-2 text-xs font-semibold text-gray-600 hover:bg-gray-100 rounded-xl"
              >
                Cancel
              </button>

              <button
                type="button"
                onClick={isEditAuditModalOpen ? handleSaveScoreCorrection : handleSaveBoardScore}
                className="px-5 py-2 text-xs font-bold bg-[#0B5D3B] hover:bg-[#08472d] text-white rounded-xl shadow-md flex items-center gap-1.5"
              >
                <Check className="w-4 h-4 text-[#D4A72C]" />
                <span>{isEditAuditModalOpen ? 'Save Correction Log' : 'Save Board Score'}</span>
              </button>
            </div>

          </div>
        </div>
      )}

      {/* Confirmation Modal to Finalize Official Match Result */}
      <ConfirmationModal
        isOpen={isConfirmResultModalOpen}
        onClose={() => setIsConfirmResultModalOpen(false)}
        onConfirm={handleExecuteConfirmResult}
        title="Confirm Official Match Result?"
        description={`Confirming will finalize the score (${match.player1TotalPoints} - ${match.player2TotalPoints}) and automatically update tournament points table, player rankings, and advance the winner in the knockout bracket.`}
        confirmLabel="Confirm & Update Standings"
        variant="primary"
      />

    </div>
  );
};
