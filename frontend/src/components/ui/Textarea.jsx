import React, { forwardRef } from 'react';

const Textarea = forwardRef(({
  label,
  error,
  required,
  className = '',
  ...props
}, ref) => {
  return (
    <div className="w-full flex flex-col gap-1.5">
      {label && (
        <label className="text-sm font-medium text-gray-300">
          {label} {required && <span className="text-red-400">*</span>}
        </label>
      )}
      <textarea
        ref={ref}
        className={`flex min-h-[80px] w-full rounded-md border bg-background px-3 py-2 text-sm text-gray-100 placeholder:text-gray-500 focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary disabled:cursor-not-allowed disabled:opacity-50 transition-colors
          ${error ? 'border-red-500 focus:ring-red-500/50 focus:border-red-500' : 'border-border'}
          ${className}
        `}
        {...props}
      />
      {error && (
        <p className="text-sm text-red-400 mt-1">{error}</p>
      )}
    </div>
  );
});
Textarea.displayName = 'Textarea';

export default Textarea;
