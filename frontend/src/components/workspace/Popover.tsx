import { useEffect, useRef, type ReactNode } from 'react';

export function Popover({
  className,
  group,
  summary,
  children,
}: {
  className?: string;
  group?: string;
  summary: ReactNode;
  children: ReactNode;
}) {
  const root = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const closeOutside = (event: PointerEvent) => {
      if (
        event.target instanceof Node &&
        root.current?.open &&
        !root.current.contains(event.target)
      )
        root.current.open = false;
    };
    document.addEventListener('pointerdown', closeOutside);
    return () => document.removeEventListener('pointerdown', closeOutside);
  }, []);
  return (
    <details
      ref={root}
      name={group}
      className={`workspace-popover ${className ?? ''}`}
      onKeyDown={(event) => {
        if (event.key === 'Escape') {
          event.preventDefault();
          event.stopPropagation();
          event.currentTarget.open = false;
          event.currentTarget.querySelector('summary')?.focus();
        }
      }}
    >
      <summary>{summary}</summary>
      <div className="popover-content">{children}</div>
    </details>
  );
}
