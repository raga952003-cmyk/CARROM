import React, { useRef, useState } from 'react';
import { X } from 'lucide-react';
import { Tournament, PosterConfig } from '../../types/tournament';
import { useTournament } from '../../context/TournamentContext';
import { tournamentService } from '../../services/tournamentService';
import { TournamentPoster } from '../common/TournamentPoster';
import { usePosterQr } from '../common/usePosterQr';
import { posterDefaults, posterFingerprint, DEFAULT_PUBLIC_SITE_URL, validatePosterBaseUrl } from '../../utils/posterFacts';

export const PosterGeneratorModal: React.FC<{ tournament: Tournament; isOpen: boolean; onClose: () => void }> = ({ tournament: initialTournament, isOpen, onClose }) => {
  const { updateTournament, tournaments } = useTournament();
  const tournament = tournaments.find(t => t.id === initialTournament.id) || initialTournament;
  const [config, setConfig] = useState<PosterConfig>(() => posterDefaults(tournament));
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [exportUrl, setExportUrl] = useState('');
  const preview = useRef<HTMLDivElement>(null);
  const { qr, error: qrError, url: qrUrl, isLocal: qrIsLocal } = usePosterQr(tournament.id, config.publicBaseUrl);
  const change = (key: keyof PosterConfig, value: any) => setConfig(p => ({ ...p, [key]: value }));
  const run = async (name: string, action: () => Promise<void>) => {
    if (busy) return;
    setBusy(name); setError(''); setNotice('');
    try { await action(); } catch (e) { setError(e instanceof Error ? e.message : 'Unable to complete this action.'); } finally { setBusy(''); }
  };
  if (!isOpen) return null;
  const save = () => run('publish', async () => {
    const publicBaseUrl = validatePosterBaseUrl(qrUrl.split('#')[0]);
    if (!config.badgeText.trim()) throw new Error('Enter a poster badge.');
    await document.fonts.ready;
    const content = preview.current?.querySelector('[data-testid="tournament-poster"]') as HTMLElement;
    if (content && content.scrollHeight > content.clientHeight + 4) throw new Error('Text exceeds this format. Shorten the copy or choose A4.');
    await updateTournament(tournament.id, { posterConfig: { ...config, publicBaseUrl, sourceFingerprint: posterFingerprint(tournament) } });
    setNotice('Poster saved. Players can now see this layout.');
  });
  const download = () => run('download', async () => {
    setExportUrl('');
    const node = preview.current?.firstElementChild as HTMLElement;
    if (!node || !qr) throw new Error('Wait for the QR code to finish loading.');
    await document.fonts.ready;
    await Promise.all(Array.from(node.querySelectorAll('img')).map(img => img.decode()));
    const content = node.querySelector('[data-testid="tournament-poster"]') as HTMLElement;
    if (content && content.scrollHeight > content.clientHeight + 4) throw new Error('Text exceeds this format. Shorten the copy or choose A4.');
    const { toPng } = await import('html-to-image');
    const dimensions = config.posterSize === 'square' ? [1080, 1080] : config.posterSize === 'a4' ? [2480, 3508] : [1080, 1350];
    const url = await toPng(node, { canvasWidth: dimensions[0], canvasHeight: dimensions[1], pixelRatio: 1, skipFonts: true });
    const link = document.createElement('a'); link.href = url;
    link.download = `${tournament.name.replace(/[^a-zA-Z0-9_-]/g, '_').slice(0, 80)}-${config.posterSize}.png`;
    setExportUrl(url); link.click(); setNotice('PNG downloaded. Review it before sharing.');
  });
  return <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-3"><div className="bg-white rounded-2xl max-w-5xl w-full max-h-[95vh] overflow-auto p-5">
    <div className="flex justify-between"><h2 className="text-xl font-bold">Tournament poster</h2><button aria-label="Close poster" onClick={onClose} disabled={!!busy}><X /></button></div>
    <div className="grid md:grid-cols-2 gap-6 mt-4">
      <fieldset disabled={!!busy} className="space-y-3">
        <label className="block">Public website for QR<input aria-label="Public website for QR" type="url" maxLength={300} className="block border rounded p-2 w-full" value={config.publicBaseUrl || ''} placeholder={DEFAULT_PUBLIC_SITE_URL} onChange={e => change('publicBaseUrl', e.target.value)} /><span className="text-xs">Players scan this link to view this tournament and register.</span></label>
        <label className="block">Format<select className="block border rounded p-2 w-full" value={config.posterSize} onChange={e => change('posterSize', e.target.value)}><option value="portrait">Portrait · 1080 × 1350</option><option value="square">Square · 1080 × 1080</option><option value="a4">A4 · 2480 × 3508</option></select></label>
        <label className="block">Theme<select className="block border rounded p-2 w-full" value={config.themeStyle} onChange={e => change('themeStyle', e.target.value)}>{['emerald_gold', 'royal_ebony', 'heritage_wood', 'championship_blue'].map(t => <option key={t} value={t}>{t.replaceAll('_', ' ')}</option>)}</select></label>
        {([['badgeText', 'Badge', 60], ['tagline', 'Tagline', 100], ['announcement', 'Announcement', 180], ['organizerContact', 'Public organiser contact', 100], ['eligibility', 'Eligibility', 100], ['sponsorText', 'Sponsors (confirmed only)', 100]] as const).map(([key, label, length]) => <label className="block" key={key}>{label}<input className="block border rounded p-2 w-full" maxLength={length} value={config[key] || ''} onChange={e => change(key, e.target.value)} /></label>)}
        <label className="block">Highlights (one per line, up to three)<textarea className="block border rounded p-2 w-full" maxLength={240} value={config.highlights.join('\n')} onChange={e => change('highlights', e.target.value.split('\n').slice(0, 3).map(s => s.slice(0, 80)))} /></label>
        <button className="border rounded p-2" onClick={() => run('copy', async () => { const result = await tournamentService.generatePosterCopy({ tournamentName: tournament.name, venue: tournament.venue, city: tournament.city, category: tournament.category, format: tournament.format }); if (!result?.tagline) throw new Error('Copy generation unavailable. You can edit the fields manually.'); setConfig(p => ({ ...p, tagline: String(result.tagline).slice(0, 100), announcement: String(result.announcement || '').slice(0, 180) })); setNotice('Review the suggested copy before publishing.'); })}>Suggest promotional copy</button>
      </fieldset>
      <div><div ref={preview} style={{ maxWidth: 440 }}><TournamentPoster tournament={tournament} config={config} qr={qr} qrIsLocal={qrIsLocal} /></div>{qrUrl && <><a className="text-sm underline" href={qrUrl} target="_blank" rel="noopener noreferrer">Open QR destination</a><p className="text-xs break-all mt-2">{qrUrl}</p></>}
        {tournament.status === 'draft' && <p className="text-sm mt-2">Draft: the public link becomes available when registration opens.</p>}
        {qrIsLocal && <p className="text-sm mt-2">This QR uses your local address. Download from the deployed website before sharing with players.</p>}
        {tournament.posterConfig?.sourceFingerprint && tournament.posterConfig.sourceFingerprint !== posterFingerprint(tournament) && <p className="text-amber-700">Tournament details changed. Publish and download an updated poster.</p>}
        {tournament.posterConfig?.publishedAt && <p className="text-sm">Last saved: {new Date(tournament.posterConfig.publishedAt).toLocaleString()}</p>}
      </div>
    </div>
    {(error || qrError) && <p role="alert" className="text-red-700 mt-3">{error || qrError}</p>}{notice && <p role="status" className="text-green-800 mt-3">{notice}</p>}
    {exportUrl && <a href={exportUrl} target="_blank" rel="noopener noreferrer" className="underline block mt-2">Open exported PNG</a>}
    <div className="flex justify-end gap-3 mt-4"><button className="border rounded p-2" disabled={!!busy} onClick={onClose}>Close</button><button className="border rounded p-2" disabled={!!busy || !qr} onClick={download}>{busy === 'download' ? 'Preparing PNG…' : 'Download PNG'}</button><button className="bg-green-800 text-white rounded p-2" disabled={!!busy || !qr} onClick={save}>{busy === 'publish' ? 'Saving…' : 'Publish poster'}</button></div>
  </div></div>;
};
