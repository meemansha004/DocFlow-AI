import React, { useState, useRef, useEffect } from 'react';
import { MoreVertical } from 'lucide-react';

// Small 3-dot dropdown menu. `items`: [{ label, icon, onClick, danger }].
const KebabMenu = ({ items = [], label = 'Open menu', align = 'right', buttonClassName = '' }) => {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const onClick = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={(e) => { e.stopPropagation(); setOpen((v) => !v); }}
        className={`p-1 rounded-md text-gray-400 hover:text-gray-100 hover:bg-surface-hover transition-colors ${buttonClassName}`}
      >
        <MoreVertical size={16} />
      </button>
      {open && (
        <div
          role="menu"
          className={`absolute ${align === 'right' ? 'right-0' : 'left-0'} top-full mt-1 z-[80] min-w-44 rounded-md border border-border bg-surface shadow-xl py-1`}
        >
          {items.map((item) => (
            <button
              key={item.label}
              type="button"
              role="menuitem"
              onClick={(e) => { e.stopPropagation(); setOpen(false); item.onClick(); }}
              className={`w-full flex items-center gap-2 px-3 py-2 text-left text-sm transition-colors hover:bg-surface-hover
                ${item.danger ? 'text-red-400 hover:text-red-300' : 'text-gray-300 hover:text-gray-100'}`}
            >
              {item.icon && <item.icon size={14} />}
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
};

export default KebabMenu;
