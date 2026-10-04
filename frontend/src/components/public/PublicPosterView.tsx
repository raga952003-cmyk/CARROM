import React, { useEffect, useState } from 'react';
import { isRegistrationDeadlinePassed } from '../../utils/registrationDeadline';
import { Tournament } from '../../types/tournament';
import { tournamentService } from '../../services/tournamentService';
import { TournamentPoster } from '../common/TournamentPoster';
import { usePosterQr } from '../common/usePosterQr';

export const PublicPosterView: React.FC<{ tournamentId: string }> = ({ tournamentId }) => {
  const [tournament, setTournament] = useState<Tournament | null>(null);
  const [error, setError] = useState('');
  const { qr, isLocal } = usePosterQr(tournamentId, tournament?.posterConfig?.publicBaseUrl);
  useEffect(() => { let active = true; setTournament(null); setError(''); tournamentService.getTournamentById(tournamentId).then(t => { if (active) setTournament(t); }).catch(() => { if (active) setError('This tournament is unavailable or still a draft.'); }); return () => { active = false; }; }, [tournamentId]);
  return <main className="min-h-screen bg-gray-100 p-4"><div className="max-w-lg mx-auto">{error ? <p role="alert">{error}</p> : !tournament ? <p>Loading tournament…</p> : <><TournamentPoster tournament={tournament} qr={qr} qrIsLocal={isLocal} /><div className="bg-white p-4 mt-3 rounded"><p>Status: {tournament.status}</p>{tournament.status === 'registration_open' && !isRegistrationDeadlinePassed(tournament.registrationEndDate) ? <a className="block bg-green-800 text-white rounded p-3 mt-2 text-center" href={`#/join/${encodeURIComponent(tournamentId)}`}>Sign in to register</a> : <p>Registration is currently closed.</p>}<a className="block underline mt-3" href={`#/live/${encodeURIComponent(tournamentId)}`}>View fixtures and results</a></div></>}</div></main>;
};
