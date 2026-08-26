import React, { useState, useEffect } from 'react';
import { X, Settings } from 'lucide-react';
import { Tournament, TournamentFormat } from '../../types/tournament';
import { useTournament } from '../../context/TournamentContext';

interface EditTournamentModalProps {
  isOpen: boolean;
  onClose: () => void;
  tournament: Tournament;
}

export const EditTournamentModal: React.FC<EditTournamentModalProps> = ({
  isOpen,
  onClose,
  tournament
}) => {
  const { updateTournament } = useTournament();

  const [name, setName] = useState(tournament.name);
  const [description, setDescription] = useState(tournament.description || '');
  const [category, setCategory] = useState<'singles' | 'doubles' | 'both'>(tournament.category);
  const [format, setFormat] = useState<TournamentFormat>(tournament.format);
  const [prizePool, setPrizePool] = useState(tournament.prizePool || '');

  useEffect(() => {
    setName(tournament.name);
    setDescription(tournament.description || '');
    setCategory(tournament.category);
    setFormat(tournament.format);
    setPrizePool(tournament.prizePool || '');
  }, [tournament]);

  if (!isOpen) return null;

  const handleSave = async () => {
    if (!name.trim()) {
      alert('Please enter a tournament name.');
      return;
    }

    await updateTournament(tournament.id, {
      name,
      description,
      category,
      format,
      prizePool
    });

    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-black/60 backdrop-blur-xs flex items-start sm:items-center justify-center p-2 sm:p-4 animate-in fade-in duration-150">
      <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border border-gray-100 animate-in zoom-in-95 duration-150">
        
        {/* Header */}
        <div className="flex items-center justify-between pb-3 border-b border-gray-100 mb-4">
          <div className="flex items-center space-x-2">
            <div className="p-1.5 bg-emerald-100 text-[#0B5D3B] rounded-lg">
              <Settings className="w-5 h-5 text-[#0B5D3B]" />
            </div>
            <h3 className="font-bold text-gray-900 text-base">Edit Tournament Details</h3>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Body */}
        <div className="space-y-4 text-xs">
          <div>
            <label className="block font-bold text-gray-700 mb-1">Tournament Name *</label>
            <input
              type="text"
              value={name}
              onChange={e => setName(e.target.value)}
              className="w-full text-xs px-3 py-2 border border-gray-200 rounded-lg focus:ring-2 focus:ring-[#0B5D3B]"
              required
            />
          </div>

          <div>
            <label className="block font-bold text-gray-700 mb-1">Description</label>
            <textarea
              value={description}
              onChange={e => setDescription(e.target.value)}
              rows={2}
              className="w-full text-xs px-3 py-2 border border-gray-200 rounded-lg focus:ring-2 focus:ring-[#0B5D3B]"
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block font-bold text-gray-700 mb-1">Category</label>
              <select
                value={category}
                onChange={e => setCategory(e.target.value as any)}
                className="w-full text-xs px-3 py-2 border border-gray-200 rounded-lg bg-white"
              >
                <option value="singles">Singles Only</option>
                <option value="doubles">Doubles Only</option>
                <option value="both">Singles & Doubles</option>
              </select>
            </div>

            <div>
              <label className="block font-bold text-gray-700 mb-1">Format</label>
              <select
                value={format}
                onChange={e => setFormat(e.target.value as any)}
                className="w-full text-xs px-3 py-2 border border-gray-200 rounded-lg bg-white"
              >
                <option value="league_knockout">League + Knockout</option>
                <option value="round_robin">League / Round Robin</option>
                <option value="knockout">Single Knockout</option>
              </select>
            </div>
          </div>

          <div>
            <label className="block font-bold text-gray-700 mb-1">Prize Pool</label>
            <input
              type="text"
              value={prizePool}
              onChange={e => setPrizePool(e.target.value)}
              className="w-full text-xs px-3 py-2 border border-gray-200 rounded-lg focus:ring-2 focus:ring-[#0B5D3B]"
            />
          </div>
        </div>

        {/* Footer */}
        <div className="flex justify-end space-x-2 pt-4 mt-4 border-t border-gray-100">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 text-xs font-semibold text-gray-700 hover:bg-gray-100 rounded-lg"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSave}
            className="px-4 py-2 text-xs font-bold text-white bg-[#0B5D3B] hover:bg-[#08472d] rounded-lg shadow-sm"
          >
            Save Details
          </button>
        </div>

      </div>
    </div>
  );
};
