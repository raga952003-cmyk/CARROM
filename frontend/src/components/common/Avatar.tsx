import React from 'react';

/**
 * A person, shown as their picture or as their initials.
 *
 * Used everywhere a name appears beside a face — the header, the settings
 * panel, and the match card where two players are put against each other. The
 * initials fallback is not a placeholder to be replaced later: most people
 * never upload a picture, so it is the common case and has to look deliberate.
 */

interface AvatarProps {
  name?: string | null;
  src?: string | null;
  /** Pixel size. Text scales with it so initials stay centred at any size. */
  size?: number;
  className?: string;
  ring?: boolean;
}

/** Up to two initials: "Ragavendra S" gives RS, "Srinivas" gives S. */
function initialsOf(name?: string | null): string {
  const parts = (name || '').trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return 'U';
  if (parts.length === 1) return parts[0].charAt(0).toUpperCase();
  return (parts[0].charAt(0) + parts[parts.length - 1].charAt(0)).toUpperCase();
}

export const Avatar: React.FC<AvatarProps> = ({
  name, src, size = 32, className = '', ring = false,
}) => {
  const style = { width: size, height: size, fontSize: Math.max(10, Math.round(size * 0.4)) };
  const base = `rounded-full shrink-0 object-cover ${ring ? 'ring-2 ring-[#D4A72C]' : ''} ${className}`;

  if (src) {
    return (
      <img
        src={src}
        alt={name || 'Player'}
        style={style}
        className={base}
        // A picture that fails to load must not leave a broken-image icon in
        // the middle of the scoring screen; drop it and let the space close up.
        onError={e => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }}
      />
    );
  }

  return (
    <div
      style={style}
      aria-label={name || 'Player'}
      className={`${base} bg-[#D4A72C] text-[#202522] flex items-center justify-center font-bold`}
    >
      {initialsOf(name)}
    </div>
  );
};
