import { useEffect, useState } from 'react';
import { publicPosterUrl } from '../../utils/posterFacts';

export function usePosterQr(id: string) {
  const [qr, setQr] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    setQr(''); setError('');
    if (!id) return;
    import('qrcode').then(module => module.default.toDataURL(publicPosterUrl(id), { margin: 4, width: 256, errorCorrectionLevel: 'M' }))
      .then(value => { if (active) setQr(value); })
      .catch(() => { if (active) setError('Could not generate the QR code. Please reopen the poster.'); });
    return () => { active = false; };
  }, [id]);
  return { qr, error };
}
