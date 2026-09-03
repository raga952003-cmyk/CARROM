import React, { useState, useRef } from 'react';
import { 
  X, 
  Upload, 
  Sparkles, 
  Check, 
  AlertCircle, 
  FileText, 
  Trash2,
  Calendar,
  Grid,
  Users
} from 'lucide-react';
import { Tournament, Player } from '../../types/tournament';
import { useTournament } from '../../context/TournamentContext';
import { tournamentService } from '../../services/tournamentService';

interface ImportParticipantsModalProps {
  isOpen: boolean;
  onClose: () => void;
  tournament: Tournament;
}

interface ParsedPlayer {
  /** 'doubles' rows carry a partner and register as a team. */
  type: 'singles' | 'doubles';
  name: string;
  club: string;
  city: string;
  rating: number;
  seed: number | null;
  email?: string | null;
  phone?: string | null;
  teamName?: string | null;
  partnerName?: string | null;
  partnerEmail?: string | null;
  partnerPhone?: string | null;
  selected: boolean;
}

export const ImportParticipantsModal: React.FC<ImportParticipantsModalProps> = ({
  isOpen,
  onClose,
  tournament
}) => {
  const { 
    createPlayerAccount, 
    registerForTournament, 
    generateFixturesForTournament, 
    generateScheduleForTournament,
    publishScheduleForTournament,
    allPlayers,
    refreshData 
  } = useTournament();

  const [rawText, setRawText] = useState('');
  const [fileObj, setFileObj] = useState<File | null>(null);
  const [fileName, setFileName] = useState('');
  const [parsedPlayers, setParsedPlayers] = useState<ParsedPlayer[]>([]);
  const [loading, setLoading] = useState(false);
  // Off by default, and deliberately so: the server refuses to redraw over
  // recorded results, but even where it would succeed, adding a late entrant
  // should not silently rebuild the draw and re-announce the schedule to
  // everybody. The organiser asks for it.
  const [autoSchedule, setAutoSchedule] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');
  const [successMsg, setSuccessMsg] = useState('');
  const [step, setStep] = useState<'input' | 'preview'>('input');

  const fileInputRef = useRef<HTMLInputElement>(null);

  if (!isOpen) return null;

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setFileObj(file);
    setFileName(file.name);
    setErrorMsg('');

    const reader = new FileReader();
    reader.onload = (event) => {
      const text = event.target?.result as string;
      setRawText(text);
    };
    reader.onerror = () => {
      setErrorMsg('Failed to read file.');
    };
    reader.readAsText(file);
  };

  const handleAnalyze = async () => {
    if (!rawText.trim() && !fileObj) {
      setErrorMsg('Please upload a file or paste a player list.');
      return;
    }

    setLoading(true);
    setErrorMsg('');

    try {
      if (fileObj) {
        // Use serverless backend Excel parsing
        const response = await tournamentService.uploadExcel(fileObj);
        if (response.status === 'success' && response.players) {
          // Keep every field the parser returned: dropping partnerName/teamName
          // here is what previously turned an imported doubles sheet into
          // singles entries.
          setParsedPlayers(
            response.players.map((p: any) => ({
              type: p.type === 'doubles' ? 'doubles' : 'singles',
              name: p.name || 'Unnamed Player',
              club: p.club || 'Independent',
              city: p.city || undefined,
              rating: p.rating || 1500,
              seed: p.seed ?? null,
              email: p.email ?? null,
              phone: p.phone ?? null,
              teamName: p.teamName ?? null,
              partnerName: p.partnerName ?? null,
              partnerEmail: p.partnerEmail ?? null,
              partnerPhone: p.partnerPhone ?? null,
              selected: p.selected !== false
            }))
          );
          setStep('preview');
          if (response.errors && response.errors.length > 0) {
            setErrorMsg(`File parsed with warnings: ${response.errors.join(', ')}`);
          }
        } else {
          setErrorMsg(response.errors?.join(', ') || 'Failed to parse file on backend.');
        }
      } else {
        // Fallback to Gemini parsing for pasted text
        // Parsing happens server-side so the AI key is never shipped to the
        // browser; the local fallback below still applies if it is unavailable.
        const aiResult = await tournamentService.parseParticipantsWithAI(rawText);
        if (!aiResult.available) {
          throw new Error(aiResult.error || 'AI parsing is not configured.');
        }
        const data = { players: aiResult.players };

        if (data.players && Array.isArray(data.players)) {
          setParsedPlayers(
            data.players.map((p: any) => ({
              type: 'singles' as const,
              name: p.name || 'Unnamed Player',
              club: p.club || 'Independent',
              city: p.city || undefined,
              rating: p.rating || 1500,
              seed: p.seed || null,
              selected: true
            }))
          );
          setStep('preview');
        } else {
          setErrorMsg('Invalid response format from parser.');
        }
      }
    } catch (err: any) {
      console.warn('AI Parsing failed, falling back to local regex split:', err);
      // Local parsing fallback
      const lines = rawText.split('\n').map(l => l.trim()).filter(l => l.length > 0);
      const fallbackList: ParsedPlayer[] = lines.map((line, idx) => {
        const parts = line.split(/[,;\t]/);
        return {
          type: 'singles' as const,
          name: parts[0]?.trim() || `Player ${idx + 1}`,
          club: parts[1]?.trim() || 'Independent',
          city: parts[2]?.trim() || undefined,
          rating: parseInt(parts[3]) || 1500 + Math.floor(Math.random() * 200),
          seed: parts[4] ? parseInt(parts[4]) : null,
          selected: true
        };
      });
      setParsedPlayers(fallbackList);
      setStep('preview');
    } finally {
      setLoading(false);
    }
  };

  const handleImportAndSchedule = async () => {
    const selectedPlayers = parsedPlayers.filter(p => p.selected);
    if (selectedPlayers.length === 0) {
      setErrorMsg('No players selected for import.');
      return;
    }

    setLoading(true);
    setErrorMsg('');

    try {
      // Use backend bulk import confirmation transaction (accounts creation + registrations + fixtures + scheduling + publish)
      const response: any = await tournamentService.confirmImport(
        tournament.id, selectedPlayers, autoSchedule);
      setSuccessMsg(response.message || 'Successfully registered the players.');
      // The import created players, teams, entries and possibly a whole draw,
      // none of which the screen behind this modal knows about yet.
      await refreshData();
      // A partial import must not look like a clean one.
      if (response.skipped && response.skipped.length > 0) {
        setErrorMsg(
          `${response.skipped.length} row(s) skipped: ${response.skipped.slice(0, 3).join(' ')}` +
            (response.skipped.length > 3 ? ' ...' : '')
        );
      }

      // Close after delay
      setTimeout(() => {
        onClose();
        setStep('input');
        setRawText('');
        setFileName('');
        setFileObj(null);
        setSuccessMsg('');
      }, 3000);

    } catch (err: any) {
      setErrorMsg(err.message || 'Import or scheduling failed.');
    } finally {
      setLoading(false);
    }
  };


  const toggleSelectAll = () => {
    const allSelected = parsedPlayers.every(p => p.selected);
    setParsedPlayers(parsedPlayers.map(p => ({ ...p, selected: !allSelected })));
  };

  const handleFieldChange = (index: number, field: keyof ParsedPlayer, value: any) => {
    setParsedPlayers(prev => prev.map((p, idx) => {
      if (idx === index) {
        return { ...p, [field]: value };
      }
      return p;
    }));
  };

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-black/60 backdrop-blur-xs flex items-start sm:items-center justify-center p-2 sm:p-4">
      <div className="bg-white rounded-2xl sm:rounded-3xl max-w-2xl w-full p-4 sm:p-6 shadow-2xl border border-gray-100 flex flex-col max-h-[85vh] relative overflow-hidden">
        
        <button
          onClick={onClose}
          disabled={loading}
          className="absolute top-4 right-4 text-gray-400 hover:text-gray-600 p-1.5 rounded-lg hover:bg-gray-100"
        >
          <X className="w-5 h-5" />
        </button>

        {/* Modal Header */}
        <div className="mb-4">
          <span className="text-[10px] font-bold text-[#0B5D3B] uppercase tracking-wider bg-emerald-50 px-2.5 py-0.5 rounded-full border border-emerald-200">
            Bulk Operations Center
          </span>
          <h3 className="font-serif font-bold text-lg text-gray-900 mt-1 flex items-center gap-1.5">
            <Sparkles className="w-5 h-5 text-[#D4A72C]" />
            <span>AI Player Import & Auto-Scheduler</span>
          </h3>
          <p className="text-xs text-gray-500">
            Upload CSV/Spreadsheets or copy-paste player list texts to register competitors and generate fixtures instantly.
          </p>
        </div>

        {/* Error/Success Feed */}
        {errorMsg && (
          <div className="mb-4 p-3 bg-red-50 border border-red-200 text-red-800 text-xs rounded-xl flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-red-600 shrink-0" />
            <span>{errorMsg}</span>
          </div>
        )}
        {successMsg && (
          <div className="mb-4 p-3.5 bg-emerald-50 border border-emerald-200 text-emerald-800 text-xs rounded-xl flex items-center gap-2">
            <Check className="w-4 h-4 text-emerald-600 shrink-0" />
            <span>{successMsg}</span>
          </div>
        )}

        {/* Modal Steps content */}
        <div className="flex-1 overflow-y-auto mb-4 min-h-[250px]">
          {loading ? (
            <div className="flex flex-col items-center justify-center py-16 space-y-3">
              <div className="w-10 h-10 border-4 border-[#0B5D3B] border-t-transparent rounded-full animate-spin" />
              <p className="text-xs font-bold text-gray-700">
                {step === 'input' ? 'AI Analyzing & Extracting Player Details...' : 'Registering entries & generating schedules...'}
              </p>
              <p className="text-[10px] text-gray-400">Please do not close this modal</p>
            </div>
          ) : step === 'input' ? (
            /* Input & File upload step */
            <div className="space-y-4 text-xs">
              
              {/* Drag and Drop Zone */}
              <div 
                onClick={() => fileInputRef.current?.click()}
                className="border-2 border-dashed border-gray-200 hover:border-[#0B5D3B] rounded-2xl p-6 text-center cursor-pointer transition-all bg-gray-50/50 hover:bg-emerald-50/10 space-y-2"
              >
                <Upload className="w-8 h-8 text-gray-400 mx-auto" />
                <div>
                  <span className="font-bold text-gray-800">Click to upload spreadsheet file</span>
                  <p className="text-[10px] text-gray-400 mt-0.5">Supports CSV, Text lists, Excel exports (.csv, .txt)</p>
                </div>
                {fileName && (
                  <div className="inline-flex items-center gap-1.5 bg-[#0B5D3B]/10 text-[#0B5D3B] px-3 py-1 rounded-full font-semibold">
                    <FileText className="w-3.5 h-3.5" />
                    <span>{fileName}</span>
                  </div>
                )}
                <input
                  type="file"
                  ref={fileInputRef}
                  onChange={handleFileUpload}
                  accept=".csv,.txt"
                  className="hidden"
                />
              </div>

              {/* Text Area copy paste */}
              <div>
                <label className="block font-bold text-gray-700 mb-1">
                  Or Paste Player List (Unstructured / Tabular / Comma Separated)
                </label>
                <textarea
                  value={rawText}
                  onChange={e => setRawText(e.target.value)}
                  rows={8}
                  placeholder={`Example list format:
1. Ramesh Kumar (Deccan Gymkhana), rating 1800, seed 1
2. Sunil Patil, Hadapsar club, 1650 rating
3. Priya Nair, Model Colony, seed 3`}
                  className="w-full p-3 border border-gray-200 rounded-xl focus:ring-2 focus:ring-[#0B5D3B] font-mono text-[11px]"
                />
              </div>

            </div>
          ) : (
            /* Preview Step */
            <div className="space-y-3">
              <div className="flex items-center justify-between text-xs pb-1 border-b border-gray-100">
                <span className="font-bold text-gray-700">Preview Parsed Player Entries ({parsedPlayers.length})</span>
                <button
                  onClick={toggleSelectAll}
                  className="text-[#0B5D3B] font-bold hover:underline"
                >
                  {parsedPlayers.every(p => p.selected) ? 'Deselect All' : 'Select All'}
                </button>
              </div>

              <div className="overflow-x-auto rounded-xl border border-gray-200">
                <table className="w-full text-left text-xs">
                  <thead className="bg-gray-50 text-gray-600 font-semibold border-b border-gray-200">
                    <tr>
                      <th className="px-3 py-2.5 w-8">Import</th>
                      <th className="px-3 py-2.5">Player Name</th>
                      <th className="px-3 py-2.5">Club</th>
                      <th className="px-3 py-2.5">City</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {parsedPlayers.map((player, idx) => (
                      <tr key={idx} className={`hover:bg-gray-50/50 ${!player.selected ? 'opacity-50' : ''}`}>
                        <td className="px-3 py-2">
                          <input
                            type="checkbox"
                            checked={player.selected}
                            onChange={() => handleFieldChange(idx, 'selected', !player.selected)}
                            className="rounded text-[#0B5D3B] focus:ring-[#0B5D3B] w-4 h-4 cursor-pointer"
                          />
                        </td>
                        <td className="px-3 py-2">
                          <input
                            type="text"
                            value={player.name}
                            onChange={(e) => handleFieldChange(idx, 'name', e.target.value)}
                            className="w-full p-1 border border-transparent hover:border-gray-200 focus:border-[#0B5D3B] focus:ring-1 rounded bg-transparent font-bold text-gray-800"
                          />
                          {/* A doubles row registers two people as a team, so both
                              must be visible before the admin confirms. */}
                          {player.type === 'doubles' && (
                            <div className="mt-1 flex items-center gap-1.5 flex-wrap">
                              <span className="px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider rounded bg-blue-100 text-blue-700">
                                Doubles
                              </span>
                              <input
                                type="text"
                                value={player.partnerName || ''}
                                onChange={(e) => handleFieldChange(idx, 'partnerName', e.target.value)}
                                placeholder="Partner name"
                                className="flex-1 min-w-[7rem] p-1 text-[11px] border border-transparent hover:border-gray-200 focus:border-[#0B5D3B] focus:ring-1 rounded bg-transparent text-gray-700"
                              />
                              {player.teamName && (
                                <span className="text-[10px] text-gray-500 italic truncate max-w-[9rem]">
                                  {player.teamName}
                                </span>
                              )}
                            </div>
                          )}
                        </td>
                        <td className="px-3 py-2">
                          <input
                            type="text"
                            value={player.club}
                            onChange={(e) => handleFieldChange(idx, 'club', e.target.value)}
                            className="w-full p-1 border border-transparent hover:border-gray-200 focus:border-[#0B5D3B] focus:ring-1 rounded bg-transparent text-gray-600"
                          />
                        </td>
                        <td className="px-3 py-2">
                          <input
                            type="text"
                            value={player.city}
                            onChange={(e) => handleFieldChange(idx, 'city', e.target.value)}
                            className="w-full p-1 border border-transparent hover:border-gray-200 focus:border-[#0B5D3B] focus:ring-1 rounded bg-transparent text-gray-600"
                          />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

            </div>
          )}
        </div>

        {/* Modal Footer */}
        <div className="flex items-center justify-between border-t border-gray-100 pt-4">
          <button
            type="button"
            onClick={step === 'preview' ? () => setStep('input') : onClose}
            disabled={loading}
            className="px-4 py-2 text-xs font-bold text-gray-600 hover:bg-gray-100 rounded-xl"
          >
            {step === 'preview' ? 'Back' : 'Cancel'}
          </button>

          {step === 'input' ? (
            <button
              onClick={handleAnalyze}
              disabled={loading}
              className="px-5 py-2.5 bg-[#0B5D3B] hover:bg-[#08472d] text-white text-xs font-bold rounded-xl shadow-md flex items-center gap-1.5"
            >
              <Sparkles className="w-4 h-4 text-[#D4A72C]" />
              <span>Analyze & Preview List</span>
            </button>
          ) : (
            <div className="flex items-center gap-3">
              <label className="flex items-center gap-1.5 text-xs font-semibold text-gray-700 cursor-pointer">
                <input
                  type="checkbox"
                  checked={autoSchedule}
                  onChange={e => setAutoSchedule(e.target.checked)}
                  disabled={loading}
                  className="accent-[#0B5D3B]"
                />
                <span>Draw and publish the schedule too</span>
              </label>
              <button
                onClick={handleImportAndSchedule}
                disabled={loading}
                className="px-5 py-2.5 bg-[#D4A72C] hover:bg-[#c29623] text-[#0B5D3B] text-xs font-black rounded-xl shadow-md flex items-center gap-1.5"
              >
                <Calendar className="w-4 h-4 text-[#0B5D3B]" />
                <span>{autoSchedule ? 'Confirm Import & Auto-Schedule' : 'Confirm Import'}</span>
              </button>
            </div>
          )}
        </div>

      </div>
    </div>
  );
};
