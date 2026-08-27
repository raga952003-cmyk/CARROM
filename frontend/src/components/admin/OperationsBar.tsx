import React, { useState } from 'react';
import { Printer, Smartphone, Globe, Copy, Check, ChevronDown, Zap } from 'lucide-react';
import { Tournament } from '../../types/tournament';
import { BulkScoreEntry } from './BulkScoreEntry';

interface OperationsBarProps {
  tournament: Tournament;
}

/**
 * Match-day entry points: paper sheets, a board's scoring screen, and the
 * public board.
 *
 * These are all URLs so they can be opened on another device — a scorer keeps
 * their board open on a phone, and the public link goes on a notice board or
 * into a group chat.
 */
export const OperationsBar: React.FC<OperationsBarProps> = ({ tournament }) => {
  const [boardsOpen, setBoardsOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const [bulkOpen, setBulkOpen] = useState(false);

  const publicUrl = `${window.location.origin}${window.location.pathname}#/live/${tournament.id}`;
  const boards = Array.from({ length: Math.max(1, tournament.numberOfBoards || 1) }, (_, i) => i + 1);

  const open = (hash: string) => window.open(`${window.location.pathname}${hash}`, '_blank');

  const copyPublicLink = async () => {
    try {
      await navigator.clipboard.writeText(publicUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard is unavailable over plain http on some browsers; show the
      // URL so it can still be copied by hand.
      window.prompt('Public link', publicUrl);
    }
  };

  const Btn: React.FC<{ onClick: () => void; children: React.ReactNode; tone?: 'gold' }> =
    ({ onClick, children, tone }) => (
      <button
        type="button"
        onClick={onClick}
        className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-bold border shrink-0 transition-colors ${
          tone === 'gold'
            ? 'bg-[#D4A72C] border-[#D4A72C] text-[#202522] hover:bg-[#c29623]'
            : 'bg-white border-gray-200 text-gray-700 hover:border-gray-300'
        }`}
      >
        {children}
      </button>
    );

  return (
    <div className="px-3 sm:px-6 py-2.5 bg-white border-b border-gray-200/80">
      <div className="flex items-center gap-2 overflow-x-auto">
        <span className="text-[10px] font-black uppercase tracking-wider text-gray-400 shrink-0 hidden sm:block">
          Match day
        </span>

        <Btn tone="gold" onClick={() => open(`#/print/boards/${tournament.id}`)}>
          <Printer className="w-3.5 h-3.5" /> Board sheets
        </Btn>
        <Btn onClick={() => open(`#/print/fixtures/${tournament.id}`)}>
          <Printer className="w-3.5 h-3.5" /> Draw
        </Btn>
        <Btn onClick={() => open(`#/print/standings/${tournament.id}`)}>
          <Printer className="w-3.5 h-3.5" /> Standings
        </Btn>

        <div className="relative shrink-0">
          <Btn onClick={() => setBoardsOpen(v => !v)}>
            <Smartphone className="w-3.5 h-3.5" /> Scorer mode
            <ChevronDown className="w-3 h-3" />
          </Btn>
          {boardsOpen && (
            <div className="absolute left-0 mt-1 z-30 bg-white border border-gray-200 rounded-xl shadow-lg py-1 min-w-[10rem]">
              <div className="px-3 py-1 text-[10px] uppercase tracking-wider text-gray-400">
                Open a board
              </div>
              {boards.map(n => (
                <button
                  key={n}
                  onClick={() => { setBoardsOpen(false); open(`#/board/${n}?t=${tournament.id}`); }}
                  className="w-full text-left px-3 py-1.5 text-xs font-semibold text-gray-700 hover:bg-gray-50"
                >
                  Board {n}
                </button>
              ))}
            </div>
          )}
        </div>

        <Btn onClick={() => open(`#/live/${tournament.id}`)}>
          <Globe className="w-3.5 h-3.5" /> Public board
        </Btn>
        <Btn onClick={copyPublicLink}>
          {copied ? <Check className="w-3.5 h-3.5 text-emerald-600" /> : <Copy className="w-3.5 h-3.5" />}
          {copied ? 'Copied' : 'Copy link'}
        </Btn>

        <Btn onClick={() => setBulkOpen(true)}>
          <Zap className="w-3.5 h-3.5" /> Rapid scores
        </Btn>
      </div>

      <BulkScoreEntry
        tournament={tournament}
        isOpen={bulkOpen}
        onClose={() => setBulkOpen(false)}
      />
    </div>
  );
};
