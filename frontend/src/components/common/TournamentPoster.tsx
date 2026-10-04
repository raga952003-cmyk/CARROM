import React, { useEffect, useRef, useState } from 'react';
import { PosterConfig, Tournament } from '../../types/tournament';
import { posterDate, posterDefaults, scoringSummary } from '../../utils/posterFacts';

const themes = { emerald_gold: ['#08472d', '#d4a72c'], royal_ebony: ['#181a1b', '#eac264'], heritage_wood: ['#422915', '#ebc98a'], championship_blue: ['#0f345a', '#8cd5ff'] };

export const TournamentPoster: React.FC<{ tournament: Tournament; config?: PosterConfig; qr?: string; qrIsLocal?: boolean }> = ({ tournament: t, config, qr, qrIsLocal = false }) => {
  const frame = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(600);
  useEffect(() => { if (!frame.current) return; const observer = new ResizeObserver(entries => setWidth(entries[0].contentRect.width)); observer.observe(frame.current); return () => observer.disconnect(); }, []);
  const p = config || posterDefaults(t);
  const [background, accent] = themes[p.themeStyle] || themes.emerald_gold;
  const square = p.posterSize === 'square';
  const cell = (label: string, value: string) => <div><div style={{ opacity: .8, fontSize: 10 }}>{label}</div><strong style={{ fontSize: 12, overflowWrap: 'anywhere' }}>{value}</strong></div>;
  return <div ref={frame} style={{ width: '100%', aspectRatio: square ? '1' : p.posterSize === 'a4' ? '210 / 297' : '4 / 5' }}><div data-testid="tournament-poster" style={{ width: 600, height: square ? 600 : p.posterSize === 'a4' ? 600 * 297 / 210 : 750, zoom: width / 600, aspectRatio: square ? '1' : p.posterSize === 'a4' ? '210 / 297' : '4 / 5', boxSizing: 'border-box', padding: square ? 16 : 24, background, color: 'white', border: `4px solid ${accent}`, display: 'flex', flexDirection: 'column', justifyContent: 'space-between', gap: square ? 6 : 12, fontFamily: 'Arial, sans-serif', overflowWrap: 'anywhere' }}>
    <header style={{ textAlign: 'center' }}>
      <span style={{ background: accent, color: '#17231b', fontSize: 10, fontWeight: 800, padding: '5px 10px', borderRadius: 20, display: 'inline-block' }}>{p.badgeText}</span>
      <h2 style={{ fontSize: square ? 21 : 25, lineHeight: 1.12, margin: '10px 0 6px', fontFamily: 'Georgia, serif' }}>{t.name}</h2>
      {p.tagline && <p style={{ fontSize: 12, margin: 0, color: accent }}>{p.tagline}</p>}
    </header>
    <div style={{ background: '#00000030', padding: 12, borderRadius: 10, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
      {cell('Tournament dates', `${posterDate(t.tournamentStartDate)} – ${posterDate(t.tournamentEndDate)}`)}
      {cell('Entry fee', t.entryFee ? `₹${t.entryFee.toLocaleString('en-IN')} / entry` : 'Free entry')}
      <div style={{ gridColumn: '1 / -1' }}>{cell('Venue', `${t.venue}, ${t.city}`)}</div>
      {cell('Prize pool', t.prizePool || 'Not announced')}
      {cell('Registration closes', posterDate(t.registrationEndDate))}
    </div>
    <div style={{ fontSize: 11, lineHeight: 1.4 }}>
      <strong style={{ color: accent }}>{scoringSummary(t)}</strong>
      <div>{t.category === 'both' ? 'Singles & doubles' : t.category === 'singles' ? 'Singles' : 'Doubles'} · {t.format.replaceAll('_', ' ')}</div>
      {p.highlights.filter(Boolean).slice(0, 3).map((h, i) => <div key={i}>• {h}</div>)}
      {p.eligibility && <div><strong>Eligibility:</strong> {p.eligibility}</div>}
      {p.announcement && <p style={{ margin: '5px 0 0' }}>{p.announcement}</p>}
      {p.organizerContact && <div><strong>Contact:</strong> {p.organizerContact}</div>}
      {p.sponsorText && <div><strong>Supported by:</strong> {p.sponsorText}</div>}
    </div>
    <footer style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, borderTop: '1px solid #ffffff55', paddingTop: 8 }}>
      <div style={{ fontSize: 11 }}><strong style={{ color: accent }}>{qrIsLocal ? 'LOCAL TEST - THIS COMPUTER ONLY' : t.status === 'draft' ? 'DRAFT PREVIEW' : t.status === 'registration_open' ? 'SCAN FOR DETAILS & REGISTRATION' : 'SCAN FOR TOURNAMENT DETAILS'}</strong><div>Registration: {posterDate(t.registrationStartDate)} – {posterDate(t.registrationEndDate)}</div><div>Payment instructions are available in the app.</div></div>
      {qr && <img src={qr} alt="Tournament details QR code" width={132} height={132} style={{ background: 'white', flexShrink: 0, imageRendering: 'pixelated', borderRadius: 0 }} />}
    </footer>
  </div></div>;
};
