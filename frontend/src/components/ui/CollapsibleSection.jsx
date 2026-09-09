import React, { useState } from 'react';
import { ChevronDown } from 'lucide-react';
import Badge from './Badge';

const CollapsibleSection = ({
  title,
  count,
  badgeVariant = 'neutral',
  defaultExpanded = false,
  children,
  className = ''
}) => {
  const [isExpanded, setIsExpanded] = useState(defaultExpanded);

  return (
    <div className={`border border-border rounded-xl overflow-hidden bg-surface ${className}`}>
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full flex items-center justify-between p-4 bg-surface hover:bg-surface-hover transition-colors"
      >
        <div className="flex items-center gap-3">
          <ChevronDown 
            size={18} 
            className={`text-gray-400 transition-transform duration-200 ${isExpanded ? 'rotate-180' : ''}`} 
          />
          <h3 className="font-medium text-gray-100">{title}</h3>
          {count !== undefined && (
            <Badge variant={badgeVariant}>{count}</Badge>
          )}
        </div>
      </button>
      
      <div 
        className={`grid transition-all duration-200 ease-in-out ${isExpanded ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0'}`}
      >
        <div className="overflow-hidden">
          <div className="p-4 border-t border-border/50 bg-background/30">
            {children}
          </div>
        </div>
      </div>
    </div>
  );
};

export default CollapsibleSection;
