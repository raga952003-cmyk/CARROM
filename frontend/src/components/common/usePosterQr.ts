import { useEffect, useState } from 'react';
import { isLocalPosterUrl, publicPosterUrl } from '../../utils/posterFacts';

export function usePosterQr(id: string, publicBaseUrl?: string) {
  const [result, setResult] = useState({ qr: '', error: '', url: '', isLocal: false });
  useEffect(() => {
    let active = true;
    setResult({ qr: '', error: '', url: '', isLocal: false });
    if (!id) return;
    let url: string;
    try { url = publicPosterUrl(id, publicBaseUrl); }
    catch (e) { setResult({ qr: '', error: e instanceof Error ? e.message : 'Enter a valid public website address.', url: '', isLocal: false }); return; }
    import('qrcode').then(module => module.default.toDataURL(url, { margin: 4, scale: 10, errorCorrectionLevel: 'M', color: { dark: '#000000ff', light: '#ffffffff' } }))
      .then(qr => { if (active) setResult({ qr, error: '', url, isLocal: isLocalPosterUrl(url) }); })
      .catch(() => { if (active) setResult({ qr: '', error: 'Could not generate the QR code. Please reopen the poster.', url, isLocal: false }); });
    return () => { active = false; };
  }, [id, publicBaseUrl]);
  return result;
}
